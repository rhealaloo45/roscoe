# roscoe build — master improvements checklist

Target: someone with minimal coding knowledge can build any agent entirely
from `roscoe build`'s UI — easy to navigate, no YAML/dev jargon required to
get something working. Manager signal (2026-08-02 conversation): standalone
Python export is the priority path over hosted deployment, but hosting isn't
being removed — both stay supported.

All phases below are implemented and committed on `dev`. Notes kept as a
record of what each one was for.

Two things landed that weren't on the original list, both found by running the
editor rather than reading it: the builder served on a single-threaded server
(one wedged client froze it for everyone), and a quote-escaping slip silently
killed every button in the page while it still rendered — there is now a test
that parses the page's JavaScript.

Still open (marked inline below):
- Validation messages are visible now but still read as internal errors.
- Node-panel labels in the Flow tab are still developer-facing.
- Drag/link is mouse-only; no touch support.
- Custom Python from the browser — deliberately out of scope for v1.
- Export doesn't cover connectors needing roscoe's own signed client.

## 0 — Critical, blocks everything else — DONE (7705287)

- [x] **Save/Check feedback silently swallowed when no node is selected.**
      `showIssues()` writes into `#issues`, which only exists inside the
      selected-node panel (`build_ui.py:521`, `669-677`). With nothing
      selected: Save writes the file and returns `saved: true` server-side,
      Check finds real problems (e.g. `entry` pointing at a non-existent
      node) — user sees **nothing** either way. Confirmed live via the
      running server. For a non-technical user this isn't "confusing," it's
      "the tool looks broken with no way to tell why." Fix: a persistent
      status bar that exists independent of node selection.
- [x] Also fixed while verifying: the editor served on a single-threaded
      HTTPServer, so one wedged client froze the whole builder. Now
      ThreadingHTTPServer, matching run_web.py.

## 1 — Trigger nodes on the canvas (scheduler) — DONE (0a612e3)

- [x] New `trigger` node type ("Schedule"), palette entry in `build_ui.py`,
      so scheduling is something you add on canvas, not a CLI flag.
