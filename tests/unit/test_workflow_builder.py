"""The visual builder's server side — loading, validating, and saving a workflow.

Saving overwrites a real file, so most of these are about what must *survive* it.
A builder that quietly drops a setting is worse than no builder at all.
"""

import json
import textwrap

import pytest
import yaml

from roscoe.cli.build_command import LAYOUT_FILE, _EditorState
from roscoe.workflow.schema import Workflow

CONFIG = """
    agent_name: demo
    model:
      provider: ollama
      model: llama3.1
"""

WORKFLOW = """
    agents:
      helper:
        system_prompt: You help.
        tools: []

    workflow:
      entry: a
      system: Be brief.
      output: "{{ out }}"
      max_steps: 30
      nodes:
        - id: a
          type: llm_step
          prompt: hi
          output: out
          next: END
"""


def _project(tmp_path, workflow=WORKFLOW):
    (tmp_path / "agent_config.yaml").write_text(textwrap.dedent(CONFIG))
    if workflow is not None:
        (tmp_path / "workflow.yaml").write_text(textwrap.dedent(workflow))
    return _EditorState(tmp_path / "agent_config.yaml", tmp_path / "workflow.yaml")


# --- round-trip fidelity ---


def test_to_dict_is_a_faithful_inverse_of_from_dict():
    block = yaml.safe_load(textwrap.dedent(WORKFLOW))["workflow"]
    once = Workflow.from_dict(block)
    twice = Workflow.from_dict(once.to_dict())

    assert once.to_dict() == twice.to_dict()
    assert twice.max_steps == 30
    assert twice.system == "Be brief."


def test_every_node_field_survives_a_round_trip():
    block = {
        "entry": "act",
        "nodes": [
            {"id": "act", "type": "connector_action", "connector": "db", "method": "execute",
             "inputs": {"sql": "DELETE FROM t WHERE id = ?", "params": ["{{ input.id }}"]},
             "requires_approval": True, "output": "done", "next": "ok", "on_reject": "no"},
            {"id": "ok", "type": "llm_step", "prompt": "yes", "system": "terse", "next": "END"},
            {"id": "no", "type": "condition", "when": "1 > 0", "then": "ok", "else": "ok"},
        ],
    }
    restored = Workflow.from_dict(Workflow.from_dict(block).to_dict()).to_dict()

    assert restored["nodes"] == block["nodes"]


# --- loading ---


def test_load_returns_the_workflow_and_known_agents(tmp_path):
    data = _project(tmp_path).load()

    assert data["workflow"]["entry"] == "a"
    assert data["agents"] == ["helper"]
    assert data["error"] is None


def test_load_offers_a_starter_workflow_for_a_new_project(tmp_path):
    data = _project(tmp_path, workflow=None).load()

    assert data["workflow"]["nodes"]
    assert data["error"] is None


def test_load_surfaces_a_broken_file_instead_of_crashing(tmp_path):
    state = _project(tmp_path, "workflow:\n  nodes:\n    - id: a\n      type: nonsense\n")
    data = state.load()

    assert "unknown type" in data["error"]


# --- saving ---


def test_save_writes_a_file_that_parses_back(tmp_path):
    state = _project(tmp_path)
    block = state.load()["workflow"]
    block["nodes"][0]["prompt"] = "changed"

    result = state.save(block, {"a": {"x": 10, "y": 20}})

    assert result["saved"] is True
    reloaded = yaml.safe_load((tmp_path / "workflow.yaml").read_text())
    assert reloaded["workflow"]["nodes"][0]["prompt"] == "changed"


def test_save_preserves_other_top_level_keys(tmp_path):
    # The editor only knows about `workflow:`; everything else must survive.
    state = _project(tmp_path)
    state.save(state.load()["workflow"], {})

    reloaded = yaml.safe_load((tmp_path / "workflow.yaml").read_text())
    assert reloaded["agents"]["helper"]["system_prompt"] == "You help."


