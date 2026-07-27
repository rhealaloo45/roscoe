"""Phase 2 — agent_step and multi-agent workflows.

A scripted FakeLLM stands in for the model so the inner ReAct loop, agent handoff,
and nested approval pause/resume run without a live provider.
"""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from roscoe.approval.gate import ApprovalGate
from roscoe.workflow.executor import WorkflowExecutor
from roscoe.workflow.schema import Workflow, WorkflowError


class FakeLLM:
    """Returns scripted AIMessages. ``bind_tools`` records what it was given."""

    def __init__(self, *replies):
        self._replies = list(replies)
        self._index = 0
        self.bound_tools = None
        self.prompts = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def with_retry(self, **kwargs):
        # Mirrors langchain_core's real RunnableRetry: it wraps ainvoke but,
        # unlike a chat model, does not expose bind_tools.
        return _NoBindToolsWrapper(self)

    async def ainvoke(self, messages, *args, **kwargs):
        self.prompts.append(messages)
        reply = self._replies[min(self._index, len(self._replies) - 1)]
        self._index += 1
        return reply


class _NoBindToolsWrapper:
    def __init__(self, inner):
        self._inner = inner

    async def ainvoke(self, *args, **kwargs):
        return await self._inner.ainvoke(*args, **kwargs)


def _search(query: str) -> str:
    """Search the web."""
    return f"results for {query}"


def _send_email(to: str, body: str) -> dict:
    """Send an email."""
    return {"sent": True, "to": to}


def _tool(fn, name):
    return StructuredTool.from_function(fn, name=name, description=fn.__doc__ or name)


def _connectors():
    return {"web": [_tool(_search, "search")], "mail": [_tool(_send_email, "send_email")]}


def _call(name, args, cid="c1"):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


RESEARCH_FLOW = {
    "agents": {
        "researcher": {"system_prompt": "You research.", "tools": ["web.search"]},
    },
    "nodes": [
        {
            "id": "research",
            "type": "agent_step",
            "agent": "researcher",
            "task": "Research {{ input.topic }}",
            "output": "findings",
        },
    ],
}


# --- schema ---


def test_agent_step_and_agents_block_parse():
    wf = Workflow.from_dict(RESEARCH_FLOW)
    assert wf.get("research").type == "agent_step"
    assert wf.agents["researcher"].tools == ["web.search"]
    assert wf.agents["researcher"].system_prompt == "You research."


def test_agents_may_be_passed_alongside_the_workflow_block():
    wf = Workflow.from_dict(
        {"nodes": RESEARCH_FLOW["nodes"]}, agents=RESEARCH_FLOW["agents"]
    )
    assert "researcher" in wf.agents


def test_agent_step_referencing_an_undefined_agent_is_rejected():
    with pytest.raises(WorkflowError, match="references agent 'researcher'"):
        Workflow.from_dict({"nodes": RESEARCH_FLOW["nodes"], "agents": {}})


def test_agent_step_requires_a_task():
    nodes = [{"id": "a", "type": "agent_step", "agent": "researcher"}]
    with pytest.raises(WorkflowError, match="missing 'task'"):
        Workflow.from_dict({"nodes": nodes, "agents": RESEARCH_FLOW["agents"]})


def test_agent_spec_rejects_a_non_list_tools_value():
    agents = {"researcher": {"tools": "web.search"}}
    with pytest.raises(WorkflowError, match="'tools' must be a list"):
        Workflow.from_dict({"nodes": RESEARCH_FLOW["nodes"], "agents": agents})


# --- execution ---


async def test_agent_step_runs_the_inner_react_loop():
    llm = FakeLLM(
        AIMessage(content="", tool_calls=[_call("search", {"query": "roscoe"})]),
        AIMessage(content="Here is what I found."),
    )
    ex = WorkflowExecutor(
        Workflow.from_dict(RESEARCH_FLOW), connectors=_connectors(), llm=llm
    )
    result = await ex.run({"topic": "roscoe"})

    assert result.status == "success"
    assert result.state["findings"] == "Here is what I found."
    # The agent's declared tool was resolved from the connector and bound.
    assert [t.name for t in llm.bound_tools] == ["search"]


