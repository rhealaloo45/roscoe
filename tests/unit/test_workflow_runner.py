"""Phase 4 — WorkflowRunner, the `init-nc` scaffold, and `roscoe run` routing.

WorkflowRunner returns the same ``AgentResult`` as ``AgentRunner``, which is what
lets the CLI and the browser chat drive a workflow without a special case. These
tests pin that contract.
"""

import json
import textwrap

import pytest
from click.testing import CliRunner
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from roscoe.cli.init_command import scaffold_workflow_project
from roscoe.cli.main import cli
from roscoe.workflow.executor import WorkflowExecutor
from roscoe.workflow.runner import WorkflowRunner, _pending_action
from roscoe.workflow.schema import Workflow


class FakeLLM:
    def __init__(self, *replies):
        self._replies = list(replies) or [AIMessage(content="ok")]
        self._index = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, *args, **kwargs):
        reply = self._replies[min(self._index, len(self._replies) - 1)]
        self._index += 1
        return reply


def _grant(employee_id: str) -> str:
    """Grant access."""
    return f"granted {employee_id}"


def _runner(workflow_dict, *, llm=None, gate=None, connectors=None):
    """Build a runner around a hand-made executor, skipping config/network."""
    workflow = Workflow.from_dict(workflow_dict)
    executor = WorkflowExecutor(
        workflow, connectors=connectors or {}, llm=llm or FakeLLM(), approval_gate=gate
    )
    from roscoe.middleware.rate_limiter import RateLimiter

    return WorkflowRunner(
        workflow=workflow,
        executor=executor,
        config={"middleware": {"audit": {"enabled": False}}},
        agent_name="test-wf",
        provider="ollama",
        model="llama3.1",
        rate_limiter=RateLimiter(),
    )


SIMPLE = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "Say hi to {{ input.name }}.",
                     "output": "greeting"}]}

GATED = {
    "nodes": [
        {"id": "grant", "type": "connector_action", "connector": "vpn", "method": "grant",
         "inputs": {"employee_id": "{{ input.id }}"}, "requires_approval": True,
         "output": "result"},
    ]
}


# --- AgentResult contract ---


def test_run_returns_an_agent_result():
    result = _runner(SIMPLE, llm=FakeLLM(AIMessage(content="hello"))).run({"name": "Rhea"})

    assert result.status == "success"
    assert result.output == "hello"
    assert result.nodes_traversed == ["a"]
    assert result.run_id


def test_a_string_input_arrives_as_input_message():
    flow = {"nodes": [{"id": "a", "type": "llm_step", "prompt": "{{ input.message }}",
                       "output": "echo"}]}
    result = _runner(flow, llm=FakeLLM(AIMessage(content="seen"))).run("hello there")

    assert result.status == "success"


def test_failures_come_back_as_an_error_result_naming_the_node():
    flow = {"nodes": [{"id": "boom", "type": "condition", "when": "missing.x", "then": "boom"}]}
    result = _runner(flow).run({})

    assert result.status == "error"
    assert "boom" in result.error


# --- approval ---


def _vpn_connector():
    return {"vpn": [StructuredTool.from_function(_grant, name="grant", description="Grant.")]}


def test_paused_run_reports_the_held_action_like_a_tool_call():
    runner = _runner(GATED, connectors=_vpn_connector())
    result = runner.run({"id": "E-1"})

    assert result.status == "paused"
    # An approval UI reads pending_action["tool_calls"] — same shape either way.
    call = result.pending_action["tool_calls"][0]
    assert call["name"] == "vpn.grant"
    assert call["args"] == {"employee_id": "E-1"}
    assert result.pending_action["kind"] == "connector"


def test_resume_approve_completes_the_run():
    runner = _runner(GATED, connectors=_vpn_connector())
    paused = runner.run({"id": "E-1"})
    result = runner.resume(paused.run_id, "approve")

    assert result.status == "success"
    assert result.output == "granted E-1"