def test_save_keeps_settings_the_canvas_does_not_draw(tmp_path):
    state = _project(tmp_path)
    state.save(state.load()["workflow"], {})

    reloaded = yaml.safe_load((tmp_path / "workflow.yaml").read_text())["workflow"]
    assert reloaded["max_steps"] == 30
    assert reloaded["system"] == "Be brief."
    assert reloaded["output"] == "{{ out }}"


def test_save_refuses_an_invalid_workflow(tmp_path):
    state = _project(tmp_path)
    original = (tmp_path / "workflow.yaml").read_text()

    result = state.save({"nodes": [{"id": "x", "type": "condition"}]}, {})

    assert result["saved"] is False
    assert result["issues"]
    # The file on disk is untouched.
    assert (tmp_path / "workflow.yaml").read_text() == original


def test_save_stores_layout_beside_the_workflow_not_inside_it(tmp_path):
    state = _project(tmp_path)
    state.save(state.load()["workflow"], {"a": {"x": 42, "y": 7}})

    layout = json.loads((tmp_path / LAYOUT_FILE).read_text())
    assert layout["a"] == {"x": 42, "y": 7}
    # No coordinates leak into the config the user reads and edits.
    saved = yaml.safe_load((tmp_path / "workflow.yaml").read_text())
    assert "layout" not in saved and "layout" not in saved["workflow"]
    assert all("x" not in node and "y" not in node for node in saved["workflow"]["nodes"])


def test_layout_is_read_back_on_load(tmp_path):
    state = _project(tmp_path)
    state.save(state.load()["workflow"], {"a": {"x": 42, "y": 7}})

    assert state.load()["layout"]["a"]["x"] == 42


def test_a_corrupt_layout_file_is_ignored(tmp_path):
    state = _project(tmp_path)
    (tmp_path / LAYOUT_FILE).write_text("{ not json")

    assert state.load()["layout"] == {}


# --- validation ---


def test_check_reports_issues_without_writing(tmp_path):
    state = _project(tmp_path)
    result = state.check({"nodes": [{"id": "a", "type": "llm_step", "prompt": "{{ lambda: 1 }}"}]})

    assert result["ok"] is False
    assert any("Unsupported syntax" in i["message"] for i in result["issues"])


def test_check_passes_a_sound_workflow(tmp_path):
    state = _project(tmp_path)
    assert state.check(state.load()["workflow"])["ok"] is True


# --- the Setup tab: editing agent_config.yaml from the browser ---


SETUP_CONFIG = """
    agent_name: demo
    model:
      provider: ollama
      model: llama3.1
      api_key: ${OLLAMA_KEY}
    middleware:
      audit:
        enabled: true
    connectors:
      appdb:
        type: database
        path: ./app.db
"""


def _setup_project(tmp_path):
    (tmp_path / "agent_config.yaml").write_text(textwrap.dedent(SETUP_CONFIG))
    (tmp_path / "workflow.yaml").write_text(textwrap.dedent(WORKFLOW))
    return _EditorState(tmp_path / "agent_config.yaml", tmp_path / "workflow.yaml")


def _config_on_disk(state):
    return yaml.safe_load(state.config_file.read_text())


def test_load_offers_the_config_for_editing(tmp_path):
    data = _setup_project(tmp_path).load()

    assert data["config"]["model"]["provider"] == "ollama"
    assert data["config"]["connectors"]["appdb"]["path"] == "./app.db"
    assert "database" in data["connector_types"]
    assert data["agents_detail"]["helper"]["system_prompt"] == "You help."


