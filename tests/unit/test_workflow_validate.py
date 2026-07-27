"""Phase 3 — static validation, workflow loading, and the ``roscoe validate`` command.

These are the checks that answer "how do you catch a bad config before it runs" — the
gap YAML pipelines usually have, since there is no compiler between the file and
production.
"""

import textwrap

import pytest
from click.testing import CliRunner
from langchain_core.tools import StructuredTool

from roscoe.cli.main import cli
from roscoe.workflow.loader import has_workflow, load_workflow
from roscoe.workflow.registry import ConnectorError, build_connectors, get_connector_class
from roscoe.workflow.schema import Workflow, WorkflowError
from roscoe.workflow.validate import ERROR, WARNING, validate_workflow


def _lookup(employee_id: str) -> dict:
    """Look up an employee."""
    return {"id": employee_id}


def _connectors():
    return {"hr": [StructuredTool.from_function(_lookup, name="lookup", description="Look up.")]}


GOOD = {
    "nodes": [
        {"id": "a", "type": "connector_action", "connector": "hr", "method": "lookup",
         "inputs": {"employee_id": "{{ input.id }}"}, "output": "employee"},
        {"id": "b", "type": "llm_step", "prompt": "Greet {{ employee.id }}."},
    ]
}


# --- expression checking ---


def test_a_sound_workflow_reports_nothing():
    assert validate_workflow(Workflow.from_dict(GOOD), connectors=_connectors()) == []


def test_broken_expression_syntax_is_caught_before_running():
    flow = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "Hi {{ name ( }}"}]}
    issues = validate_workflow(Workflow.from_dict(flow))

    assert [i.level for i in issues] == [ERROR]
    assert issues[0].node == "a"


def test_disallowed_construct_in_a_template_is_caught():
    flow = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "{{ __import__('os') }}"}]}
    issues = validate_workflow(Workflow.from_dict(flow))

    assert any("Unknown function" in i.message for i in issues)


def test_condition_expressions_are_checked_too():
    flow = {
        "nodes": [
            {"id": "a", "type": "condition", "when": "x.upper()", "then": "b"},
            {"id": "b", "type": "llm_step", "prompt": "hi"},
        ]
    }
    issues = validate_workflow(Workflow.from_dict(flow))

    assert any("method calls are not" in i.message for i in issues)


def test_workflow_output_template_is_checked():
    flow = {**GOOD, "output": "{{ lambda: 1 }}"}
    issues = validate_workflow(Workflow.from_dict(flow))

    assert any("workflow.output" in i.message for i in issues)


def test_valid_expressions_using_state_are_not_flagged():
    # Unknown names can't be checked statically — only shape is.
    flow = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "{{ anything.at.all }}"}]}
    assert validate_workflow(Workflow.from_dict(flow)) == []


# --- reachability ---


def test_unreachable_node_is_a_warning():
    flow = {
        "entry": "a",
        "nodes": [
            {"id": "a", "type": "llm_step", "prompt": "hi", "next": "END"},
            {"id": "orphan", "type": "llm_step", "prompt": "never"},
        ],
    }
    issues = validate_workflow(Workflow.from_dict(flow))

    assert [(i.level, i.node) for i in issues] == [(WARNING, "orphan")]


def test_condition_without_else_warns_and_names_the_fall_through_target():
    flow = {
        "nodes": [
            {"id": "check", "type": "condition", "when": "true", "then": "yes"},
            {"id": "fallback", "type": "llm_step", "prompt": "f", "next": "END"},
            {"id": "yes", "type": "llm_step", "prompt": "y", "next": "END"},
        ]
    }
    issues = validate_workflow(Workflow.from_dict(flow))

    assert [i.level for i in issues] == [WARNING]
    assert "falls through to 'fallback'" in issues[0].message