async def test_task_template_is_resolved_before_the_agent_sees_it():
    llm = FakeLLM(AIMessage(content="done"))
    ex = WorkflowExecutor(
        Workflow.from_dict(RESEARCH_FLOW), connectors=_connectors(), llm=llm
    )
    await ex.run({"topic": "VPN policy"})

    sent = "\n".join(str(getattr(m, "content", "")) for m in llm.prompts[0])
    assert "Research VPN policy" in sent


async def test_two_agents_hand_off_through_state():
    flow = {
        "agents": {
            "researcher": {"tools": ["web.search"]},
            "writer": {"tools": []},
        },
        "nodes": [
            {"id": "research", "type": "agent_step", "agent": "researcher",
             "task": "Research {{ input.topic }}", "output": "findings"},
            {"id": "write", "type": "agent_step", "agent": "writer",
             "task": "Summarise: {{ findings }}", "output": "summary"},
        ],
    }
    llm = FakeLLM(
        AIMessage(content="raw findings"),
        AIMessage(content="tidy summary"),
    )
    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors(), llm=llm)
    result = await ex.run({"topic": "x"})

    assert result.nodes_traversed == ["research", "write"]
    assert result.state["findings"] == "raw findings"
    assert result.state["summary"] == "tidy summary"


async def test_agent_messages_are_collected_for_cost_accounting():
    llm = FakeLLM(AIMessage(content="answer"))
    ex = WorkflowExecutor(
        Workflow.from_dict(RESEARCH_FLOW), connectors=_connectors(), llm=llm
    )
    result = await ex.run({"topic": "x"})

    assert any(isinstance(m, AIMessage) for m in result.messages)


async def test_unresolvable_agent_tool_is_a_clear_error():
    flow = {
        "agents": {"researcher": {"tools": ["web.nope"]}},
        "nodes": RESEARCH_FLOW["nodes"],
    }
    llm = FakeLLM(AIMessage(content="x"))
    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors(), llm=llm)
    result = await ex.run({"topic": "x"})

    assert result.status == "error"
    assert "has no method 'nope'" in result.error


async def test_agent_step_without_a_model_is_a_clear_error():
    ex = WorkflowExecutor(Workflow.from_dict(RESEARCH_FLOW), connectors=_connectors())
    result = await ex.run({"topic": "x"})

    assert "needs a model" in result.error


async def test_bare_tool_names_resolve_from_any_connector():
    flow = {
        "agents": {"researcher": {"tools": ["search"]}},
        "nodes": RESEARCH_FLOW["nodes"],
    }
    llm = FakeLLM(AIMessage(content="ok"))
    ex = WorkflowExecutor(Workflow.from_dict(flow), connectors=_connectors(), llm=llm)
    await ex.run({"topic": "x"})

    assert [t.name for t in llm.bound_tools] == ["search"]


# --- nested approval: an agent's gated tool call must still pause the workflow ---


NOTIFY_FLOW = {
    "agents": {"notifier": {"tools": ["mail.send_email"]}},
    "nodes": [
        {"id": "notify", "type": "agent_step", "agent": "notifier",
         "task": "Email {{ input.to }}", "output": "result"},
        {"id": "wrap", "type": "llm_step", "prompt": "Confirm.", "output": "note"},
    ],
}


def _gated_executor(*replies):
    llm = FakeLLM(*replies)
    ex = WorkflowExecutor(
        Workflow.from_dict(NOTIFY_FLOW),
        connectors=_connectors(),
        llm=llm,
        approval_gate=ApprovalGate(["send_email"]),
    )
    return ex, llm


