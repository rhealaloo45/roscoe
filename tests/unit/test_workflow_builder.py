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
