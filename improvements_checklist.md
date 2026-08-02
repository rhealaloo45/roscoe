# roscoe build — master improvements checklist

Target: someone with minimal coding knowledge can build any agent entirely
from `roscoe build`'s UI — easy to navigate, no YAML/dev jargon required to
get something working. Manager signal (2026-08-02 conversation): standalone
Python export is the priority path over hosted deployment, but hosting isn't
being removed — both stay supported.

Ordered by priority. Items found by exercising the running builder directly,
not just reading code, unless noted otherwise.

## 0 — Critical, blocks everything else

- [ ] **Save/Check feedback silently swallowed when no node is selected.**
      `showIssues()` writes into `#issues`, which only exists inside the
      selected-node panel (`build_ui.py:521`, `669-677`). With nothing
      selected: Save writes the file and returns `saved: true` server-side,
      Check finds real problems (e.g. `entry` pointing at a non-existent
      node) — user sees **nothing** either way. Confirmed live via the
      running server. For a non-technical user this isn't "confusing," it's
      "the tool looks broken with no way to tell why." Fix: a persistent
      status bar that exists independent of node selection.

## 1 — Trigger nodes on the canvas (scheduler)

- [ ] New `trigger` node type ("Schedule"), palette entry in `build_ui.py`,
      so scheduling is something you add on canvas, not a CLI flag.