def test_explicit_else_or_next_silences_the_fall_through_warning():
    base = [
        {"id": "check", "type": "condition", "when": "true", "then": "yes"},
        {"id": "fallback", "type": "llm_step", "prompt": "f", "next": "END"},
        {"id": "yes", "type": "llm_step", "prompt": "y", "next": "END"},
    ]
    with_else = [{**base[0], "else": "fallback"}, base[1], base[2]]
    with_next = [{**base[0], "next": "fallback"}, base[1], base[2]]

    assert validate_workflow(Workflow.from_dict({"nodes": with_else})) == []
    assert validate_workflow(Workflow.from_dict({"nodes": with_next})) == []


def test_nodes_reached_only_through_a_branch_are_not_flagged():
    flow = {
        "entry": "check",
        "nodes": [
            {"id": "check", "type": "condition", "when": "true", "then": "yes", "else": "no"},
            {"id": "yes", "type": "llm_step", "prompt": "y", "next": "END"},
            {"id": "no", "type": "llm_step", "prompt": "n", "next": "END"},
        ],
    }
    assert validate_workflow(Workflow.from_dict(flow)) == []


# --- connector-aware checks ---


def test_unknown_method_is_an_error():
    flow = {"nodes": [{"id": "a", "type": "connector_action", "connector": "hr",
                       "method": "ghost", "inputs": {}}]}
    issues = validate_workflow(Workflow.from_dict(flow), connectors=_connectors())

    assert any("has no method 'ghost'" in i.message for i in issues)


def test_missing_required_input_is_an_error():
    flow = {"nodes": [{"id": "a", "type": "connector_action", "connector": "hr",
                       "method": "lookup", "inputs": {}}]}
    issues = validate_workflow(Workflow.from_dict(flow), connectors=_connectors())

    assert any("requires input 'employee_id'" in i.message for i in issues)


def test_unexpected_input_is_a_warning():
    flow = {"nodes": [{"id": "a", "type": "connector_action", "connector": "hr",
                       "method": "lookup",
                       "inputs": {"employee_id": "x", "typo": "y"}}]}
    issues = validate_workflow(Workflow.from_dict(flow), connectors=_connectors())

    assert any(i.level == WARNING and "typo" in i.message for i in issues)


def test_connector_checks_are_skipped_without_connectors():
    flow = {"nodes": [{"id": "a", "type": "connector_action", "connector": "ghost",
                       "method": "x", "inputs": {}}]}
    assert validate_workflow(Workflow.from_dict(flow)) == []


def test_agent_tool_references_are_checked():
    flow = {
        "agents": {"r": {"tools": ["hr.ghost"]}},
        "nodes": [{"id": "a", "type": "agent_step", "agent": "r", "task": "go"}],
    }
    issues = validate_workflow(Workflow.from_dict(flow), connectors=_connectors())

    assert any("has no method 'ghost'" in i.message for i in issues)


# --- connector registry ---


def test_registry_resolves_known_types():
    from roscoe.connectors import NotionConnector

    assert get_connector_class("notion") is NotionConnector


def test_registry_rejects_unknown_types():
    with pytest.raises(ConnectorError, match="Unknown connector type"):
        get_connector_class("nope")


def test_build_connectors_uses_an_explicit_type_when_given():
    built = build_connectors({"my_api": {"type": "rest_api", "base_url": "https://x.test"}})
    assert "my_api" in built


def test_build_connectors_names_the_one_that_failed():
    with pytest.raises(ConnectorError, match="notion"):
        build_connectors({"notion": {}})  # missing required token


# --- loading ---


def _write_project(tmp_path, config_text, workflow_text=None):
    (tmp_path / "agent_config.yaml").write_text(textwrap.dedent(config_text))
    if workflow_text is not None:
        (tmp_path / "workflow.yaml").write_text(textwrap.dedent(workflow_text))
    return tmp_path / "agent_config.yaml"


CONFIG = """
    agent_name: demo
    model:
      provider: ollama
      model: llama3.1
"""


