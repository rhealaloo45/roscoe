# roscoe

> **R**eady-to-run **O**rchestration **S**DK — **C**onfigurable, **O**bservable, **E**xtensible

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org)

**roscoe** is a Python SDK for building LLM-powered agents that ship with production
plumbing built in — retries, cost tracking, audit logging, rate limiting, human approval,
memory, monitoring, and evals. You write tools (plain Python functions) and a YAML config;
roscoe handles everything else.

Switch LLM providers by editing one config block. Your code never changes.

```
pip install roscoe
```

---

## Quick start

### Option A: Scaffold with the CLI

```bash
roscoe init my-agent
```

A GUI wizard opens — pick your provider, toggle middleware, configure memory. Hit
**Create Project** and you get a ready-to-run folder:

```
my-agent/
├── agent_config.yaml    # fully commented — every option explained
├── main.py              # 6 lines to run your agent
├── tools/my_tools.py    # your @tool functions go here
├── prompts/system.txt   # agent personality + instructions
├── evals/test_cases.json
└── .env.example         # all possible credential placeholders
```

```bash
cd my-agent
cp .env.example .env     # fill in your API key
python main.py
```

Use `--quick` to skip the wizard, or `--cli` for a terminal-based wizard.

### Option B: Code it directly

```python
from roscoe import AgentRunner
from roscoe.tools import tool


@tool
def get_price(sku: str) -> dict:
    """Fetch the price for a product SKU."""
    return {"sku": sku, "price": 1999}


agent = AgentRunner.from_config("agent_config.yaml", tools=[get_price])
result = agent.run("What is the price of SKU-001?")

print(result.output)        # the LLM's answer
print(result.status)        # "success" | "error" | "paused"
print(result.cost_usd)      # estimated cost in USD
print(result.total_tokens)  # token usage
print(result.run_id)        # UUID tying this run to the audit trail
```

---

## Features

### Provider-agnostic

Swap LLM providers by editing the `model:` block in your YAML config. Your Python code
stays identical.

| Provider | Config key | Example model |
|---|---|---|
| OpenAI | `openai` | `gpt-4o-mini` |
| OpenRouter | `openai` + `base_url` | any of 100+ models |
| Azure OpenAI | `azure_openai` | `gpt-4o` (via deployment) |
| Anthropic | `anthropic` | `claude-sonnet-4-5` |
| Google Gemini | `gemini` | `gemini-1.5-pro` |
| Ollama (local) | `ollama` | `llama3.1` (free, no key) |

Register custom providers: `ProviderFactory.register("my_provider", MyProviderClass)`.

```yaml
# Just change this block — nothing else
model:
  provider: openai
  model: gpt-4o-mini
  api_key: ${OPENAI_API_KEY}
  temperature: 0.1
```

### Automatic middleware

All middleware is configured in YAML and runs automatically on every `agent.run()` call.
Zero boilerplate code.

```yaml
middleware:
  retry:
    max_attempts: 3              # exponential backoff on transient errors
  rate_limiter:
    enabled: true
    requests_per_minute: 60      # token-bucket per provider
  cost_tracking:
    enabled: true                # USD estimate in result.cost_usd
  audit:
    enabled: true                # JSONL log at logs/audit.jsonl
```

- **Retry** — handles rate limits, timeouts, 500s with exponential backoff.
- **Rate limiting** — token-bucket algorithm, one bucket per provider. Prevents thrashing
  API limits when multiple agents share a provider. Ollama is skipped (no external limit).
- **Cost tracking** — reads `usage_metadata` from LangChain, prices via built-in rate
  table. Extensible:
  ```python
  from roscoe.middleware.cost_tracker import COST_TABLE
  COST_TABLE["openai"]["gpt-4.1"] = {"input": 0.002, "output": 0.008}
  ```
- **Audit logging** — non-blocking JSONL, one line per run: run_id, agent, provider,
  model, tokens, cost, status, latency. Feed to `roscoe monitor` for dashboards.

### Memory

Three types, all configured in YAML:

```yaml
memory:
  conversation:
    enabled: true
    window_size: 10         # last N messages per session_id
  persistent:
    enabled: true
    backend: sqlite
    connection: ./facts.db  # long-term facts per user_id
```

- **Conversation** — short-term, per `session_id`, windowed. Pass `session_id` to
  `agent.run()` to keep context across turns.
- **Persistent** — long-term facts per `user_id` in sqlite. The agent remembers "My name
  is Rhea" across sessions.
- **Knowledge / RAG** — vector retrieval via FAISS (if installed) or a zero-dependency
  keyword retriever. Set up in code:
  ```python
  from roscoe.memory.knowledge import KnowledgeMemory
  km = KnowledgeMemory.from_texts(["policy doc text..."], metadatas=[{"source": "hr.pdf"}])
  ```

### Connectors

Pre-built tool bundles for enterprise systems. Each connector gives you LangChain tools
you hand straight to `AgentRunner`:

```python
from roscoe.connectors import GitHubConnector

gh = GitHubConnector({"token": "ghp_..."})
agent = AgentRunner.from_config("agent.yaml", tools=gh.tools)

# Mix with your own tools:
agent = AgentRunner.from_config("agent.yaml", tools=[my_tool] + gh.tools)
```

| Connector | Tools | Auth |
|---|---|---|
| **REST** (any API) | GET, POST, PUT, DELETE | Bearer / Basic / API key |
| **Jira** | search, create, update, transition issues | Email + API token |
| **ServiceNow** | create, query, update incidents | Username + password |
| **Outlook** | send email, read inbox, create event, availability | MS Graph (OAuth2) |
| **SharePoint** | list files, download, upload, search | MS Graph (OAuth2) |
| **GitHub** | list repos, issues, PRs, create issue | Personal access token |
| **Notion** | search, pages, databases, blocks | Integration token |
| **Google Workspace** | Gmail send/read, Calendar, Tasks, Drive search | Service account or OAuth2 (`roscoe google-auth`) |
| **Snowflake** | execute SQL queries | `pip install roscoe[snowflake]` |

### Human-in-the-loop

Make sensitive tools require approval before they run:

```yaml
middleware:
  human_approval:
    require_approval_for: ["send_email", "delete_record"]
```

```python
result = agent.run("Send an email to bob@acme.com")

if result.status == "paused":
    print(result.pending_action)  # inspect what the agent wants to do

    # approve — tool runs as planned
    result = agent.resume(result.run_id, "approve")

    # reject — tool is blocked, agent gets a rejection message
    result = agent.resume(result.run_id, "reject")

    # modify — change the arguments before running
    result = agent.resume(result.run_id, "modify", payload={"to": "correct@acme.com"})
```

The run stops *before* the gated tool executes and returns `status="paused"`. Call
`resume()` to continue. Wire this to a Slack button, a web UI, or a CLI prompt.

### Monitoring

Offline aggregation of your audit logs — no live server required.

```bash
roscoe monitor --path logs/audit.jsonl
```

Outputs a text dashboard with:
- Total runs, cost per day, cost per agent
- Latency percentiles (p50 / p95 / p99) per agent
- Error rate breakdown
- Token usage summary

**Alerts** — configure thresholds for daily cost, error rate, and latency:

```python
from roscoe.monitoring.alerts import check_and_notify
from roscoe.monitoring.notifier import build_notifier

notifier = build_notifier("slack", {"webhook_url": "https://hooks.slack.com/..."})
check_and_notify(metrics, alert_config, notifier)
```

**Exporters** — push metrics to Prometheus Pushgateway or Azure Monitor:

```python
from roscoe.monitoring.exporters.prometheus import PrometheusPushgatewayExporter
exporter = PrometheusPushgatewayExporter(gateway_url="http://localhost:9091")
exporter.push(metrics)
```

### Evals

Test your agent with a dataset of cases and score the results:

```bash
roscoe eval --dataset evals/test_cases.json --config agent_config.yaml
```

**Scorers:**
- **Tool usage** — deterministic, order-aware (did the agent call the right tools?).
- **Output quality** — LLM-as-judge, 0–10 scale. Add `--judge` to enable.
- **Hallucination** — LLM-as-judge, checks output against provided context docs.