def test_secrets_are_offered_as_placeholders_not_resolved_values(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_KEY", "sk-real-secret")
    data = _setup_project(tmp_path).load()

    # Reading through load_config would resolve this, and the next save would
    # write the real key into a file the user commits.
    assert data["config"]["model"]["api_key"] == "${OLLAMA_KEY}"


def test_saving_setup_writes_model_connectors_and_ui(tmp_path):
    state = _setup_project(tmp_path)

    state.save_config({
        "model": {"provider": "nvidia", "model": "gpt-oss", "api_key": "${NV}"},
        "connectors": {"google": {"type": "google_workspace", "client_id": "${CID}"}},
        "ui": {"title": "Meeting Assistant",
               "inputs": [{"name": "meeting_title", "required": True}]},
        "agents": {},
    })
    on_disk = _config_on_disk(state)

    assert on_disk["model"] == {"provider": "nvidia", "model": "gpt-oss", "api_key": "${NV}"}
    assert on_disk["connectors"]["google"]["client_id"] == "${CID}"
    assert on_disk["ui"]["inputs"][0]["name"] == "meeting_title"


def test_keys_the_setup_tab_does_not_own_survive_a_save(tmp_path):
    state = _setup_project(tmp_path)

    state.save_config({"model": {"provider": "openai"}, "connectors": {}, "ui": {},
                       "agents": {}})
    on_disk = _config_on_disk(state)

    # agent_name and middleware have no UI — losing them would be silent damage.
    assert on_disk["agent_name"] == "demo"
    assert on_disk["middleware"]["audit"]["enabled"] is True


def test_clearing_a_section_removes_it_rather_than_writing_an_empty_block(tmp_path):
    state = _setup_project(tmp_path)

    state.save_config({"model": {"provider": "openai"}, "connectors": {}, "ui": {},
                       "agents": {}})

    assert "connectors" not in _config_on_disk(state)


def test_agents_are_written_beside_the_workflow_without_losing_it(tmp_path):
    state = _setup_project(tmp_path)

    state.save_config({
        "model": {}, "connectors": {}, "ui": {},
        "agents": {"task_maker": {"system_prompt": "Make tasks.",
                                  "tools": ["ticktick.create_task"]}},
    })
    document = yaml.safe_load(state.workflow_file.read_text())

    assert document["agents"]["task_maker"]["tools"] == ["ticktick.create_task"]
    assert document["workflow"]["entry"] == "a"      # the graph is still there
    assert "helper" not in document["agents"]        # replaced, not merged


def test_saving_the_graph_afterwards_keeps_the_agents(tmp_path):
    state = _setup_project(tmp_path)
    state.save_config({"model": {}, "connectors": {}, "ui": {},
                       "agents": {"task_maker": {"system_prompt": "Make tasks."}}})

    state.save({"entry": "a", "nodes": [{"id": "a", "type": "llm_step", "prompt": "hi"}]}, {})
    document = yaml.safe_load(state.workflow_file.read_text())

    assert "task_maker" in document["agents"]


def test_a_connector_that_cannot_be_opened_is_saved_but_flagged(tmp_path):
    state = _setup_project(tmp_path)

    result = state.save_config({
        "model": {}, "ui": {}, "agents": {},
        "connectors": {"jira": {"base_url": "", "email": "", "api_token": ""}},
    })

    assert result["saved"] is True          # never lose what was typed
    assert any(i["level"] == "warning" and "jira" in i["message"] for i in result["issues"])


def test_an_agent_given_a_method_its_connector_lacks_is_an_error(tmp_path):
    state = _setup_project(tmp_path)

    result = state.save_config({
        "model": {}, "ui": {},
        "connectors": {"appdb": {"type": "database", "path": str(tmp_path / "a.db")}},
        "agents": {"helper": {"tools": ["appdb.drop_everything"]}},
    })

    assert any(i["level"] == "error" and "drop_everything" in i["message"]
               for i in result["issues"])


def test_saving_setup_reports_the_methods_now_available(tmp_path):
    state = _setup_project(tmp_path)

    result = state.save_config({
        "model": {}, "ui": {}, "agents": {},
        "connectors": {"appdb": {"type": "database", "path": str(tmp_path / "a.db")}},
    })

    assert "query" in result["methods"]["appdb"]


# --- the editor page itself ---


def test_feedback_is_rendered_outside_the_property_panel():
    """Save/Validate results must not live inside the node property panel.

    The panel is replaced wholesale by "Nothing selected" whenever no node is
    highlighted, so anything written into it then is discarded silently — the
    file saved, validation found real errors, and the user saw nothing at all.
    Feedback goes to a toast that is always in the document instead.
    """
    from roscoe.cli.build_ui import PAGE

    assert 'id="status"' in PAGE                      # the always-present target
    assert "getElementById('status')" in PAGE         # ...and what showIssues writes to
    # No feedback container may live inside the panel/setup markup again.
    assert 'id="issues"' not in PAGE
    assert "setupIssues" not in PAGE


def test_saving_setup_confirms_success_rather_than_going_quiet():
    """A clean save used to render an empty list, which reads as "nothing
    happened" — the one case where the user most needs to know it worked."""
    from roscoe.cli.build_ui import PAGE

    assert "Saved agent_config.yaml" in PAGE


def test_the_editor_server_is_threaded():
    """A single-threaded server lets one wedged client freeze the whole editor
    — a stale tab or a browser that hung mid-request took the builder down with
    it, which looks indistinguishable from a crash."""
    import inspect

    from roscoe.cli import build_command

    source = inspect.getsource(build_command)
    assert "ThreadingHTTPServer((host, port)" in source


# --- running and reviewing, without leaving the editor ---


def test_metrics_read_the_projects_own_audit_log(tmp_path):
    """Activity must report on the project being edited, not whatever log
    happens to sit in the process's working directory."""
    state = _project(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "audit.jsonl").write_text(
        json.dumps({"agent_name": "demo", "status": "success", "total_tokens": 12,
                    "start_time": "2026-08-02T10:00:00", "end_time": "2026-08-02T10:00:01"})
        + "\n"
        + json.dumps({"agent_name": "demo", "status": "error", "error": "Boom: nope",
                      "start_time": "2026-08-02T11:00:00", "end_time": "2026-08-02T11:00:01"})
        + "\n",
        encoding="utf-8",
    )

    out = state.metrics()

    assert out["total_runs"] == 2
    assert out["runs_by_status"] == {"success": 1, "error": 1}
    assert out["error_rate_pct"] == 50.0
    assert out["errors_by_type"] == {"Boom": 1}
    assert [r["status"] for r in out["recent"]] == ["error", "success"]  # newest first


def test_metrics_on_a_project_that_has_never_run_are_empty_not_an_error(tmp_path):
    out = _project(tmp_path).metrics()

    assert out["total_runs"] == 0
    assert out["recent"] == []


def test_a_broken_config_is_reported_rather_than_crashing_the_editor(tmp_path):
    """Hitting Run with an unloadable project must come back as a message in
    the page — a traceback out of the request handler would take the tab down
    with no explanation."""
    (tmp_path / "agent_config.yaml").write_text("model: {provider: nope}\n")
    (tmp_path / "workflow.yaml").write_text(textwrap.dedent(WORKFLOW))
    state = _EditorState(tmp_path / "agent_config.yaml", tmp_path / "workflow.yaml")

    out = state.run_agent({})

    assert out["status"] == "error"
    assert out["error"]


def test_progress_starts_empty(tmp_path):
    assert _project(tmp_path).progress() == {"steps": [], "log": []}


def test_the_editor_offers_run_and_activity_tabs():
    """Trying a workflow out lives in a drawer over the Flow tab rather than a
    tab of its own — the endpoints behind it are what has to stay reachable."""
    from roscoe.cli.build_ui import PAGE

    assert "showRunDock(true)" in PAGE
    assert "tab('activity')" in PAGE
    assert "/api/run" in PAGE
    assert "/api/metrics" in PAGE


def test_setup_shows_one_section_at_a_time():
    """Four sections stacked in one scroll put a 220px block and a 1,900px
    block in the same column. Setup is a settings screen: a rail of sections
    down the side, one open at a time."""
    from roscoe.cli.build_ui import PAGE

    assert "showSetupSection(" in PAGE
    assert 'id="setupNav"' in PAGE
    for section in ("model", "connectors", "agents", "page"):
        assert f'data-sec="{section}"' in PAGE


def test_a_collapsed_card_builds_no_body_at_all():
    """Hiding the body in CSS still rendered every sub-agent's whole
    connector-by-method tool matrix on every draw — three agents meant that
    matrix three times over."""
    from roscoe.cli.build_ui import PAGE

    # Both card builders return early, before any body markup, when closed.
    assert PAGE.count("if(!open) return head + '</div>';") == 2
    assert "const open = openAgent === i;" in PAGE
    assert "const open = openConn === i;" in PAGE


def test_remove_is_inside_the_opened_card_not_on_every_row():
    """A column of remove buttons down a collapsed list is noise, and one
    mis-click from dropping a configured connector."""
    from roscoe.cli.build_ui import PAGE

    assert "function cardFoot(" in PAGE
    assert "Remove connector" in PAGE
    assert "Remove sub-agent" in PAGE
    # The summary row carries a disclosure chevron and nothing destructive.
    head = PAGE[PAGE.index("function cardHead("):PAGE.index("function cardFoot(")]
    assert "danger" not in head


def test_connectors_are_chosen_from_a_modal_not_a_wall_of_buttons():
    """The whole catalogue rendered inline made Setup unreadable; it now sits
    behind one button that opens a searchable picker."""
    from roscoe.cli.build_ui import PAGE

    assert "openPicker()" in PAGE
    assert 'id="pickerModal"' in PAGE
    assert 'id="pickerSearch"' in PAGE


def test_the_editor_uses_icons_rather_than_emoji():
    """Emoji render differently per platform and drag full colour into a flat
    two-tone UI — every glyph is an inline SVG inheriting currentColor."""
    import re

    from roscoe.cli.build_ui import PAGE

    emoji = re.compile("[\U0001f000-\U0001faff⬀-⯿]")
    # The run log's own ✓/✗ markers are terminal output being echoed, not UI
    # chrome, so they're matched as text rather than drawn — allow those.
    chrome = "\n".join(l for l in PAGE.split("\n") if "includes('" not in l)
    assert not emoji.search(chrome), f"emoji left in the page: {emoji.findall(chrome)}"


def test_export_refusal_comes_back_as_a_message_not_an_exception(tmp_path):
    """The person clicking Download is looking at a web page, not a terminal —
    a raised ExportError would surface as a dead request with no explanation."""
    (tmp_path / "agent_config.yaml").write_text(textwrap.dedent(CONFIG))
    (tmp_path / "workflow.yaml").write_text(textwrap.dedent("""
        agents:
          helper:
            system_prompt: You help.
            tools: []

        workflow:
          entry: a
          nodes:
            - id: a
              type: agent_step
              agent: helper
              task: do something
    """))
    state = _EditorState(tmp_path / "agent_config.yaml", tmp_path / "workflow.yaml")

    out = state.export_python()

    assert out["ok"] is False
    assert "Agent node" in out["error"]


def test_export_returns_a_named_file_and_its_source(tmp_path):
    state = _project(tmp_path)

    out = state.export_python()

    assert out["ok"] is True
    assert out["filename"] == "demo.py"          # from agent_name
    compile(out["source"], out["filename"], "exec")


# --- editor ergonomics ---


def test_destructive_actions_snapshot_for_undo():
    """Deleting a node used to be unrecoverable — you rebuilt it by hand."""
    from roscoe.cli.build_ui import PAGE

    assert "function undo()" in PAGE
    for mutation in ("function removeNode()", "function addNode(", "function duplicateNode()"):
        start = PAGE.index(mutation)
        assert "snapshot()" in PAGE[start:start + 400], f"{mutation} does not snapshot"


def test_free_text_fields_commit_as_you_type():
    """`onchange` only fires on blur, so typing a prompt and immediately hitting
    Save lost the edit. This actually happened — an output_message came out
    empty. Selects and checkboxes stay on change; they fire correctly already."""
    from roscoe.cli.build_ui import PAGE

    for field in ("prompt", "task", "when", "output_message", "output", "system"):
        # The page's JS quotes the field name with escaped single quotes.
        assert 'oninput="set(' + "\\'" + field in PAGE, field


def test_typing_is_not_hijacked_by_shortcuts():
    """Delete inside a prompt must delete a character, not the selected node."""
    from roscoe.cli.build_ui import PAGE

    assert "function typing(el)" in PAGE
    assert "if(typing(e.target)) return;" in PAGE


def test_dragging_accounts_for_zoom():
    """The pointer moves in screen pixels but the layout is in sheet pixels —
    at 0.5x an unscaled delta sends a node twice as far as the cursor went."""
    from roscoe.cli.build_ui import PAGE

    assert "(ev.clientX - sx) / zoom" in PAGE
    assert "(ev.clientY - sy) / zoom" in PAGE


def test_the_canvas_can_be_zoomed_and_fitted():
    from roscoe.cli.build_ui import PAGE

    assert "function zoomFit()" in PAGE
    assert "function zoomBy(" in PAGE


def test_narrow_windows_get_smaller_rails_so_the_panel_is_not_clipped():
    from roscoe.cli.build_ui import PAGE

    assert "@media (max-width: 1100px)" in PAGE


# --- the page's own JavaScript ---


def test_the_pages_javascript_parses(tmp_path):
    """A syntax error anywhere in the inline script kills the entire editor —
    every button stops working, and the browser reports nothing visible in the
    page itself. Exactly that shipped once: a quote-escaping slip in a template
    string. Parse it here so it can never reach anyone.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to parse the page's JavaScript")

    from roscoe.cli.build_ui import PAGE

    script = PAGE[PAGE.index("<script>") + len("<script>"):PAGE.rindex("</script>")]
    path = tmp_path / "page.js"
    path.write_text(script, encoding="utf-8")

    result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


# --- the connector picker ---


def test_load_describes_every_connector_for_the_picker(tmp_path):
    """The Setup tab used to offer a bare type name and free-form key/value
    pairs, which only helps if you already know what keys that type wants."""
    payload = _project(tmp_path).load()

    catalog = {entry["type"]: entry for entry in payload["catalog"]}

    assert "smtp" in catalog and "web_search" in catalog and "rest_api" in catalog
    smtp = catalog["smtp"]
    assert smtp["label"] == "Email (SMTP)"
    assert smtp["blurb"]
    assert {f["name"] for f in smtp["fields"]} >= {"host", "username", "password"}


def test_a_connector_with_more_than_one_auth_option_offers_a_mode_chooser():
    """Jira/ServiceNow/Google Workspace each have a real second way to
    authenticate (OAuth vs. a token) — the picker has to expose both as an
    explicit choice, not merge every mode's fields into one confusing list."""
    from roscoe.connectors.catalog import CATALOG

    for type_name in ("jira", "servicenow", "google_workspace", "github", "rest_api"):
        spec = CATALOG[type_name]
        assert "auth_modes" in spec, f"{type_name} should offer an auth_modes chooser"
        assert "fields" not in spec, f"{type_name} should not also carry a flat fields list"
        modes = spec["auth_modes"]
        assert len(modes) >= 2
        keys = [m["key"] for m in modes]
        assert len(keys) == len(set(keys)), f"{type_name} has duplicate mode keys"
        for mode in modes:
            assert mode["label"]
            assert mode["fields"]


def test_every_catalogued_type_is_a_type_the_registry_can_build():
    """A catalogue entry for a type that doesn't exist would offer someone a
    connector that fails the moment they save it."""
    from roscoe.connectors.catalog import CATALOG
    from roscoe.workflow.registry import available_types

    unknown = set(CATALOG) - set(available_types())
    assert not unknown, f"catalogued but not buildable: {sorted(unknown)}"


def test_secret_fields_name_an_environment_variable_to_prefill():
    """Every secret is offered as ${VAR} so nobody types a real key into a form
    that gets written to a file they will commit."""
    from roscoe.connectors.catalog import CATALOG

    for type_name, spec in CATALOG.items():
        for mode_label, field in _all_fields(spec):
            if field["secret"] and field["required"]:
                assert field["env"], f"{type_name}{mode_label}.{field['name']} has no env var to prefill"


def _all_fields(spec):
    """Yield (mode label, field) for a catalog entry, whether it offers one
    flat field list or several selectable auth modes."""
    if "auth_modes" in spec:
        for mode in spec["auth_modes"]:
            for field in mode["fields"]:
                yield f" [{mode['key']}]", field
    else:
        for field in spec["fields"]:
            yield "", field
