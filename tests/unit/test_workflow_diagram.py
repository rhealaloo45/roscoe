"""Workflow visualisation — Mermaid generation and the ``roscoe graph`` command.

The diagram is how a workflow gets reviewed by someone who won't read YAML, so it
has to stay faithful: every node drawn, every edge drawn, and approval gates and
implicit fall-throughs visibly different from ordinary steps.
"""

import textwrap

from click.testing import CliRunner

from roscoe.cli.main import cli
from roscoe.workflow.diagram import to_mermaid
from roscoe.workflow.schema import Workflow

FLOW = {
    "entry": "lookup",
    "nodes": [
        {"id": "lookup", "type": "connector_action", "connector": "hrdb",
         "method": "query", "inputs": {}, "output": "rows"},
        {"id": "check", "type": "condition", "when": "len(rows) > 0",
         "then": "grant", "else": "deny"},
        {"id": "grant", "type": "connector_action", "connector": "hrdb",
         "method": "execute", "inputs": {}, "requires_approval": True,
         "next": "done", "on_reject": "deny"},
        {"id": "done", "type": "llm_step", "prompt": "Confirm it.", "next": "END"},
        {"id": "deny", "type": "llm_step", "prompt": "Explain the refusal."},
    ],
}


def _mermaid(flow=FLOW):
    return to_mermaid(Workflow.from_dict(flow))


def test_every_node_appears():
    output = _mermaid()
    for node_id in ("lookup", "check", "grant", "done", "deny"):
        assert node_id in output
    assert output.startswith("flowchart TD")


def test_shape_distinguishes_node_types():
    output = _mermaid()

    assert 'lookup["' in output       # connector_action — rectangle
    assert 'check{"' in output        # condition — diamond
    assert 'done("' in output         # llm_step — rounded


def test_condition_branches_are_labelled():
    output = _mermaid()

    assert "check -->|yes| grant" in output
    assert "check -->|no| deny" in output


def test_approval_branches_show_both_outcomes():
    output = _mermaid()

    assert "grant -->|approved| done" in output
    assert "grant -->|rejected| deny" in output


def test_gated_nodes_are_marked_and_styled():
    output = _mermaid()

    assert "&#128274;" in output          # padlock in the label
    assert "class grant gated" in output


def test_entry_node_is_highlighted():
    assert "class lookup entry" in _mermaid()


def test_implicit_fall_through_is_drawn_as_a_dotted_edge():
    # `lookup` has no `next`, so it falls through to whatever is next in the file.
    assert "lookup -.-> check" in _mermaid()


def test_end_is_renamed_since_it_is_reserved():
    output = _mermaid()

    assert 'END_["end"]' in output
    assert "done --> END_" in output


def test_labels_escape_characters_that_would_break_mermaid():
    flow = {
        "nodes": [
            {"id": "a", "type": "condition", "when": "x[0].y == \"q\" | z", "then": "a"},
        ]
    }
    output = to_mermaid(Workflow.from_dict(flow))

    # The raw characters would close the label early or be read as an edge label.
    assert '"' not in output.split("\n")[1].replace('a{"', "").replace('"}', "")
    assert "&#91;" in output and "&#124;" in output


def test_long_expressions_are_clipped():
    flow = {
        "nodes": [
            {"id": "a", "type": "llm_step", "prompt": "word " * 60},
        ]
    }
    line = [ln for ln in to_mermaid(Workflow.from_dict(flow)).split("\n") if ln.strip().startswith("a(")][0]

    assert "…" in line
    assert len(line) < 160


def test_agent_step_shows_which_agent_runs():
    flow = {
        "agents": {"researcher": {"tools": []}},
        "nodes": [{"id": "a", "type": "agent_step", "agent": "researcher", "task": "go"}],
    }
    output = to_mermaid(Workflow.from_dict(flow))

    assert "agent: researcher" in output
    assert 'a([' in output  # stadium shape


# --- CLI ---


CONFIG = """
    agent_name: demo
    model:
      provider: ollama
      model: llama3.1
    workflow:
      nodes:
        - id: a
          type: llm_step
          prompt: hi
"""


def _project(tmp_path):
    path = tmp_path / "agent_config.yaml"
    path.write_text(textwrap.dedent(CONFIG))
    return str(path)


def test_graph_terminal_prints_mermaid(tmp_path):
    result = CliRunner().invoke(cli, ["graph", "--config", _project(tmp_path), "--terminal"])

    assert result.exit_code == 0, result.output
    assert "flowchart TD" in result.output


def test_graph_writes_a_file(tmp_path):
    out = tmp_path / "flow.mmd"
    result = CliRunner().invoke(
        cli, ["graph", "--config", _project(tmp_path), "--output", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert "flowchart TD" in out.read_text()


def test_graph_reports_a_project_with_no_workflow(tmp_path):
    (tmp_path / "agent_config.yaml").write_text("agent_name: d\nmodel:\n  provider: ollama\n  model: x\n")
    result = CliRunner().invoke(
        cli, ["graph", "--config", str(tmp_path / "agent_config.yaml"), "--terminal"]
    )

    assert result.exit_code != 0
    assert "No workflow found" in result.output
