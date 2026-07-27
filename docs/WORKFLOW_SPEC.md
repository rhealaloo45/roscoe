# roscoe workflows — declarative agent specification

> Status: implemented. All four node types, the `agents:` block, nested approval, and
> `roscoe validate` are live. Wiring into `roscoe run` / `roscoe init-nc` is next.

## Why this exists

roscoe's `agent_config.yaml` configures the *scaffolding* around an agent — model,
memory, middleware. The agent's actual behaviour still has to be written as Python
`@tool` functions. That makes the common cases (call an API, branch on a field, ask
the model to write a paragraph) cost a Python file each.

A workflow is a declarative description of **what the agent does**, not just how it's
configured. It is deliberately *not* a general programming language: it covers the
common 80% and hands anything genuinely bespoke to a Python tool or — from Phase 2 —
to `agent_step`, which drops into roscoe's existing autonomous ReAct loop.

Nothing here replaces the existing path. A project with no `workflow:` block behaves
exactly as it does today.

## What it reuses

The workflow engine is an orchestration layer over machinery roscoe already has. It
adds no new agent runtime, no new approval mechanism, and no new provider handling:

| Concern | Reused from |
|---|---|
| Tool invocation | connector `StructuredTool`s (`connector.tools`) |
| LLM calls | `roscoe.llm.provider_factory.ProviderFactory` |
| Human approval | `roscoe.approval.gate.ApprovalGate` (same `paused` / `resume` contract) |
| Retry, rate limit, cost, audit | the existing middleware stack in `AgentRunner` |
| Autonomous reasoning | `roscoe.core.executor.ReactExecutor` (via `agent_step`, Phase 2) |

## Shape

```yaml
workflow:
  entry: get_employee          # node id to start at (defaults to the first node)
  max_steps: 50                # cycle guard; optional
  output: "{{ message }}"      # final answer template; optional

  nodes:
    - id: get_employee
      type: connector_action
      connector: hr_rest
      method: get_employee
      inputs:
        employee_id: "{{ input.employee_id }}"
      output: employee

    - id: check_eligible
      type: condition
      when: "employee.department in ['Engineering', 'Product']"
      then: grant_vpn
      else: deny_vpn

    - id: grant_vpn
      type: connector_action
      connector: vpn
      method: grant_access
      inputs: { employee_id: "{{ employee.id }}" }
      requires_approval: true
      output: grant_result
      next: END

    - id: deny_vpn
      type: llm_step
      prompt: "Explain politely why {{ employee.name }} was denied VPN access."
      output: message
```

### State

One dict flows through the whole run. Every node reads from it and may write one key
to it (`output:`). The run's input is namespaced under `input`, so
`{{ input.employee_id }}` refers to what the caller passed in, and `{{ employee }}`
refers to whatever an earlier node stored.

### Routing

Each node picks its successor in this order:

1. an explicit `next:` (or `then:`/`else:` on a `condition`),
2. otherwise the next node in the list,
3. otherwise the run ends.

`END` is a reserved id meaning "stop here". Cycles are legal — `max_steps` bounds them.

## Node types

| Type | Purpose | Required fields |
|---|---|---|
| `connector_action` | Call one tool or connector method. No Python. | `method` |
| `condition` | Branch on an expression. | `when` |
| `llm_step` | One prompt to the model, no tools. | `prompt` |
| `agent_step` | Hand a sub-problem to the ReAct loop. | `agent`, `task` |

Common optional fields on every node: `output` (state key to write), `next` (successor
id), `requires_approval` (pause before running — `connector_action` only, since it is
the only node type with an external side effect).

`connector:` is optional on a `connector_action`. Name one to disambiguate, or leave
it out and the `method` resolves against the project's own `@tool` functions first,
then any configured connector — so a workflow can call local Python without dressing
it up as a connector. Naming a connector explicitly makes a miss an error rather than
silently landing on a same-named method elsewhere.

## Agents

`agent_step` is the escape hatch: when a stretch of work is too open-ended to wire as
explicit nodes, hand it to an agent that picks its own tool calls. Agents are declared
once and referenced by name, so several steps can share one:

```yaml
agents:
  researcher:
    system_prompt: "You find and summarise facts."
    tools: [web.search, knowledge.search]   # connector.method, or a bare tool name
    max_iterations: 10

workflow:
  nodes:
    - id: research
      type: agent_step
      agent: researcher
      task: "Research {{ input.topic }}"
      output: findings

    - id: write
      type: agent_step
      agent: writer
      task: "Summarise: {{ findings }}"
      output: summary
```

