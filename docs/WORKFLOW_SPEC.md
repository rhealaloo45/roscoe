# roscoe workflows — declarative agent specification

> Status: **Phase 0 (design lock)**. Phase 1 implements `connector_action`,
> `condition`, and `llm_step`. `agent_step` and the `agents:` block land in Phase 2.

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
| `connector_action` | Call one connector method. No Python. | `connector`, `method` |
| `condition` | Branch on an expression. | `when` |
| `llm_step` | One prompt to the model, no tools. | `prompt` |
| `agent_step` *(Phase 2)* | Hand a sub-problem to the ReAct loop. | `agent` |

Common optional fields on every node: `output` (state key to write), `next` (successor
id), `requires_approval` (pause before running — `connector_action` only, since it is
the only node type with an external side effect).

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
resolved arguments; `reject` skips the node, writes `None` to its `output` key, and
continues along the normal route.

Tool names listed in `middleware.human_approval.require_approval_for` are also
honoured, so a method already gated for the ReAct path stays gated here without being
re-declared per node.

## Errors

Structural problems (unknown node id, unknown node type, missing required field,
duplicate id) raise `WorkflowError` at parse time — before anything runs. Runtime
failures inside a node (a connector 500, a bad expression) end the run with
`status="error"` and the failing node id, rather than raising through the caller.

`roscoe validate` (Phase 3) reports the parse-time class of problem without executing
the workflow.

## Deliberately out of scope for v1

- Parallel node execution — sequential only. Fan-out needs async orchestration and no
  observed use case requires it yet.
- Loops over collections (`iterate_over`). Cycles via `next:` cover simple retry;
  batch iteration is a Phase 2+ decision.
- Sub-workflows. `agent_step` is the composition primitive for now.