- [x] New `roscoe schedule` command — wraps `agent.run()`/
      `WorkflowRunner.run()` in an interval loop (`--every 1h`/`1d` first;
      cron syntax later only if `--every` proves insufficient — avoid adding
      a parsing dependency until there's a real reason). Logs to
      `logs/audit.jsonl` like any other run.
- [x] `roscoe/workflow/schema.py` needs a way to represent "this workflow is
      scheduled, not chat-triggered."
- [x] Exported-to-Python path needs no new code here — document wiring the
      standalone script into cron / Task Scheduler / a systemd timer instead.

## 2 — Merge run / monitor / logs into `roscoe build` — DONE (a566382)

- [x] Consolidate the three already-working standalone GUIs (`monitor_gui.py`,
      `eval_gui.py`, `pricing_gui.py`, ~150-200 lines each, share
      `gui_theme.py`) into `build_command.py`'s tab system alongside the
      existing Flow/Setup tabs. This is mostly consolidation, not new
      feature work.
- [x] New "Run" tab — live chat/test panel inside the builder, reusing
      `run_web.py`'s chat logic.
- [x] New "Logs" tab — tails `audit.jsonl` live.

## 3 — Node picker rebuild — DONE (97c3dd5, 95ea6c7)

- [x] Replace the generic "+connector" name/type/key-value form in
      `build_ui.py` with a categorized picker: icon + one-line plain-language
      description per node/connector type.
- [x] Per-connector-type guided forms instead of one generic settings list —
      OAuth button for Google/GitHub, token field for TickTick, base-URL
      field for REST, etc.
- [~] PARTLY. The connector picker and its forms are in plain language.
      The Flow tab's node panel still says "expression", "state key" and
      similar. Not finished.
- [ ] NOT DONE. Check still surfaces raw internal errors like
      `'workflow.entry' points at 'does_not_exist'`. They are now visible
      (Phase 0) but not yet rewritten for a non-coder.
- [x] Fold in editor UX gaps (below) as part of this rebuild, not separately.

### Editor UX gaps — DONE (95ea6c7)

- [x] No undo — `removeNode()` is destructive with no recovery.
- [x] Inconsistent commit timing — Flow panel fields use `onchange`
      (blur-only), Setup tab fields use `oninput`. Type-then-click-elsewhere
      can lose an edit (already caused one real bug this session — an empty
      `output_message`).
- [x] No keyboard shortcuts — no Delete, Ctrl+S, Esc.
- [x] No zoom / pan / fit-to-view — large workflows become unusable on the
      canvas.
- [x] No duplicate-node action — 5 similar Action nodes means filling every
      field 5x by hand.
- [ ] NOT DONE. Still mouse-only — drag and link bind mousemove/mouseup,
      so a tablet can't move nodes. The zoom work touched the same code but
      only fixed the scale maths, not the event types.
- [x] Right panel clips under ~900px viewport width (saw "NOTHI..." instead
      of "NOTHING SELECTED" at 800px).

## 4 — New built-in connectors — DONE (6de85e5)

- [x] Web search (provider TBD — Tavily/SerpAPI/Brave)
- [x] SMS sender (Twilio-shaped)
- [x] Generic mail sender (SMTP, distinct from Gmail-specific `send_email`)
- [x] Each follows the existing pattern in `roscoe/connectors/*.py`.
- [x] Each new connector slots into Phase 3's picker as it's added.

## 5 — Custom tool escape hatch — DONE for v1 (97c3dd5)

- [x] v1: friendlier UI on top of the existing generic `rest_api` connector
      ("point at your own endpoint").
- [ ] NOT DONE, and deliberately out of scope for v1 — running
      user-supplied Python from a browser form is its own problem.

## 6 — Standalone Python export — DONE (9295d7f)

- [x] **Exposed as a button in `roscoe build`'s UI ("Export" / "Download"),
      not a CLI command.** User never opens a terminal for this — click the
      button, browser downloads the `.py` file directly. New `/api/export`
      endpoint in `build_command.py`'s server, plain `<a href="/api/export"
      download>` (or fetch → blob → anchor-click) in `build_ui.py`, no
      separate `roscoe export` step required to get the file.
- [x] Core generator logic lives in `roscoe/export/python_generator.py`,
      called by the `/api/export` endpoint. (A `roscoe export` CLI command
      can still exist on top of the same module for anyone who prefers the
      terminal — but it's optional, not the primary path.)
- [x] Supports `connector_action` / `llm_step` / `condition` only; refuses
      cleanly on `agent_step` — shown as a plain-language message in the
      builder UI itself (not a terminal error, nobody's looking at a
      terminal), e.g. "This workflow uses an Agent node — export isn't
      supported for that yet."
- [x] Only OpenAI-wire-compatible providers for `llm_step` generation
      (openai, azure_openai, nvidia, ollama — all share the `chat/completions`
      shape via `base_url`, confirmed this session). `anthropic`/`gemini` get
      the same clean in-UI refusal as `agent_step` for v1.
- [x] Generated file has exactly one dependency (`httpx`), no `roscoe`
      import, no `langchain` import.
      - Reuse `roscoe/workflow/expressions.py`'s templating near-verbatim
        (already pure stdlib: `ast` + `re`).
      - Connector calls become direct `httpx` calls built from each
        connector's config (mirrors `base_connector.py`/`rest_api.py`).
      - Node-graph walk mirrors `WorkflowExecutor._walk`
        (`roscoe/workflow/executor.py`) as a plain loop — no retry/approval/
        audit middleware in the export; say so in the generated file's
        header comment.
- [x] Tests: run generator against Tier-1 fixtures (the FX-rate and
      email-digest-shaped workflows already proven this session), execute
      generated `.py` in a subprocess against a mocked HTTP server, assert
      it matches `WorkflowExecutor`'s real output for the same inputs.

### 6a — Export beyond REST connectors — DONE (3c24429)

The first cut refused every connector but `rest_api`/`agent`, which made
export unusable for the agents people actually build in the picker — a
web-search agent couldn't be downloaded at all.

- [x] Nine more types export: web search (Tavily/Brave/Serper), SMTP, Twilio,
      GitHub, Jira, ServiceNow, Notion, TickTick, SQLite. Each contributes the
      source of its own tools from `roscoe/export/connector_snippets.py`.
- [x] Only the types the workflow actually calls are emitted, so the file
      stays readable and doesn't ship clients it never reaches.
- [x] Tool names checked at export time — a node calling a method its
      connector doesn't have is refused in the editor, not at runtime.
- [x] Connector type aliases (`search`, `email`, `sms`, `sqlite`) resolve the
      same way `registry.py` resolves them.
- [ ] **NOT DONE — OAuth connectors still refuse:** `google_workspace`,
      `outlook`, `sharepoint`. Each needs a token-refresh exchange baked into
      the generated file before its calls; doable with `httpx` alone, just not
      done yet. `snowflake` and non-SQLite databases refuse for a different
      reason — they need a driver an exported file can't assume is installed —
      and probably always should.

### 6b — Download as a project folder — DONE (b4479ce)

A loose `.py` wasn't a usable handover. The button now returns a zip:
`agent.py`, `.env.example` (generated from the config, so it can't drift from
what the script reads), `requirements.txt`, and a README describing this
workflow's steps.

- [x] Generated file reads a `.env` beside it via a stdlib parser — no
      python-dotenv dependency, and real environment variables still win.
- [x] `/api/export-bundle` alongside the existing `/api/export`.
- [x] Verified by unzipping and running in a fresh interpreter with roscoe
      off the path.

## 7 — "Call Agent" node — DONE (8ce4d86)

- [x] New node/connector type in the Phase 3 picker — URL + optional key,
      auto-unwraps `.output` instead of making someone hand-template the
      `/api/chat` response envelope.
- [x] Exporter compiles this to one of two things depending on the target:
      - target agent also exported → direct Python function import, no
        network hop.
      - target agent is hosted/running → an `httpx` call to its `/api/chat`.
- [x] This is what makes multi-agent orchestration fully buildable in the UI
      without hand-writing YAML.

## 8 — API key + CORS hardening — DONE (c295888)

- [x] `roscoe/cli/run_web.py` + `run_command.py` — new `--api-key`
      (`envvar="ROSCOE_API_KEY"`, same pattern as `google_auth_command.py`)
      and `--cors-origin` options.
- [x] `_check_auth(headers, api_key)` / `_cors_headers(origin, allowed)` as
      plain testable functions (mirrors the `_EditorState` pattern already
      used in `build_command.py` — logic separate from the raw
      `BaseHTTPRequestHandler` plumbing).
- [x] Gate `/api/*` paths only; leave the static page ungated. `401` +
      `{"error": "Unauthorized"}` on missing/wrong `Authorization: Bearer`.
      Unset `--api-key` → unchanged, open, exactly like today (local dev
      stays frictionless).
- [x] New `tests/unit/test_run_web.py` (doesn't exist yet).

## 9 — Docs — DONE (fec0bfb)

- [x] `docs.md`: "Integrating roscoe into an existing project" — sibling-
      folder pattern, `--no-browser`, the exact `/api/chat` request/response
      contract, `--api-key`/`--cors-origin` usage.
- [x] `docs.md`: "Composing multiple agents" — same-process (`agent_step`)
      vs the "Call Agent" node's two compile targets, with a worked example.
- [x] `docs.md`: exporting a workflow, and scheduling (both `roscoe schedule`
      and wiring an exported script into the OS's own scheduler).