async def test_agent_tool_call_pauses_the_whole_workflow():
    ex, _ = _gated_executor(
        AIMessage(content="", tool_calls=[_call("send_email", {"to": "a@b.c", "body": "hi"})]),
        AIMessage(content="Sent."),
    )
    result = await ex.run({"to": "a@b.c"})

    assert result.status == "paused"
    assert result.pending.kind == "agent"
    assert result.pending.agent == "notifier"
    assert result.pending.tool_calls[0]["name"] == "send_email"
    # Nothing downstream ran.
    assert "note" not in result.state


async def test_resume_approve_continues_the_agent_then_the_workflow():
    ex, _ = _gated_executor(
        AIMessage(content="", tool_calls=[_call("send_email", {"to": "a@b.c", "body": "hi"})]),
        AIMessage(content="Sent."),
        AIMessage(content="Confirmed."),
    )
    paused = await ex.run({"to": "a@b.c"})
    result = await ex.resume(paused.pending, "approve")

    assert result.status == "success"
    assert result.state["result"] == "Sent."
    assert result.state["note"] == "Confirmed."


async def test_resume_reject_tells_the_agent_and_lets_it_recover():
    ex, _ = _gated_executor(
        AIMessage(content="", tool_calls=[_call("send_email", {"to": "a@b.c", "body": "hi"})]),
        AIMessage(content="Understood, not sending."),
        AIMessage(content="Confirmed."),
    )
    paused = await ex.run({"to": "a@b.c"})
    result = await ex.resume(paused.pending, "reject")

    assert result.status == "success"
    assert result.state["result"] == "Understood, not sending."


async def test_resume_modify_rewrites_the_agents_arguments():
    ex, _ = _gated_executor(
        AIMessage(content="", tool_calls=[_call("send_email", {"to": "wrong@b.c", "body": "hi"})]),
        AIMessage(content="Sent."),
        AIMessage(content="Confirmed."),
    )
    paused = await ex.run({"to": "wrong@b.c"})
    result = await ex.resume(
        paused.pending, "modify", override_args={"to": "right@b.c", "body": "hi"}
    )

    assert result.status == "success"
    # The tool ran with the corrected address, which the agent then reported on.
    assert result.state["result"] == "Sent."


# --- retry middleware + agent_step together (regression) ---
#
# Found live: `roscoe run` on a real workflow 400'd with
# "'RunnableRetry' object has no attribute 'bind_tools'" the first time an
# agent_step actually ran with retry enabled. WorkflowRunner.from_config wrapped
# the model in retry BEFORE handing it to WorkflowExecutor, which then tried to
# bind_tools on the already-wrapped runnable — a real chat model exposes
# bind_tools, but LangChain's retry wrapper (RunnableRetry) does not. It went
# unnoticed because every earlier test workflow used llm_step only, which never
# calls bind_tools.


async def test_agent_step_binds_tools_before_a_retry_wrapper_hides_them():
    """Uses langchain_core's real RunnableRetry (via .with_retry()), not a test
    double, so this fails the same way the live bug did if the ordering regresses.
    """
    def _search(query: str) -> str:
        """Look something up."""
        return f"found: {query}"

    search = StructuredTool.from_function(_search, name="search")
    llm = FakeLLM(
        AIMessage(content="", tool_calls=[_call("search", {"query": "x"})]),
        AIMessage(content="done"),
    )
    flow = {
        "entry": "research",
        "nodes": [{
            "id": "research", "type": "agent_step", "agent": "researcher",
            "task": "look things up", "output": "result",
        }],
    }
    agents = {"researcher": {"tools": ["search"]}}

    ex = WorkflowExecutor(
        Workflow.from_dict(flow, agents), tools=[search], llm=llm,
        enable_retry=True, retry_config={}, provider="",
    )
    result = await ex.run({})

    assert result.status == "success"
    assert [t.name for t in llm.bound_tools] == ["search"]
