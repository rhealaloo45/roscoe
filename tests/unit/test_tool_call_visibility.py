"""on_tool_call — a live signal that a sub-agent's inner loop is actually
doing something, not hung, during a slow or failing tool call.

Regression context: a workflow with agent_step nodes could sit for many
minutes with zero visibility into what was happening — a misconfigured
connector made every tool call fail, and the inner ReAct loop just kept
retrying up to max_iterations with nothing printed anywhere. This tests the
callback plumbing (ReactExecutor -> WorkflowExecutor -> WorkflowRunner) that
now surfaces every tool call as it happens.
"""

import asyncio

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from roscoe.core.executor import ReactExecutor
from roscoe.workflow.executor import WorkflowExecutor
from roscoe.workflow.runner import WorkflowRunner
from roscoe.workflow.schema import Workflow


class FakeModel:
    """Returns scripted AIMessages on each ``ainvoke``."""

    def __init__(self, replies):
        self._replies = list(replies)
        self._i = 0

    async def ainvoke(self, messages, *args, **kwargs):
        reply = self._replies[self._i]
        self._i += 1
        return reply

    def bind_tools(self, tools):
        return self


def _call(name, args=None, cid="c1"):
    return {"name": name, "args": args or {}, "id": cid, "type": "tool_call"}


def _ok_tool():
    def ping(x: str) -> str:
        """Ping."""
        return f"pong: {x}"
    return StructuredTool.from_function(ping, name="ping", description="Ping.")


def _failing_tool():
    def boom(x: str) -> str:
        """Always fails."""
        raise RuntimeError("connector exploded")
    return StructuredTool.from_function(boom, name="boom", description="Always fails.")


# --- ReactExecutor: the raw callback ---


def test_a_successful_tool_call_fires_start_then_done():
    events = []
    model = FakeModel([
        AIMessage(content="", tool_calls=[_call("ping", {"x": "hi"})]),
        AIMessage(content="done"),
    ])
    executor = ReactExecutor(model, [_ok_tool()], on_tool_call=events.append)

    asyncio.run(executor.run([]))

    assert [e["phase"] for e in events] == ["start", "done"]
    assert events[0]["name"] == "ping"
    assert events[0]["args"] == {"x": "hi"}
    assert "pong: hi" in events[1]["summary"]
    assert events[1]["seconds"] >= 0


def test_a_failing_tool_call_fires_start_then_error():
    events = []
    model = FakeModel([
        AIMessage(content="", tool_calls=[_call("boom", {"x": "hi"})]),
        AIMessage(content="gave up"),
    ])
    executor = ReactExecutor(model, [_failing_tool()], on_tool_call=events.append)

    asyncio.run(executor.run([]))

    assert [e["phase"] for e in events] == ["start", "error"]
    assert "connector exploded" in events[1]["summary"]


def test_a_long_summary_is_truncated():
    events = []

    def verbose(x: str) -> str:
        """Verbose."""
        return "x" * 500
    tool = StructuredTool.from_function(verbose, name="verbose", description="Verbose.")
    model = FakeModel([
        AIMessage(content="", tool_calls=[_call("verbose")]),
        AIMessage(content="done"),
    ])
    executor = ReactExecutor(model, [tool], on_tool_call=events.append)

    asyncio.run(executor.run([]))

    assert len(events[1]["summary"]) <= 203  # 200 + the ellipsis
    assert events[1]["summary"].endswith("…")


def test_no_callback_configured_is_a_silent_no_op():
    """The default (on_tool_call=None) must not need any special-casing by a
    caller that doesn't care about this."""
    model = FakeModel([
        AIMessage(content="", tool_calls=[_call("ping", {"x": "hi"})]),
        AIMessage(content="done"),
    ])
    executor = ReactExecutor(model, [_ok_tool()])

    result = asyncio.run(executor.run([]))

    assert result.status == "success"


# --- WorkflowExecutor: forwarded with the agent's name attached ---


FLOW = {
    "entry": "step",
    "nodes": [
        {"id": "step", "type": "agent_step", "agent": "helper", "task": "go",
         "output": "out", "next": "END"},
    ],
}


def test_workflow_executor_forwards_tool_calls_tagged_with_the_agent_name():
    events = []
    model = FakeModel([
        AIMessage(content="", tool_calls=[_call("ping", {"x": "hi"})]),
        AIMessage(content="done"),
    ])
    wf = Workflow.from_dict(FLOW, agents={"helper": {"tools": ["ping"]}})
    executor = WorkflowExecutor(
        wf, connectors={}, llm=model, tools=[_ok_tool()],
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    executor.on_tool_call = lambda agent_name, event: events.append((agent_name, event))

    asyncio.run(executor.run({}))

    assert events[0][0] == "helper"
    assert events[0][1]["phase"] == "start"


def test_setting_on_tool_call_after_the_agent_is_already_cached_still_works():
    """The forwarder reads WorkflowExecutor.on_tool_call at call time, not at
    the moment the (cached) ReactExecutor was built — a host wires this up
    once per run, which can be after the first agent_step already ran once
    in the same executor's lifetime (e.g. a resumed workflow)."""
    events = []
    model = FakeModel([
        AIMessage(content="", tool_calls=[_call("ping", {"x": "hi"})]),
        AIMessage(content="done"),
    ])
    wf = Workflow.from_dict(FLOW, agents={"helper": {"tools": ["ping"]}})
    executor = WorkflowExecutor(
        wf, connectors={}, llm=model, tools=[_ok_tool()],
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    # Force the ReactExecutor to be built (and cached) before on_tool_call is set.
    executor._agent_executor("helper")  # noqa: SLF001 — reaching in on purpose for the test

    executor.on_tool_call = lambda agent_name, event: events.append((agent_name, event))
    asyncio.run(executor.run({}))

    assert events, "callback set after caching should still fire"


# --- WorkflowRunner: the public wiring a host actually uses ---


def test_workflow_runner_set_on_tool_call_wires_through_to_the_executor():
    wf = Workflow.from_dict(FLOW, agents={"helper": {"tools": ["ping"]}})
    executor = WorkflowExecutor(
        wf, connectors={}, llm=FakeModel([AIMessage(content="done")]), tools=[_ok_tool()],
        approval_gate=None, enable_retry=False, retry_config=None, provider="openai",
    )
    runner = WorkflowRunner(
        workflow=wf, executor=executor, config={}, agent_name="demo",
        provider="openai", model="m", rate_limiter=_NoLimiter(),
    )

    seen = []
    runner.set_on_tool_call(seen.append)

    # Bound methods aren't interned, so `is` on two separately-taken
    # `seen.append` references would fail even though they're equal —
    # `==` is the correct check for "did this get wired to the right place".
    assert executor.on_tool_call == seen.append


class _NoLimiter:
    async def acquire(self, provider):
        return None