Multi-agent is just several `agent_step` nodes handing results to each other through
the state — the orchestration is this graph, not a second framework. Each named agent
compiles to its own `ReactExecutor`, so nothing about the autonomous loop changes.

Agents inherit the workflow's approval gate. If an agent reaches for a tool listed in
`require_approval_for`, the **whole workflow** pauses with the agent mid-flight and
resumes exactly where it stopped — wrapping work in an agent is never a way around a
gate.

## Expressions and templates

Two closely related things, both evaluated against the state:

- **Templates** appear in `inputs:`, `prompt:`, and `output:`. `"{{ x }}"` alone
  returns the *typed* value of `x` (a dict stays a dict). Mixed text like
  `"Hi {{ name }}"` renders to a string.
- **Expressions** appear in a `condition`'s `when:`, and inside `{{ }}`. Same grammar
  either way.

The grammar is a deliberately small subset of Python, evaluated by walking a parsed
AST with an allowlist — never `eval()`. It supports comparisons, boolean and
arithmetic operators, `in`, indexing, literals, conditional expressions, and a fixed
set of safe functions (`len`, `str`, `int`, `float`, `bool`, `abs`, `min`, `max`,
`round`, `sorted`, `sum`, `any`, `all`, `lower`, `upper`, `strip`, `get`).

Two rules keep it sandboxed:

- **Dotted access is dict lookup, not `getattr`.** `employee.name` means
  `employee["name"]`. Python attributes are never reached, so `__class__` and friends
  are not a way out.
- **Only bare-name calls to allowlisted functions.** `foo.bar()` does not parse, so
  no method on a state value can be invoked.

Anything outside the allowlist (lambdas, comprehensions, imports, attribute calls,
assignment) raises `ExpressionError` naming the construct.

## Human approval

A node with `requires_approval: true` stops the run *before* it executes and returns
`status="paused"`, carrying the node id and its already-resolved arguments so a human
sees exactly what is about to happen. Resuming with `approve` / `reject` / `modify`
uses the same decision vocabulary as tool-level approval today. `modify` replaces the
resolved arguments.

**`reject` stops the run** unless the node sets `on_reject:`. This is deliberate: a
node's `next:` was written for the action having succeeded, so continuing down it
after a refusal reports work that never happened — a rejected "grant access" would
still reach the node that says access is active. Give the node an `on_reject:` to
route somewhere that tells the truth:

```yaml
- id: grant
  type: connector_action
  method: grant_vpn_access
  requires_approval: true
  next: confirm_granted      # taken on approve
  on_reject: explain_refusal # taken on reject; without it, the run stops
```

Tool names listed in `middleware.human_approval.require_approval_for` are also
honoured, so a method already gated for the ReAct path stays gated here without being
re-declared per node.

A pause carries a `kind` saying what is being approved — `connector` for a gated node,
`agent` for a tool call an `agent_step`'s inner loop wants to make. Rejecting an agent's
call reports the refusal back to the agent, which then decides how to proceed, rather
than aborting the run.

## Giving it a front end

`roscoe run` serves a browser page. An optional `ui:` block in `agent_config.yaml`
brands it, so a project gets a usable front end without anyone writing one:

```yaml
ui:
  title: Ham Ventures IT Desk
  subtitle: VPN access requests
  heading: Request VPN access
  intro: Fill in your details and submit. An administrator approves each grant.
  accent: "#7c3aed"
  submit: Submit request

  inputs:                       # swaps the chat box for a form
    - name: employee_id
      label: Employee ID
      placeholder: E-1042
      required: true
    - name: action
      label: What do you need?
      type: select
      options: [grant, revoke]
      default: grant
```

`inputs:` is the part that matters for a workflow. A chat box asks for a sentence,
but a workflow takes **named inputs** (`{{ input.employee_id }}`), so each field maps
to one, and submitting runs the workflow with them. Without `inputs:` the page stays a
chat box and the message arrives as `{{ input.message }}`.

Approval still happens in the page: a gated node shows the real call and its arguments
with Approve / Reject buttons, whichever mode you are in.

For anything beyond this, `ui_script:` still points `roscoe run` at your own web app.

## Seeing the workflow

```bash
roscoe graph                       # renders it in a browser
roscoe graph --terminal            # prints Mermaid source
roscoe graph --output flow.mmd     # writes it to a file for a PR or a wiki
```

The picture is generated from the file every time, so the two cannot drift. Shape
carries the meaning: rectangles call out to a system, diamonds decide, rounded nodes
write prose, stadiums hand off to an agent. Steps that stop for a human are outlined
and marked, and a dotted edge is one that falls through to the next node in the file
rather than being routed explicitly.

## Validating before you run

```bash
roscoe validate                        # uses agent_config.yaml
roscoe validate --workflow flow.yaml
```