def test_resume_modify_passes_new_arguments_through():
    runner = _runner(GATED, connectors=_vpn_connector())
    paused = runner.run({"id": "E-1"})
    result = runner.resume(paused.run_id, "modify", payload={"employee_id": "E-9"})

    assert result.output == "granted E-9"


def test_resume_rejects_an_unknown_decision_and_an_unknown_run():
    runner = _runner(GATED, connectors=_vpn_connector())
    paused = runner.run({"id": "E-1"})

    with pytest.raises(ValueError, match="decision must be"):
        runner.resume(paused.run_id, "maybe")
    with pytest.raises(KeyError):
        runner.resume("not-a-run", "approve")


def test_pending_action_maps_an_agent_pause_to_its_tool_calls():
    from roscoe.workflow.executor import PendingNode

    pending = PendingNode(
        node_id="n", kind="agent", agent="researcher",
        tool_calls=[{"name": "send_email", "args": {"to": "a@b.c"}, "id": "c1"}],
    )
    action = _pending_action("run-1", pending)

    assert action["kind"] == "agent"
    assert action["tool_calls"][0]["name"] == "send_email"


# --- scaffold ---


def test_init_nc_scaffolds_a_python_free_project(tmp_path):
    dest = scaffold_workflow_project("demo", dest_dir=tmp_path)

    assert (dest / "agent_config.yaml").is_file()
    assert (dest / "workflow.yaml").is_file()
    assert (dest / "docs.md").is_file()
    # The whole point: no Python to write.
    assert not (dest / "tools").exists()
    assert not (dest / "main.py").exists()
    assert "demo" in (dest / "agent_config.yaml").read_text()


def test_init_nc_scaffold_is_valid_out_of_the_box(tmp_path):
    dest = scaffold_workflow_project("demo", dest_dir=tmp_path)
    result = CliRunner().invoke(
        cli, ["validate", "--config", str(dest / "agent_config.yaml")]
    )

    assert result.exit_code == 0, result.output
    assert "No problems found" in result.output


def test_init_nc_refuses_to_overwrite(tmp_path):
    scaffold_workflow_project("demo", dest_dir=tmp_path)
    with pytest.raises(FileExistsError):
        scaffold_workflow_project("demo", dest_dir=tmp_path)


def test_init_nc_command_reports_next_steps():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["init-nc", "demo"])

        assert result.exit_code == 0, result.output
        assert "roscoe validate" in result.output


# --- run routing ---


def test_run_uses_the_workflow_path_when_one_is_defined(tmp_path, monkeypatch):
    (tmp_path / "agent_config.yaml").write_text(textwrap.dedent("""
        agent_name: demo
        model:
          provider: ollama
          model: llama3.1
        workflow:
          nodes:
            - id: a
              type: llm_step
              prompt: hi
              output: out
    """))

    seen = {}

    class _Stub:
        provider, model, agent_name = "ollama", "llama3.1", "demo"
        workflow = type("W", (), {"nodes": [1]})()

        def run(self, text, **kwargs):
            seen["input"] = text
            from roscoe.core.agent_result import AgentResult

            return AgentResult(output="done", run_id="r1")

    monkeypatch.setattr(WorkflowRunner, "from_config", classmethod(lambda cls, *a, **k: _Stub()))

    result = CliRunner().invoke(
        cli, ["run", "--config", str(tmp_path / "agent_config.yaml"), "--set", "topic=x"]
    )

    assert result.exit_code == 0, result.output
    assert seen["input"] == {"topic": "x"}  # --set became the workflow's inputs
    assert "done" in result.output


def test_run_rejects_a_malformed_set_value(tmp_path):
    (tmp_path / "agent_config.yaml").write_text("agent_name: d\nmodel:\n  provider: ollama\n  model: x\n")
    result = CliRunner().invoke(
        cli, ["run", "--config", str(tmp_path / "agent_config.yaml"), "--set", "oops"]
    )

    assert result.exit_code != 0
    assert "KEY=VALUE" in result.output