**Regression diffing** — compare two eval runs:

```python
from roscoe.evals.regression import compare_runs
diff = compare_runs(report_a, report_b)
print(diff.improved)    # cases that got better
print(diff.regressed)   # cases that got worse
```

**Test case format** (`evals/test_cases.json`):

```json
{
  "cases": [
    {
      "id": "weather-london",
      "input": "What's the weather in London?",
      "expected_tools": ["get_weather"],
      "expected_output": "Should mention London weather",
      "context_docs": ["London is currently 15°C and rainy."]
    }
  ]
}
```

### Templates

Six pre-built templates, each with tools, system prompt, config, and approval gates
pre-configured:

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
| `hr_agent` | Leave, payslips, personal details | REST API | submit_leave_request |
| `it_support_agent` | Tickets, escalation, KB search | ServiceNow | escalate_ticket |
| `legal_agent` | Contract search, clause extraction, risk flags | Knowledge (RAG) | — |
| `knowledge_base_agent` | Q&A over Notion / SharePoint / docs | Notion + Knowledge | — (read-only) |
| `exec_assistant_agent` | Email, calendar, availability | Outlook (MS Graph) | send_email, create_event |
| `google_workspace_agent` | Gmail, Calendar, Tasks, Drive | Google Workspace | send_email, create_event, create_task |

---

## Workflows — no-code agents

`agent_config.yaml` configures the scaffolding; a **workflow** defines the behaviour,
so the common cases stop costing a Python file each:

```bash
roscoe init-nc my-agent      # agent_config.yaml + workflow.yaml, no Python
roscoe validate              # check it before running it
roscoe run --set topic="cloud security"
```

```yaml
workflow:
  entry: lookup
  nodes:
    - id: lookup
      type: connector_action        # call a connector method — config, not code
      connector: hr_api
      method: get_employee
      inputs: { employee_id: "{{ input.id }}" }
      output: employee

    - id: eligible
      type: condition               # branch on the state
      when: "employee.department in ['Engineering', 'Product']"
      then: grant
      else: explain

    - id: grant
      type: connector_action
      requires_approval: true       # pause for a human before it runs
      connector: vpn
      method: grant_access
      inputs: { employee_id: "{{ employee.id }}" }
      next: END

    - id: explain
      type: llm_step                # one prompt, no tools
      prompt: "Explain why {{ employee.name }} was denied."
```

| Node type | Does |
|---|---|
| `connector_action` | Calls one connector method with templated arguments |
| `condition` | Branches on a sandboxed expression over the shared state |
| `llm_step` | Sends one prompt to the model |
| `agent_step` | Hands a task to a named agent running the autonomous ReAct loop |

`agent_step` is the escape hatch — when a stretch of work is too open-ended to wire
by hand, let an agent pick its own tool calls. Several `agent_step` nodes passing
results through the state is how multi-agent works, with the graph as the
orchestrator. Agents inherit the workflow's approval gate, so wrapping work in an
agent is never a way around one.

Expressions are parsed and walked against an allowlist, never `eval()`'d: dotted
access is dict lookup rather than `getattr`, and only allowlisted helpers can be
called. Full reference: [`docs/WORKFLOW_SPEC.md`](docs/WORKFLOW_SPEC.md).

Projects that write their tools in Python are unaffected — `roscoe init` and the
existing `@tool` flow work exactly as before.

---

## Architecture

roscoe runs its own async ReAct loop (no LangGraph dependency). The loop is ~100 lines
in `roscoe/core/executor.py`:

```
User message
  → model.invoke(messages)
  → tool_calls in response?
    → approval gate check (pause if gated)
    → execute tools
    → append results to messages
    → loop back to model
  → no tool_calls? done → AgentResult
```

Built on **LangChain** (models, tools, messages) but not LangGraph. This keeps the agent
loop small, transparent, and easy to debug.

---

## CLI reference

