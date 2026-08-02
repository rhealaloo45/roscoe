"""The visual builder's server side — loading, validating, and saving a workflow.

Saving overwrites a real file, so most of these are about what must *survive* it.
A builder that quietly drops a setting is worse than no builder at all.
"""

import json
import textwrap

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