def test_loads_a_workflow_embedded_in_the_agent_config(tmp_path):
    config = _write_project(tmp_path, CONFIG + """
    workflow:
      nodes:
        - id: a
          type: llm_step
          prompt: hi
    """)
    workflow, cfg = load_workflow(config)

    assert [n.id for n in workflow.nodes] == ["a"]
    assert cfg["agent_name"] == "demo"


def test_loads_a_sibling_workflow_file(tmp_path):
    config = _write_project(tmp_path, CONFIG, """
    workflow:
      nodes:
        - id: a
          type: llm_step
          prompt: hi
    """)
    workflow, _ = load_workflow(config)
    assert [n.id for n in workflow.nodes] == ["a"]


def test_sibling_workflow_file_may_omit_the_workflow_key(tmp_path):
    config = _write_project(tmp_path, CONFIG, """
    nodes:
      - id: a
        type: llm_step
        prompt: hi
    """)
    workflow, _ = load_workflow(config)
    assert [n.id for n in workflow.nodes] == ["a"]


def test_agents_defined_in_the_config_reach_a_sibling_workflow(tmp_path):
    config = _write_project(tmp_path, CONFIG + """
    agents:
      r:
        tools: []
    """, """
    nodes:
      - id: a
        type: agent_step
        agent: r
        task: go
    """)
    workflow, _ = load_workflow(config)
    assert "r" in workflow.agents


def test_missing_workflow_says_where_to_put_one(tmp_path):
    config = _write_project(tmp_path, CONFIG)
    with pytest.raises(WorkflowError, match="No workflow found"):
        load_workflow(config)


def test_has_workflow_detects_both_locations(tmp_path):
    plain = _write_project(tmp_path, CONFIG)
    assert has_workflow(plain) is False

    embedded = _write_project(tmp_path, CONFIG + """
    workflow:
      nodes:
        - id: a
          type: llm_step
          prompt: hi
    """)
    assert has_workflow(embedded) is True


def test_has_workflow_is_quiet_about_unreadable_configs(tmp_path):
    assert has_workflow(tmp_path / "nothing.yaml") is False


# --- CLI ---


def test_validate_command_reports_a_clean_workflow(tmp_path):
    _write_project(tmp_path, CONFIG + """
    workflow:
      nodes:
        - id: a
          type: llm_step
          prompt: hi
    """)
    result = CliRunner().invoke(
        cli, ["validate", "--config", str(tmp_path / "agent_config.yaml")]
    )

    assert result.exit_code == 0, result.output
    assert "No problems found" in result.output


def test_validate_command_exits_non_zero_on_an_error(tmp_path):
    _write_project(tmp_path, CONFIG + """
    workflow:
      nodes:
        - id: a
          type: llm_step
          prompt: "{{ lambda: 1 }}"
    """)
    result = CliRunner().invoke(
        cli, ["validate", "--config", str(tmp_path / "agent_config.yaml")]
    )

    assert result.exit_code == 1
    assert "error" in result.output


def test_validate_command_reports_structural_problems(tmp_path):
    _write_project(tmp_path, CONFIG + """
    workflow:
      nodes:
        - id: a
          type: nonsense
    """)
    result = CliRunner().invoke(
        cli, ["validate", "--config", str(tmp_path / "agent_config.yaml")]
    )

    assert result.exit_code != 0
    assert "unknown type" in result.output


def test_validate_command_warns_but_passes_on_warnings_only(tmp_path):
    _write_project(tmp_path, CONFIG + """
    workflow:
      entry: a
      nodes:
        - id: a
          type: llm_step
          prompt: hi
          next: END
        - id: orphan
          type: llm_step
          prompt: never
    """)
    result = CliRunner().invoke(
        cli, ["validate", "--config", str(tmp_path / "agent_config.yaml")]
    )

    assert result.exit_code == 0
    assert "warning" in result.output