Structural problems (unknown node type, missing field, duplicate id, edge to nowhere)
fail at parse time. On top of that `roscoe validate` reports:

- expression syntax errors and disallowed constructs, per node;
- nodes no edge can reach;
- unknown connector methods, missing required inputs, and arguments a method does not
  take — read from the connector tools' existing schemas, so nothing extra to author.

Connector-aware checks need connectors that can actually be built; if credentials are
missing, validation says so and falls back to structure-only rather than failing.
Exits non-zero on any error, so it drops straight into CI.

## Errors

Structural problems (unknown node id, unknown node type, missing required field,
duplicate id) raise `WorkflowError` at parse time — before anything runs. Runtime
failures inside a node (a connector 500, a bad expression) end the run with
`status="error"` and the failing node id, rather than raising through the caller.

`roscoe validate` (Phase 3) reports the parse-time class of problem without executing
the workflow.

## Talking to a database

The database connector makes a SQL-backed workflow possible with no Python: SQLite
needs no driver and no service, and any other database works by naming a DB-API
driver you already have.

```yaml
connectors:
  hrdb:
    type: database
    path: ./app.db
    schema: ./schema.sql  # applied once, when the database is first created
    read_only: false      # writes are off unless you say so
```

`schema:` means a project needs no setup script: point it at plain SQL (tables plus
any seed rows) and the database is built the first time it is opened. It runs **only**
on a database that did not already exist, so re-running never overwrites live data —
write it with `CREATE TABLE IF NOT EXISTS` / `INSERT OR IGNORE` and it stays safe to
apply by hand too. `roscoe validate` prints each database's tables, so a missing
schema shows up before a run rather than as a confusing error during one.

Unlike anything the tools accept at runtime, a schema file may contain many
statements — it comes from the config, not from a model. For non-SQLite databases,
create the schema with your own migration tooling instead.

```yaml
- id: find_employee
  type: connector_action
  connector: hrdb
  method: query
  inputs:
    sql: "SELECT name, department FROM employees WHERE employee_id = ?"
    params: ["{{ input.employee_id }}"]
  output: matches
```

`query` returns a list of rows, so a lookup reads as `matches[0].department` and an
empty list means "not found" — `len(matches) > 0`.

Three things keep model-authored SQL from becoming a liability: **writes are opt-in**
(the `execute` tool is not even offered on a read-only connector, so the model cannot
reach for it), **values bind as parameters** rather than being formatted into the
statement, and **only one statement runs per call**, so nothing rides along behind a
semicolon. Gate `execute` with `human_approval` as well when it touches anything that
matters.

## Writing `llm_step` prompts

Constrain the format, not just the content. A prompt like *"Tell {{ name }} they
already have access"* reliably produces a full email — subject line, greeting, and a
`[Your Name]` signature block — because nothing told the model otherwise.

Put the rules that apply to every step in the workflow's `system:` once, and keep each
`prompt:` about what that step is actually saying:

```yaml
workflow:
  system: >
    You write short service-desk replies. Always answer in one or two plain
    sentences. Never add a greeting, a signature, or email formatting.

  nodes:
    - id: already_message
      type: llm_step
      prompt: "Tell {{ employee.name }} they already have VPN access."
```

A node's own `system:` overrides the workflow's, for the step that needs different
rules. There is no `goal:` — a workflow's intent is the shape of the graph. The one
place a goal belongs is an agent's `system_prompt`, because an agent decides its own
actions.

### Getting structured data back

By default an `llm_step`'s output is plain text — fine for a message, wrong for
anything a later node needs to read a field out of. Add `parse: json` and the reply
is parsed before it lands in state:

```yaml
- id: summarise
  type: llm_step
  prompt: |
    Return JSON only:
    {"summary": "...", "action_items": [{"task": "...", "owner": "..."}]}
  parse: json
  output: notes

- id: create_tasks
  type: agent_step
  agent: task_maker
  task: "Create a task for each of these: {{ notes.action_items }}"
```

A markdown code fence (` ```json ... ``` `) is stripped automatically — models asked
for JSON wrap it in one almost every time. If the reply still isn't valid JSON, the
run ends with a `WorkflowError` naming the node and showing what the model actually
said, rather than a confusing `'action_items' is not a valid index for a str` several
nodes downstream where the string was finally read.

## Deliberately out of scope for v1

- Parallel node execution — sequential only. Fan-out needs async orchestration and no
  observed use case requires it yet.
- Loops over collections (`iterate_over`). Cycles via `next:` cover simple retry;
  batch iteration is a Phase 2+ decision.
- Sub-workflows. `agent_step` is the composition primitive for now.