```bash
roscoe init <name>                              # scaffold with GUI wizard
roscoe init <name> --quick                      # scaffold with defaults (no wizard)
roscoe init <name> --cli                        # scaffold with terminal wizard
roscoe init <name> --template <t>               # scaffold from a template
roscoe init-nc <name>                           # no-code project — behaviour in workflow.yaml

roscoe validate                                 # check a workflow before running it
roscoe validate --workflow flow.yaml            # check a specific workflow file

roscoe run --set topic="cloud security"         # run a workflow with inputs
roscoe run                                      # browser chat (default)
roscoe run --terminal                           # interactive chat in the terminal, streamed
roscoe run -m "message"                         # one-shot message, terminal, exits after
roscoe run --host 0.0.0.0 --port 8080            # web chat host/port
roscoe run --ui-script app.py                   # force a specific custom UI script
roscoe run --no-ui-script                       # ignore any custom UI script, use built-in widget

roscoe monitor                                  # dashboard from logs/audit.jsonl
roscoe monitor --path /path/to/audit.jsonl      # custom audit log path

roscoe eval --dataset cases.json --config agent.yaml          # tool-usage scoring
roscoe eval --dataset cases.json --config agent.yaml --judge  # + LLM-as-judge
roscoe eval --dataset cases.json --config agent.yaml --tools module:attr  # custom tools

roscoe prices                                   # desktop editor for LLM pricing overrides
roscoe prices --terminal                        # print the effective price table

roscoe google-auth --client-id ... --client-secret ...  # mint a GOOGLE_REFRESH_TOKEN for OAuth2 mode

roscoe --version                                # print version
```

`roscoe run` opens a browser chat by default. If the project directory has its own
UI entry point — `app.py`, or whatever `ui_script:` names in `agent_config.yaml` —
`roscoe run` launches that instead of the built-in widget, so a bespoke
login/dashboard/chat app just works with no flags. `--terminal` and `-m` always
bypass any custom UI script and talk to the agent directly in-process.

---

## Install

```bash
pip install roscoe                # core
pip install "roscoe[snowflake]"   # + Snowflake driver
pip install "roscoe[azure]"       # + Azure Monitor exporter
pip install "roscoe[dev]"         # + pytest (for contributors)
```

### From source

```bash
git clone https://github.com/rhealaloo45/roscoe.git
cd roscoe
pip install -e ".[dev]"
pytest -q    # 121 tests, all passing
```

---

## Writing tools

A tool is a plain Python function with type hints and a docstring:

```python
from roscoe.tools import tool


@tool
def search_docs(query: str) -> list[dict]:
    """Search internal documents. Use when the user asks about company policies."""
    # your logic here
    return [{"title": "Remote Work Policy", "snippet": "..."}]
```

- The **docstring** is what the LLM reads to decide when to call the tool.
- **Type hints** are used to generate the JSON schema automatically.
- **Return dicts or primitives** — the LLM reads the return value.
- Add the tool to the `TOOLS` list in `tools/my_tools.py`, or pass it directly to
  `AgentRunner.from_config()`.

---

## Configuration reference

Full `agent_config.yaml` with all options (also generated by `roscoe init` with inline
comments):

```yaml
agent_name: my-agent

system_prompt_file: prompts/system.txt
# system_prompt: |
#   Inline prompt alternative

model:
  provider: openai                 # openai | azure_openai | anthropic | gemini | ollama
  model: gpt-4o-mini
  api_key: ${OPENAI_API_KEY}       # resolved from environment
  temperature: 0.1
  # base_url: https://openrouter.ai/api/v1   # for OpenRouter / custom endpoints
  # max_tokens: 4096

memory:
  conversation:
    enabled: true
    window_size: 10
  persistent:
    enabled: false
    backend: sqlite
    connection: ./facts.db

middleware:
  retry:
    max_attempts: 3
  rate_limiter:
    enabled: true
    requests_per_minute: 60
  cost_tracking:
    enabled: true
  audit:
    enabled: true
  # human_approval:
  #   require_approval_for: ["send_email", "delete_record"]
```

Environment variables use `${VAR_NAME}` syntax and are resolved at config load time.

---

## License

MIT — see [LICENSE](./LICENSE).
