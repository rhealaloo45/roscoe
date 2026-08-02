# roscoe — Developer Guide

> **R**eady-to-run **O**rchestration **S**DK — **C**onfigurable, **O**bservable, **E**xtensible

Everything you need to build, run, and operate your agent. Copy-paste the snippets
directly — they all work with the scaffolded project structure.

---

## Table of contents

1. [Project structure](#project-structure)
2. [Available templates](#available-templates)
3. [Building without code: workflows & `roscoe build`](#building-without-code-workflows--roscoe-build)
4. [Writing tools](#writing-tools)
5. [Running the agent](#running-the-agent)
6. [Running on a schedule](#running-on-a-schedule)
7. [Adding roscoe to an existing project](#adding-roscoe-to-an-existing-project)
8. [Composing multiple agents](#composing-multiple-agents)
9. [Exporting a standalone Python file](#exporting-a-standalone-python-file)
10. [Multi-turn conversations](#multi-turn-conversations)
11. [Swapping LLM providers](#swapping-llm-providers)
12. [Memory](#memory)
13. [Connectors](#connectors)
14. [Human-in-the-loop (HITL)](#human-in-the-loop-hitl)
15. [Audit log & cost tracking](#audit-log--cost-tracking)
16. [Monitoring dashboard](#monitoring-dashboard)
17. [Alerts & exporters](#alerts--exporters)
18. [Evals](#evals)
19. [Extending the cost table](#extending-the-cost-table)
20. [Configuration reference](#configuration-reference)
21. [Async usage](#async-usage)
22. [Troubleshooting](#troubleshooting)

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

## Building without code: workflows & `roscoe build`

Everything below this point assumes you're writing Python tools. If instead you
want to describe your agent's behaviour as a graph of steps — no Python file at
all — that's a **workflow**, and `roscoe build` is a visual editor for it. If
you've never used either, [`quickstart.md`](quickstart.md) walks through
building one from a blank project in about 15 minutes; this section is the
reference for everything that tutorial doesn't cover.

### workflow.yaml vs agent_config.yaml

`agent_config.yaml` still holds the model, memory, middleware, and connector
settings — nothing about that changes. A **workflow** adds a `workflow:` block
(either inline in `agent_config.yaml`, or in a sibling `workflow.yaml`, which
is what `roscoe init-nc` and `roscoe build` both produce) that describes the
agent's behaviour as nodes and edges instead of a Python ReAct loop:

```yaml
workflow:
  entry: lookup                 # which node runs first
  output: '{{ status }}'        # what the whole run reports back, once done
  nodes:
    - id: lookup
      type: connector_action
      connector: hr_api
      method: get_employee
      inputs: { employee_id: "{{ input.id }}" }
      output: employee
      next: eligible

    - id: eligible
      type: condition
      when: "employee.department in ['Engineering', 'Product']"
      then: grant
      else: explain

    - id: grant
      type: connector_action
      connector: vpn
      method: grant_access
      inputs: { employee_id: "{{ employee.id }}" }
      requires_approval: true
      output_message: "Access granted for {{ employee.name }}."
      output: status
      next: END

    - id: explain
      type: llm_step
      prompt: "Explain why {{ employee.name }} was denied, briefly."
      output: status
```

### Node types

| Type | What it does | Key fields |
|---|---|---|
| `trigger` | Records how often the workflow should run — see [Running on a schedule](#running-on-a-schedule). Does nothing when executed | `every`, `at` |
| `connector_action` | Calls one method on one connector, with templated arguments | `connector`, `method`, `inputs`, `output`, `requires_approval`, `output_message`, `on_reject` |
| `condition` | Branches on an expression evaluated against the current state | `when`, `then`, `else` |
| `llm_step` | Sends one prompt to the model — no tools, just a completion | `prompt`, `system` (optional override), `parse` (`json` or unset), `output` |
| `agent_step` | Hands a task to a named agent (defined under `agents:`) that runs its own autonomous tool-calling loop | `agent`, `task`, `output` |

Every node (except `condition`) can set `next:` to name the following node, or
`END` to finish the run. `condition` uses `then`/`else` instead.

### Templating: `{{ ... }}`

Any string field can reference the shared state with `{{ expression }}`:

- `{{ input.field }}` — reads whatever was passed into `agent.run(...)`, or
  typed into a form built from `ui.inputs` (see below)
- `{{ some_node_output.field }}` — reads a previous node's result, using
  whatever name that node's `output:` gave it
- Dotted access (`employee.name`) and indexing (`found.files[0].id`) both work,
  but only on data that's actually there — expressions are parsed and checked
  against an allowlist, never `eval()`'d, so a typo or an unknown name fails
  loudly with a message naming exactly which name and which field, rather than
  silently returning `None` or executing arbitrary code.
- A string that is *only* one placeholder (`"{{ prep.tasks }}"`) keeps the
  referenced value's real type — a list stays a list, a dict stays a dict.
  Mixed text (`"Hi {{ input.name }}"`) always renders to a string.

Full expression grammar (comparisons, boolean logic, the small set of allowed
helper functions): [`WORKFLOW_SPEC.md`](https://github.com/rhealaloo45/roscoe/blob/main/docs/WORKFLOW_SPEC.md).

### Getting structured data back: `parse: json`

By default an `llm_step`'s output is plain text. Ask for JSON and get a real
dict/list back instead of a string you'd have to parse yourself:

```yaml
- id: summarise
  type: llm_step
  prompt: |
    Return JSON only, no code fences:
    {"summary": "...", "action_items": [{"task": "...", "owner": "..."}]}
  parse: json
  output: notes
```

Later nodes can then read `{{ notes.summary }}` or loop over
`{{ notes.action_items }}` directly. If the model's reply isn't valid JSON (or
is wrapped in markdown code fences — those are stripped automatically first),
the node fails with a clear error showing a snippet of what actually came
back, rather than a generic parse traceback.

### Showing something readable: `output_message`

A connector's raw return is API-shaped — an id, a status code, a nested object
— exactly right for the *next* node to read, and wrong to show a person as
"here's what happened." `output_message` renders a friendlier string instead,
and it's stored under the node's `output:` key in place of the raw value:

```yaml
- id: send_recap
  type: connector_action
  connector: gmail
  method: send_email
  inputs: { to: "{{ input.participants }}", subject: "Recap", body: "{{ notes.summary }}" }
  output_message: "Recap sent to {{ input.participants }}."
  output: status
```

`output_message` can also reference the call's own return value, under its own
`output:` name — e.g. if a `create_tasks_batch` call outputs to `result`,
`output_message: "Created {{ result.created }} task(s)."` works, because that
value is made available under `result` specifically for this render, even
though it hasn't been written into the shared state yet at that point.

### Human approval: `requires_approval`

Add `requires_approval: true` to any `connector_action` node to pause the run
right before that call executes:

```yaml
requires_approval: true
on_reject: recap_cancelled   # optional — where to go if rejected; default is just "stop"
```

The run comes back with `status: "paused"` and a `pending_action` describing
exactly what's about to run (method name and resolved arguments — the reviewer
sees real values, not the template). Continue it with:

```python
result = agent.resume(run_id, "approve")   # run it as planned
result = agent.resume(run_id, "reject")    # skip it, follow on_reject if set
result = agent.resume(run_id, "modify", payload={"to": "corrected@company.com"})
```

If a node's action naturally creates *several* things in one go (e.g. several
tasks from a list), give the connector a single method that takes the whole
list and creates them all — that way there's one thing to approve, not one
pause per item. `output_message` still applies the same way on approval as it
does on a normal (non-gated) run.

### The visual editor: `roscoe build`

```bash
roscoe build              # opens http://localhost:8099
roscoe build --port 9000  # different port
```

Four tabs — building, testing and reviewing an agent all happen here, so
nothing below needs a terminal.

**Flow** — the graph itself:
- Click **Schedule** / **Action** / **Decision** / **Prompt** / **Agent** in
  the left palette to add a node of that type
- Click a node to select it; the right panel shows exactly the fields that
  node type takes (see the table above)
- Drag from a node's output port to another node to connect them — or just set
  "Then go to" (or "Yes →" / "No →" for a Decision) in the panel; both do the
  same thing
- The top toolbar sets **Entry** (which node runs first) and **Workflow
  output** (what the whole run reports back — usually `{{ some_output }}`)

Click **Save workflow.yaml** when done, or **Download Python** to take the
whole thing away as a standalone file (see
[Exporting a standalone Python file](#exporting-a-standalone-python-file)).

| Shortcut | Does |
|---|---|
| `Ctrl/Cmd + S` | Save |
| `Ctrl/Cmd + Z` | Undo |
| `Ctrl/Cmd + D` | Duplicate the selected node |
| `Delete` | Delete the selected node |
| `Escape` | Deselect |

Shortcuts are ignored while you're typing in a field, so `Delete` in a prompt
deletes a character. The **Fit** button in the bottom-right scales the canvas
so a large workflow fits on screen.

**Setup** — everything that isn't the graph:
- **Model** — provider, model name, API key, temperature
- **Connectors** — pick from a grouped list of what each one does; each opens a
  form with its own named fields and help text. Secrets prefill as `${VAR}` so
  a real key never gets typed into a file you'll commit
- **Agents** — needed only for `agent_step` nodes; give an agent a name, a
  system prompt, and tick which connector methods it may call
- **Web page** — the `ui:` block (title, accent colour, and either a chat box
  or a form built from named inputs) controlling what `roscoe run` serves

Click **Save setup** to write these into `agent_config.yaml`. The form reads
and writes *without* resolving `${VAR}`, so real secrets are never written back
into the file.

**Run** — try the agent without leaving the editor. It runs the **saved**
workflow, so save first: "run it" should mean the thing that would actually
run. You'll see each node tick off as it goes, then the answer, tokens and
cost.

**Activity** — every run this project has done: totals, error rate, cost, and a
table of recent runs. Same figures `roscoe monitor` reports, from the same
audit log.

### Checking it before running it: `roscoe validate`

```bash
roscoe validate                        # checks agent_config.yaml's workflow
roscoe validate --workflow other.yaml  # checks a specific file instead
```

Reports unreachable nodes, expression syntax errors, unknown connector
methods, and missing required inputs — all without making a single LLM call or
touching a real connector. Deliberately tolerant of missing secrets (a fresh
project's `${OPENAI_API_KEY}` won't exist yet, and that shouldn't block
checking the graph's structure) but will use real connector credentials to
verify method names when they *are* available.

### Seeing the shape of it: `roscoe graph`

```bash
roscoe graph              # opens a flowchart in your browser
roscoe graph --terminal   # prints Mermaid source instead — paste into a PR or wiki
roscoe graph --output flow.mmd
```

Read-only and generated straight from `workflow.yaml`, so the diagram and the
actual behaviour can't drift apart.

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
roscoe run --set topic=x      # workflow projects: supply inputs and run once
```

If this project has its own web UI script (`app.py`, or whatever `ui_script:`
names in `agent_config.yaml`), `roscoe run` launches that instead of the
built-in browser widget — so a custom login page or dashboard just works with
no flags. `--terminal` and `-m` always talk to the agent directly and never
touch a custom UI script.

If the project defines a workflow (a `workflow:` block or a `workflow.yaml`),
`roscoe run` executes that graph instead of an autonomous agent. Check it first
with `roscoe validate`, which reports bad expressions, unreachable nodes, unknown
connector methods, and missing inputs without running anything.

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

## Running on a schedule

Add a **Schedule** node in `roscoe build` (or a `trigger` node by hand) to say
how often a workflow should run, then start it:

```yaml
workflow:
  entry: daily
  nodes:
    - id: daily
      type: trigger
      every: 1d           # 30s | 15m | 2h | 1d | 7d
      at: "06:00"         # optional wall-clock time, only for `every: 1d`
      next: fetch_data
```

```bash
roscoe schedule                 # uses the trigger's own interval
roscoe schedule --now           # run immediately, then keep to the interval
roscoe schedule --every 30m     # override without editing the file
roscoe schedule --once          # wait for the first firing, run once, exit
```

Each firing is an ordinary run, so cost tracking, the audit log and
`roscoe monitor` all pick scheduled runs up with no special handling. A failed
run is logged and the schedule keeps going — tomorrow's run is still worth
attempting.

A trigger only records *when*; it does nothing when executed. A scheduled
workflow is still an ordinary graph that `roscoe run` executes on demand.

**For something that must survive a reboot**, run `roscoe schedule` under
whatever keeps your other services alive (systemd, pm2, a container restart
policy) — or export the workflow (below) and point cron / Task Scheduler at the
generated file.

---

## Adding roscoe to an existing project

An agent runs as its own process. The tidiest way to add one to an app you
already have is a sibling folder, run alongside the frontend and backend you're
already running:

```
my-project/
├── frontend/
├── backend/
└── agent/               ← roscoe lives here
    ├── agent_config.yaml
    ├── workflow.yaml
    └── .env
```

Start it as a service rather than a UI:

```bash
cd agent
roscoe run --no-browser --host 127.0.0.1 --port 8090
```

Your backend then calls it over plain HTTP — **in any language**. Nothing
roscoe-specific is needed on that side; roscoe only has to be installed
wherever the agent process itself runs.

```bash
curl -X POST http://127.0.0.1:8090/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "summarise today"}'
```

```json
{"type": "final", "output": "...", "tokens": 342, "cost": "$0.0012", "tools": []}
```

The reply is one of three shapes:

| `type` | Meaning | Other fields |
|---|---|---|
| `final` | It finished | `output`, `tokens`, `cost`, `tools` |
| `paused` | It stopped for a human decision | `run_id`, `tool_calls` |
| `error` | It failed | `error` |

A `paused` reply is continued by posting the decision back:

```bash
curl -X POST http://127.0.0.1:8090/api/approve \
  -H 'Content-Type: application/json' -d '{"decision": "approve"}'
```

### Guarding it

The API is open by default, which is fine on `127.0.0.1` and not fine anywhere
else. Before binding to anything wider, require a key:

```bash
roscoe run --no-browser --host 0.0.0.0 --port 8090 --api-key "$ROSCOE_API_KEY"
```

Every `/api/` call then needs `Authorization: Bearer <key>`; anything else gets
`401`. If a **browser** on another origin calls it directly, allow that origin
so the preflight succeeds:

```bash
roscoe run --no-browser --cors-origin https://app.example.com --api-key "$ROSCOE_API_KEY"
```

Prefer calling from your own backend where you can, so the key never reaches a
browser at all.

---

## Composing multiple agents

Two ways, depending on whether the pieces are one deployment or several.

**In one process** — an `agent_step` node hands part of a workflow to a named
agent that runs its own tool-calling loop. Best when the agents ship together.

**Across processes** — one agent calls another over its API, using the `agent`
connector. Best when teams own and deploy their agents separately:

```yaml
connectors:
  sales:
    type: agent
    base_url: http://localhost:8091
  finance:
    type: agent
    base_url: http://localhost:8092
    api_key: ${FINANCE_AGENT_KEY}     # if that agent runs with --api-key

workflow:
  entry: ask_sales
  output: "{{ answer }}"
  nodes:
    - id: ask_sales
      type: connector_action
      connector: sales
      method: ask
      inputs: { message: "{{ input.question }}" }
      output: sales_answer
      next: ask_finance

    - id: ask_finance
      type: connector_action
      connector: finance
      method: ask
      inputs: { message: "{{ input.question }}" }
      output: finance_answer
      next: merge

    - id: merge
      type: llm_step
      prompt: |
        Combine these into one answer:
        Sales: {{ sales_answer }}
        Finance: {{ finance_answer }}
      output: answer
```

`ask` returns the sub-agent's answer as text, not the `/api/chat` envelope, so
`{{ sales_answer }}` is the reply itself. If a sub-agent fails, the calling
workflow stops with that reason rather than continuing with an error payload in
place of an answer. A sub-agent that pauses for human approval can't answer a
machine caller at all, and says so — remove the approval gate on any agent
meant to be called this way.

---

## Exporting a standalone Python file

Some teams can't install roscoe — an org that won't approve the dependency, or
an app that wants the agent inline rather than as a service. Click
**Download Python** in `roscoe build` and you get a single file whose only
dependency is `httpx`:

```bash
pip install httpx
python your_agent.py "a message"
```

```python
from your_agent import run

result = run({"question": "how are we doing?"})
print(result["status"])   # "success" | "error"
print(result["output"])
print(result["steps"])    # the nodes it walked through
```

Secrets aren't baked in — `${VARS}` stay placeholders and resolve from the
environment wherever the file runs, so an exported agent is safe to commit.

**What exports:** Action, Decision, Prompt and Schedule nodes; REST and `agent`
connectors; and models on an OpenAI-shaped API (`openai`, `nvidia`, `ollama`).

**What doesn't**, and why:

| Not supported | Reason |
|---|---|
| Agent nodes | They choose their own tools as they go, which needs roscoe's agent loop |
| Gmail, GitHub, Jira, … | They need roscoe's own client to sign their requests |
| `anthropic`, `gemini` | Different API shape from the generated caller |
| Bare methods with no connector | They resolve to this project's Python tools, which the file can't reach |

Export refuses these by name rather than producing a file that fails later, and
says what to do instead.

**Not carried over:** retries, approval gates, audit logging and cost tracking.
An export is the workflow's logic, not roscoe's runtime around it. If you need
those, run it with roscoe.

To schedule an exported file, use the OS scheduler:

```bash
# crontab -e   (Linux/macOS) — every day at 06:00
0 6 * * * cd /path/to/agent && /usr/bin/python3 your_agent.py
```

On Windows, Task Scheduler → Create Task → Action: `python.exe your_agent.py`.

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
| TickTick | `TickTickConnector` | `token`, `default_project_id` |
| Database | `DatabaseConnector` | `path` (SQLite) — or `driver` + `dsn`/`params` for any DB-API driver. `schema:` builds the db on first use; `read_only: false` to allow writes |
| Snowflake | `SnowflakeConnector` | `account`, `user`, `password`, `warehouse`, `database` |
| Web search | `WebSearchConnector` | `provider` (`tavily`/`brave`/`serper`), `api_key` — results normalised to `{title, url, snippet}` whichever you pick |
| Email (SMTP) | `SMTPConnector` | `host`, `port`, `username`, `password`, `from` — sends mail with no OAuth app to register |
| SMS (Twilio) | `TwilioConnector` | `account_sid`, `auth_token`, `from` |
| Another agent | `AgentConnector` | `base_url`, `api_key` — see [Composing multiple agents](#composing-multiple-agents) |

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
