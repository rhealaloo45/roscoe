# roscoe — Developer Guide

> **R**eady-to-run **O**rchestration **S**DK — **C**onfigurable, **O**bservable, **E**xtensible

Everything you need to build, run, and operate your agent. Copy-paste the snippets
directly — they all work with the scaffolded project structure.

---

## Table of contents

1. [Project structure](#project-structure)
2. [Available templates](#available-templates)
3. [Writing tools](#writing-tools)
4. [Running the agent](#running-the-agent)
5. [Multi-turn conversations](#multi-turn-conversations)
6. [Swapping LLM providers](#swapping-llm-providers)
7. [Memory](#memory)
8. [Connectors](#connectors)
9. [Human-in-the-loop (HITL)](#human-in-the-loop-hitl)
10. [Audit log & cost tracking](#audit-log--cost-tracking)
11. [Monitoring dashboard](#monitoring-dashboard)
12. [Alerts & exporters](#alerts--exporters)
13. [Evals](#evals)
14. [Extending the cost table](#extending-the-cost-table)
15. [Configuration reference](#configuration-reference)
16. [Async usage](#async-usage)
17. [Troubleshooting](#troubleshooting)

---

## Project structure

```
__PROJECT_NAME__/
├── agent_config.yaml       # all config — provider, middleware, memory
├── main.py                 # entry point (run: python main.py)
├── tools/
│   └── my_tools.py         # your @tool functions + TOOLS list
├── prompts/
│   └── system.txt          # agent personality and instructions
├── evals/
│   └── test_cases.json     # eval test cases
├── .env.example            # credential placeholders (copy to .env)
└── docs.md                 # this file
```

---

## Available templates

This project was scaffolded from one of roscoe's six built-in templates (or from
scratch, if you didn't pass `--template`). Each one ships with tools, a system
prompt, `agent_config.yaml`, and approval gates already wired up — a starting
point you can rename and extend, not a locked-in structure.

```bash
roscoe init my-hr-bot --template hr_agent
roscoe init my-it-bot --template it_support_agent
roscoe init my-legal --template legal_agent
roscoe init my-kb --template knowledge_base_agent
roscoe init my-ea --template exec_assistant_agent
roscoe init my-gws --template google_workspace_agent
```

| Template | Use case | Connector | Approval gate |
|---|---|---|---|
| `hr_agent` | Leave, payslips, personal details | REST API | `submit_leave_request` |
| `it_support_agent` | Tickets, escalation, KB search | ServiceNow | `escalate_ticket` |
| `legal_agent` | Contract search, clause extraction, risk flags | Knowledge (RAG) | — |
| `knowledge_base_agent` | Q&A over Notion / SharePoint / docs | Notion + Knowledge | — (read-only) |
| `exec_assistant_agent` | Email, calendar, availability | Outlook (MS Graph) | `send_email`, `create_event` |
| `google_workspace_agent` | Gmail, Calendar, Tasks, Drive | Google Workspace (service account or OAuth2) | `send_email`, `create_event`, `create_task` |

Mixing templates is fine too — copy a tool or connector wiring from another
template's `tools/my_tools.py` into this project instead of starting a new one.

---

## Writing tools

A tool is a plain Python function with type hints and a docstring:

```python
from roscoe.tools import tool


@tool
def get_weather(city: str) -> dict:
    """Get the current weather for a city. Use when the user asks about weather."""
    # Replace with a real API call
    return {"city": city, "weather": "22°C, clear"}


@tool
def search_docs(query: str) -> list[dict]:
    """Search internal documents. Use when the user asks about company policies."""
    return [{"title": "Remote Work Policy", "snippet": "Up to 3 days per week."}]
```

**Rules:**
- The **docstring** tells the LLM *when* to use the tool — make it specific.
- **Type hints** generate the JSON schema automatically — no manual schema needed.
- **Return dicts or primitives** — the LLM reads the return value.
- Don't catch exceptions inside tools — let them bubble up so retry middleware handles it.

**Register your tools** — add them to `TOOLS` in `tools/my_tools.py`:

```python
TOOLS = [get_weather, search_docs]
```

---

## Running the agent

**From the CLI** — the fastest way to try the agent, no code needed:

```bash
roscoe run                    # browser chat (opens automatically)
roscoe run --terminal         # interactive chat in the terminal, streamed
roscoe run -m "your message"  # one-shot message, prints the reply, exits
```

If this project has its own web UI script (`app.py`, or whatever `ui_script:`
names in `agent_config.yaml`), `roscoe run` launches that instead of the
built-in browser widget — so a custom login page or dashboard just works with
no flags. `--terminal` and `-m` always talk to the agent directly and never
touch a custom UI script.

**From Python — single-shot:**

```python
from roscoe import AgentRunner
from tools.my_tools import TOOLS

agent = AgentRunner.from_config("agent_config.yaml", tools=TOOLS)
result = agent.run("What's the weather in London?")

print(result.output)        # the answer
print(result.status)        # "success" | "error" | "paused"
print(result.cost_usd)      # estimated cost (None if model not in cost table)
print(result.total_tokens)  # token usage
print(result.run_id)        # UUID — ties to the audit log
print(result.tool_calls)    # list of tool names called during the run
```

**Handle errors:**

```python
if result.status == "error":
    print(result.error)      # the error message
```

---

## Multi-turn conversations

Pass `user_id` and `session_id` to maintain context across turns:

```python
agent = AgentRunner.from_config("agent_config.yaml", tools=TOOLS)

# Same session — agent remembers previous messages
r1 = agent.run("My name is Rhea.", user_id="u1", session_id="s1")
r2 = agent.run("What's my name?", user_id="u1", session_id="s1")
print(r2.output)  # "Your name is Rhea."

# Different session — fresh context
r3 = agent.run("What's my name?", user_id="u1", session_id="s2")
print(r3.output)  # won't remember (unless persistent memory is on)
```

Requires `memory.conversation.enabled: true` in `agent_config.yaml`.

---

## Swapping LLM providers

Change **only** the `model:` block in `agent_config.yaml`. Your Python code stays
identical.

**OpenAI:**
```yaml
model:
  provider: openai
  model: gpt-4o-mini
  api_key: ${OPENAI_API_KEY}
  temperature: 0.1
```

**OpenRouter (100+ models, one key):**
```yaml
model:
  provider: openai
  model: meta-llama/llama-3.1-8b-instruct
  api_key: ${OPENROUTER_API_KEY}
  base_url: https://openrouter.ai/api/v1
```

**Anthropic:**
```yaml
model:
  provider: anthropic
  model: claude-sonnet-4-5
  api_key: ${ANTHROPIC_API_KEY}
```

**Google Gemini:**
```yaml
model:
  provider: gemini
  model: gemini-1.5-pro
  api_key: ${GOOGLE_API_KEY}
```

**NVIDIA NIM (free-tier models available):**
```yaml
model:
  provider: nvidia
  model: openai/gpt-oss-120b
  api_key: ${NVIDIA_API_KEY}
```

**Azure OpenAI:**
```yaml
model:
  provider: azure_openai
  deployment: my-gpt4o-deployment
  endpoint: ${AZURE_OPENAI_ENDPOINT}
  api_key: ${AZURE_OPENAI_KEY}
```

**Ollama (free, local):**
```yaml
model:
  provider: ollama
  model: llama3.1
  # no api_key needed — runs locally
```

---

## Memory

### Conversation memory (short-term)

Keeps the last N messages per session. Enabled by default.

```yaml
memory:
  conversation:
    enabled: true
    window_size: 10    # number of messages to keep
```

### Persistent memory (long-term)

Stores facts per `user_id` in sqlite. Survives across sessions.

```yaml
memory:
  persistent:
    enabled: true
    backend: sqlite
    connection: ./facts.db    # file created automatically
```

After enabling, the agent remembers facts like "My name is Rhea" even in new sessions
(as long as the same `user_id` is passed).

### Knowledge memory (RAG)

Vector retrieval for documents. Set up in code:

```python
from roscoe.memory.knowledge import KnowledgeMemory

# From text strings
km = KnowledgeMemory.from_texts(
    ["Remote work is allowed up to 3 days per week.", "Annual leave: 25 days."],
    metadatas=[{"source": "hr_policy.pdf"}, {"source": "hr_policy.pdf"}],
)

# Use as a tool
agent = AgentRunner.from_config("agent_config.yaml", tools=TOOLS + [km.as_tool()])
```

Uses FAISS if installed, otherwise falls back to a zero-dependency keyword retriever.

---

## Connectors

Pre-built tool bundles for enterprise systems. Pass `connector.tools` to `AgentRunner`:

```python
from roscoe.connectors import GitHubConnector

gh = GitHubConnector({"token": "ghp_your_token"})
agent = AgentRunner.from_config("agent_config.yaml", tools=gh.tools)
```

**Mix connector tools with your own:**

```python
from roscoe.connectors import JiraConnector
from tools.my_tools import TOOLS

jira = JiraConnector({
    "base_url": "https://yourorg.atlassian.net",
    "email": "you@company.com",
    "api_token": "your_jira_token",
})
agent = AgentRunner.from_config("agent_config.yaml", tools=TOOLS + jira.tools)
```

### Available connectors

| Connector | Import | Config keys |
|---|---|---|
| REST (any API) | `RESTConnector` | `base_url`, `auth`, `token` |
| Jira | `JiraConnector` | `base_url`, `email`, `api_token` |
| ServiceNow | `ServiceNowConnector` | `instance_url`, `username`, `password` |
| Outlook | `OutlookConnector` | `client_id`, `client_secret`, `tenant_id`, `mailbox` |
| SharePoint | `SharePointConnector` | `client_id`, `client_secret`, `tenant_id`, `site_id` |
| GitHub | `GitHubConnector` | `token` |
| Notion | `NotionConnector` | `token` |
| Google Workspace | `GoogleWorkspaceConnector` | `credentials_file`, `subject` (service account) — or `client_id`, `client_secret`, `refresh_token` (OAuth2, minted via `roscoe google-auth`) |
| Snowflake | `SnowflakeConnector` | `account`, `user`, `password`, `warehouse`, `database` |

All connectors accept an optional `transport` parameter for mocking in tests:

```python
import httpx
mock = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
gh = GitHubConnector({"token": "test"}, transport=mock)
```

---

## Human-in-the-loop (HITL)

### Setup

List tool names that need approval in `agent_config.yaml`:

```yaml
middleware:
  human_approval:
    require_approval_for: ["send_email", "delete_record", "submit_payment"]
```

### Usage

```python
result = agent.run("Send an email to bob@acme.com saying the report is ready")

if result.status == "paused":
    # The agent wants to call send_email but stopped before executing it
    print("Tool:", result.pending_action["tool"])
    print("Args:", result.pending_action["args"])

    # Option 1: Approve — run the tool as-is
    result = agent.resume(result.run_id, "approve")

    # Option 2: Reject — block the tool, agent gets a rejection message
    result = agent.resume(result.run_id, "reject")

    # Option 3: Modify — change the arguments before running
    result = agent.resume(result.run_id, "modify", payload={
        "to": "bob@acme.com",
        "subject": "Corrected subject",
        "body": "Updated body text",
    })

print(result.output)  # final answer after approval/rejection
```

### What happens internally

```
User message → model decides to call send_email(...)
  → gate check: "send_email" is in require_approval_for
  → STOP → return AgentResult(status="paused", run_id="abc-123")
  → you call agent.resume("abc-123", "approve")
  → send_email runs → result goes back to model → model finishes
  → AgentResult(status="success", output="Email sent.")
```

Paused runs are held in memory. In a real app, wire `resume()` to a Slack button,
web UI, or CLI prompt.

---

## Audit log & cost tracking

### Audit log

Every `agent.run()` writes a JSONL line to `logs/audit.jsonl` (when audit is enabled):

```bash
cat logs/audit.jsonl
```

Each line contains:

```json
{
  "run_id": "abc-123",
  "agent_name": "my-agent",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "total_tokens": 542,
  "cost_usd": 0.000163,
  "status": "success",
  "start_time": "2026-06-29T10:00:00+00:00",
  "end_time": "2026-06-29T10:00:01.234+00:00"
}
```

### Cost tracking

Cost shows up in `result.cost_usd` after every run. Built-in rates cover common models.
For newer models, extend the table before calling `agent.run()`:

```python
from roscoe.middleware.cost_tracker import COST_TABLE

COST_TABLE["openai"]["gpt-4.1"] = {"input": 0.002, "output": 0.008}
COST_TABLE["anthropic"]["claude-opus-4"] = {"input": 0.015, "output": 0.075}
```

Rates are per 1K tokens. Ollama is always $0.00. Unknown models return `None`.

---

## Monitoring dashboard

Aggregate your audit logs into a dashboard:

```bash
roscoe monitor --path logs/audit.jsonl
```

Output:

```
roscoe monitor
──────────────────────────────────────
  runs: 47
  cost (total): $1.23
  cost (today): $0.18

  latency (p50):  1.2s
  latency (p95):  3.4s
  latency (p99):  5.1s

  error rate: 2.1%
  total tokens: 24,831
──────────────────────────────────────
```

### In code

```python
from roscoe.monitoring.metrics import load_audit, aggregate
from roscoe.monitoring.dashboard import render

records = load_audit("logs/audit.jsonl")
metrics = aggregate(records)
print(render(metrics))
```

---

## Alerts & exporters

### Alerts

Set thresholds and get notified when they're exceeded:

```python
from roscoe.monitoring.metrics import load_audit, aggregate
from roscoe.monitoring.alerts import check_and_notify
from roscoe.monitoring.notifier import build_notifier

records = load_audit("logs/audit.jsonl")
metrics = aggregate(records)

# Console alerts (or use "slack" with a webhook_url)
notifier = build_notifier("console", {})

alert_config = {
    "daily_cost_usd": 10.0,       # alert if daily cost > $10
    "error_rate_pct": 5.0,        # alert if error rate > 5%
    "latency_p95_ms": 5000,       # alert if p95 latency > 5s
}
check_and_notify(metrics, alert_config, notifier)
```

**Slack alerts:**

```python
notifier = build_notifier("slack", {
    "webhook_url": "https://hooks.slack.com/services/T.../B.../xxx"
})
```

### Prometheus exporter

Push metrics to a Prometheus Pushgateway:

```python
from roscoe.monitoring.exporters.prometheus import PrometheusPushgatewayExporter

exporter = PrometheusPushgatewayExporter(gateway_url="http://localhost:9091")
exporter.push(metrics)
```

### Azure Monitor exporter

```python
# pip install "roscoe[azure]"
from roscoe.monitoring.exporters.azure_monitor import AzureMonitorExporter

exporter = AzureMonitorExporter(connection_string="InstrumentationKey=...")
exporter.push(metrics)
```

---

## Evals

### Define test cases

Edit `evals/test_cases.json`:

```json
{
  "cases": [
    {
      "id": "weather-london",
      "input": "What's the weather in London?",
      "expected_tools": ["get_weather"],
      "metadata": {"category": "tool-use"}
    },
    {
      "id": "greeting",
      "input": "Hello, who are you?",
      "expected_output": "A helpful assistant introduction.",
      "expected_tools": [],
      "metadata": {"category": "no-tool"}
    },
    {
      "id": "policy-check",
      "input": "What is our remote work policy?",
      "expected_tools": ["search_docs"],
      "expected_output": "Should mention 3 days per week.",
      "context_docs": ["Remote work is allowed up to 3 days per week."]
    }
  ]
}
```

**Fields:**
- `id` — unique case identifier
- `input` — user message to send to the agent
- `expected_tools` — tools the agent should call, in order (deterministic scoring)
- `expected_output` — description of correct output (LLM-as-judge scoring)
- `context_docs` — ground-truth docs for hallucination scoring
- `metadata` — arbitrary tags for filtering

### Run evals

```bash
# Tool-usage scoring only (deterministic, no LLM needed)
roscoe eval --dataset evals/test_cases.json --config agent_config.yaml

# Add LLM-as-judge scoring (output quality + hallucination)
roscoe eval --dataset evals/test_cases.json --config agent_config.yaml --judge
```

### Scorers

| Scorer | Type | What it checks |
|---|---|---|
| Tool usage | Deterministic | Did the agent call the right tools in the right order? |
| Output quality | LLM-as-judge | Is the output accurate, relevant, and well-written? (0–10) |
| Hallucination | LLM-as-judge | Does the output contain claims not in the context docs? |

### Compare runs (regression diffing)

```python
from roscoe.evals.regression import compare_runs

diff = compare_runs(report_a, report_b)
print(diff.improved)     # cases that got better
print(diff.regressed)    # cases that got worse
print(diff.deltas)       # per-scorer, per-case score differences
```

---

## Extending the cost table

roscoe ships with rates for common models. NVIDIA and Ollama default to $0.00
(free-tier), so `nvidia`/`openai/gpt-oss-120b` and similar have no cost until
you price them.

**From the CLI** (writes to `~/.roscoe/prices.json`, applied on every run):

```bash
roscoe prices              # desktop editor — add/edit rates per provider+model
roscoe prices --terminal   # print the effective price table instead
```

**From Python** (in-process only, before calling `agent.run()`):

```python
from roscoe.middleware.cost_tracker import COST_TABLE

# Add a model to an existing provider
COST_TABLE["openai"]["gpt-4.1"] = {"input": 0.002, "output": 0.008}

# Add an entirely new provider
COST_TABLE["my_provider"] = {
    "my-model": {"input": 0.001, "output": 0.004},
}
```

Rates are per 1K tokens. Cost shows up in `result.cost_usd` and the audit log.

---

## Configuration reference

Every option in `agent_config.yaml`:

```yaml
# --- Agent identity ---
agent_name: my-agent                          # used in audit logs and monitoring
system_prompt_file: prompts/system.txt        # path to system prompt file
# system_prompt: |                            # or inline
#   You are a helpful assistant.
# ui_script: app.py                           # custom web UI `roscoe run` launches instead
#                                              # of the built-in widget (defaults to app.py
#                                              # if that file exists, even without this key)

# --- LLM provider ---
model:
  provider: openai                            # openai | azure_openai | anthropic | gemini | nvidia | ollama
  model: gpt-4o-mini                          # model name
  api_key: ${OPENAI_API_KEY}                  # resolved from environment
  temperature: 0.1                            # 0.0–2.0
  # base_url: https://openrouter.ai/api/v1   # custom endpoint
  # max_tokens: 4096                          # cap response length
  # deployment: my-deployment                 # Azure only
  # endpoint: https://x.openai.azure.com      # Azure only

# --- Memory ---
memory:
  conversation:
    enabled: true                             # short-term, per session_id
    window_size: 10                           # messages to keep
  persistent:
    enabled: false                            # long-term facts, per user_id
    backend: sqlite
    connection: ./facts.db                    # file path or ":memory:"

# --- Middleware ---
middleware:
  retry:
    max_attempts: 3                           # total attempts (1 = no retry)
  rate_limiter:
    enabled: true
    requests_per_minute: 60                   # token-bucket ceiling
  cost_tracking:
    enabled: true                             # result.cost_usd
  audit:
    enabled: true                             # logs/audit.jsonl
  # human_approval:
  #   require_approval_for: ["send_email"]    # tool function names
```

Environment variables use `${VAR_NAME}` syntax. They are resolved from your shell
environment at config load time. Keep secrets in `.env`, never in YAML.

---

## Async usage

Every method has an async counterpart:

```python
import asyncio
from roscoe import AgentRunner
from tools.my_tools import TOOLS

agent = AgentRunner.from_config("agent_config.yaml", tools=TOOLS)

async def main():
    result = await agent.arun("What's the weather in London?")
    print(result.output)

    # async resume
    if result.status == "paused":
        result = await agent.aresume(result.run_id, "approve")

asyncio.run(main())
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: roscoe` | venv not active → `source .venv/bin/activate` |
| `status: error`, connection refused | Ollama not running → `ollama serve` |
| `status: error`, mentions api_key | env var not exported — check `.env` |
| Config error naming a `${VAR}` | that env var isn't set; export it or add to `.env` |
| Tool never gets called | make the docstring clearer about *when* to use it |
| `cost_usd` is `None` | model not in cost table — [extend it](#extending-the-cost-table) |
| `pip install` path error | check the path is correct and venv is active |
| Rate limit errors despite rate_limiter | increase `requests_per_minute` in config |
| Memory not persisting | check `persistent.enabled: true` and same `user_id` |
| Audit log not appearing | check `audit.enabled: true` in middleware config |