- [ ] New `roscoe schedule` command — wraps `agent.run()`/
      `WorkflowRunner.run()` in an interval loop (`--every 1h`/`1d` first;
      cron syntax later only if `--every` proves insufficient — avoid adding
      a parsing dependency until there's a real reason). Logs to
      `logs/audit.jsonl` like any other run.
- [ ] `roscoe/workflow/schema.py` needs a way to represent "this workflow is
      scheduled, not chat-triggered."
- [ ] Exported-to-Python path needs no new code here — document wiring the
      standalone script into cron / Task Scheduler / a systemd timer instead.

## 2 — Merge run / monitor / evals / logs into `roscoe build`

- [ ] Consolidate the three already-working standalone GUIs (`monitor_gui.py`,
      `eval_gui.py`, `pricing_gui.py`, ~150-200 lines each, share
      `gui_theme.py`) into `build_command.py`'s tab system alongside the
      existing Flow/Setup tabs. This is mostly consolidation, not new
      feature work.
- [ ] New "Run" tab — live chat/test panel inside the builder, reusing
      `run_web.py`'s chat logic.
- [ ] New "Logs" tab — tails `audit.jsonl` live.

## 3 — Node picker rebuild (the real usability unlock)

- [ ] Replace the generic "+connector" name/type/key-value form in
      `build_ui.py` with a categorized picker: icon + one-line plain-language
      description per node/connector type.
- [ ] Per-connector-type guided forms instead of one generic settings list —
      OAuth button for Google/GitHub, token field for TickTick, base-URL
      field for REST, etc.
- [ ] Rename developer-facing UI labels to plain language throughout
      ("connector" / "node" / "expression" / "output_message" are all
      jargon walls for a non-coder) — underlying YAML field names stay
      unchanged, just the labels.
- [ ] Rewrite validation error messages into plain language — today's Check
      output surfaces raw internal errors like `'workflow.entry' points at
      'does_not_exist'`, not something a non-coder can act on.
- [ ] Fold in editor UX gaps (below) as part of this rebuild, not separately.

### Editor UX gaps (fold into Phase 3)

- [ ] No undo — `removeNode()` is destructive with no recovery.
- [ ] Inconsistent commit timing — Flow panel fields use `onchange`
      (blur-only), Setup tab fields use `oninput`. Type-then-click-elsewhere
      can lose an edit (already caused one real bug this session — an empty
      `output_message`).
- [ ] No keyboard shortcuts — no Delete, Ctrl+S, Esc.
- [ ] No zoom / pan / fit-to-view — large workflows become unusable on the
      canvas.
- [ ] No duplicate-node action — 5 similar Action nodes means filling every
      field 5x by hand.
- [ ] Mouse-only drag/link events — no touch/pointer support.
- [ ] Right panel clips under ~900px viewport width (saw "NOTHI..." instead
      of "NOTHING SELECTED" at 800px).

## 4 — New built-in connectors

- [ ] Web search (provider TBD — Tavily/SerpAPI/Brave)
- [ ] SMS sender (Twilio-shaped)
- [ ] Generic mail sender (SMTP, distinct from Gmail-specific `send_email`)
- [ ] Each follows the existing pattern in `roscoe/connectors/*.py` (11
      connectors already there as templates: database, github,
      google_workspace, jira, notion, outlook, rest_api, servicenow,
      sharepoint, snowflake, ticktick)
- [ ] Each new connector slots into Phase 3's picker as it's added.

## 5 — Custom tool escape hatch

- [ ] v1: friendlier UI on top of the existing generic `rest_api` connector
      ("point at your own endpoint").
- [ ] Arbitrary custom Python from inside a browser builder — stretch goal,
      not a v1 requirement.

## 6 — Standalone Python export

- [ ] **Exposed as a button in `roscoe build`'s UI ("Export" / "Download"),
      not a CLI command.** User never opens a terminal for this — click the
      button, browser downloads the `.py` file directly. New `/api/export`
      endpoint in `build_command.py`'s server, plain `<a href="/api/export"
      download>` (or fetch → blob → anchor-click) in `build_ui.py`, no
      separate `roscoe export` step required to get the file.
- [ ] Core generator logic lives in `roscoe/export/python_generator.py`,
      called by the `/api/export` endpoint. (A `roscoe export` CLI command
      can still exist on top of the same module for anyone who prefers the
      terminal — but it's optional, not the primary path.)
- [ ] Supports `connector_action` / `llm_step` / `condition` only; refuses
      cleanly on `agent_step` — shown as a plain-language message in the
      builder UI itself (not a terminal error, nobody's looking at a
      terminal), e.g. "This workflow uses an Agent node — export isn't
      supported for that yet."
- [ ] Only OpenAI-wire-compatible providers for `llm_step` generation
      (openai, azure_openai, nvidia, ollama — all share the `chat/completions`
      shape via `base_url`, confirmed this session). `anthropic`/`gemini` get
      the same clean in-UI refusal as `agent_step` for v1.
- [ ] Generated file has exactly one dependency (`httpx`), no `roscoe`
      import, no `langchain` import.
      - Reuse `roscoe/workflow/expressions.py`'s templating near-verbatim
        (already pure stdlib: `ast` + `re`).
      - Connector calls become direct `httpx` calls built from each
        connector's config (mirrors `base_connector.py`/`rest_api.py`).
      - Node-graph walk mirrors `WorkflowExecutor._walk`
        (`roscoe/workflow/executor.py`) as a plain loop — no retry/approval/
        audit middleware in the export; say so in the generated file's
        header comment.
- [ ] Tests: run generator against Tier-1 fixtures (the FX-rate and
      email-digest-shaped workflows already proven this session), execute
      generated `.py` in a subprocess against a mocked HTTP server, assert
      it matches `WorkflowExecutor`'s real output for the same inputs.

## 7 — "Call Agent" node (multi-agent, in UI + exporter)

- [ ] New node/connector type in the Phase 3 picker — URL + optional key,
      auto-unwraps `.output` instead of making someone hand-template the
      `/api/chat` response envelope.
- [ ] Exporter compiles this to one of two things depending on the target:
      - target agent also exported → direct Python function import, no
        network hop.
      - target agent is hosted/running → an `httpx` call to its `/api/chat`.
- [ ] This is what makes multi-agent orchestration fully buildable in the UI
      without hand-writing YAML.

## 8 — API key + CORS hardening

- [ ] `roscoe/cli/run_web.py` + `run_command.py` — new `--api-key`
      (`envvar="ROSCOE_API_KEY"`, same pattern as `google_auth_command.py`)
      and `--cors-origin` options.
- [ ] `_check_auth(headers, api_key)` / `_cors_headers(origin, allowed)` as
      plain testable functions (mirrors the `_EditorState` pattern already
      used in `build_command.py` — logic separate from the raw
      `BaseHTTPRequestHandler` plumbing).
- [ ] Gate `/api/*` paths only; leave the static page ungated. `401` +
      `{"error": "Unauthorized"}` on missing/wrong `Authorization: Bearer`.
      Unset `--api-key` → unchanged, open, exactly like today (local dev
      stays frictionless).
- [ ] New `tests/unit/test_run_web.py` (doesn't exist yet).

## 9 — Docs

- [ ] `docs.md`: "Integrating roscoe into an existing project" — sibling-
      folder pattern, `--no-browser`, the exact `/api/chat` request/response
      contract, `--api-key`/`--cors-origin` usage.
- [ ] `docs.md`: "Composing multiple agents" — same-process (`agent_step`)
      vs the "Call Agent" node's two compile targets, with a worked example.
- [ ] `docs.md`: exporting a workflow, and scheduling (both `roscoe schedule`
      and wiring an exported script into the OS's own scheduler).
