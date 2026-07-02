"""ReactExecutor — roscoe's own async tool-calling loop (no LangGraph).

The loop is deliberately small and explicit:

    1. call the model with the running message list
    2. no ``tool_calls`` on the reply  -> done, return the final answer
    3. a tool call is gated by the approval gate -> **stop**, return ``paused``
    4. otherwise run each tool, append the results, go to 1

Because the loop is ours, human-in-the-loop is just an early ``return`` (step 3) instead
of LangGraph's ``interrupt()`` + checkpointer. ``AgentRunner.resume()`` feeds the held
tool call(s) back in and re-enters the loop at step 1.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Sequence

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from roscoe.approval.gate import ApprovalGate


@dataclass
class ExecResult:
    """Outcome of running (or resuming) the loop."""

    messages: list[Any]
    status: str = "success"  # "success" | "paused" | "error"
    pending_tool_calls: list[dict[str, Any]] | None = None
    error: str | None = None


class ReactExecutor:
    """Runs a tool-bound chat model in a ReAct loop with optional approval gating."""

    def __init__(
        self,
        model: Any,
        tools: Sequence[BaseTool],
        *,
        system_prompt: str | None = None,
        max_iterations: int = 10,
        approval_gate: ApprovalGate | None = None,
        tool_timeout: float | None = None,
        max_tool_failures: int | None = None,
    ) -> None:
        self._model = model  # already tool-bound (and retry-wrapped)
        self._tools: dict[str, BaseTool] = {t.name: t for t in tools}
        self._system = system_prompt
        self._max_iterations = max_iterations
        self._gate = approval_gate
        # Per-tool guardrails: hard timeout on a single call, and an error budget
        # so a tool that keeps failing gets short-circuited instead of retried
        # every iteration until max_iterations is exhausted.
        self._tool_timeout = tool_timeout
        self._max_tool_failures = max_tool_failures
        self._failures: dict[str, int] = {}

    async def run(self, messages: Sequence[Any]) -> ExecResult:
        """Start a fresh run from ``messages`` (history + the new human turn)."""
        convo: list[Any] = list(messages)
        if self._system and not (convo and isinstance(convo[0], SystemMessage)):
            convo.insert(0, SystemMessage(content=self._system))
        return await self._loop(convo)

    async def resume(self, convo: list[Any]) -> ExecResult:
        """Continue the loop after the caller has appended ToolMessage(s) for the
        previously-held tool calls."""
        return await self._loop(convo)

    async def astream(self, messages: Sequence[Any]) -> AsyncIterator[dict[str, Any]]:
        """Run the loop, streaming events as they happen.

        Yields dicts tagged by ``type``:
          - ``{"type": "token", "text": ...}``   — a piece of the model's answer
          - ``{"type": "tool", "name": ...}``    — a tool is about to run
          - ``{"type": "done", "result": ExecResult}`` — terminal (success/paused/error)

        Tokens are emitted live as the model produces them. Tool-calling turns
        usually carry no visible text, so nothing spurious is shown before a tool runs.
        """
        convo: list[Any] = list(messages)
        if self._system and not (convo and isinstance(convo[0], SystemMessage)):
            convo.insert(0, SystemMessage(content=self._system))

        for _ in range(self._max_iterations):
            gathered: Any = None
            async for chunk in self._model.astream(convo):
                gathered = chunk if gathered is None else gathered + chunk
                text = getattr(chunk, "content", "")
                if isinstance(text, str) and text:
                    yield {"type": "token", "text": text}

            reply = gathered
            convo.append(reply)

            tool_calls = getattr(reply, "tool_calls", None) or []
            if not tool_calls:
                yield {"type": "done",
                       "result": ExecResult(messages=convo, status="success")}
                return

            if self._gate is not None and any(
                self._gate.needs_approval(c["name"]) for c in tool_calls
            ):
                yield {"type": "done", "result": ExecResult(
                    messages=convo, status="paused", pending_tool_calls=tool_calls)}
                return

            for call in tool_calls:
                yield {"type": "tool", "name": call["name"], "args": call.get("args", {})}
                convo.append(await self.exec_tool_call(call))

        yield {"type": "done", "result": ExecResult(
            messages=convo, status="error",
            error=f"Max iterations ({self._max_iterations}) reached without a final answer.")}

    async def _loop(self, convo: list[Any]) -> ExecResult:
        for _ in range(self._max_iterations):
            reply: AIMessage = await self._model.ainvoke(convo)
            convo.append(reply)

            tool_calls = getattr(reply, "tool_calls", None) or []
            if not tool_calls:
                return ExecResult(messages=convo, status="success")

            # Gate the whole batch: if any call needs approval, pause before running any.
            if self._gate is not None and any(
                self._gate.needs_approval(c["name"]) for c in tool_calls
            ):
                return ExecResult(
                    messages=convo, status="paused", pending_tool_calls=tool_calls
                )

            for call in tool_calls:
                convo.append(await self.exec_tool_call(call))

        return ExecResult(
            messages=convo,
            status="error",
            error=f"Max iterations ({self._max_iterations}) reached without a final answer.",
        )

    async def exec_tool_call(
        self, call: dict[str, Any], *, override_args: dict[str, Any] | None = None
    ) -> ToolMessage:
        """Execute one tool call and wrap the result as a ToolMessage.

        ``override_args`` supports the ``modify`` approval decision (run with edited
        arguments). A missing tool, a timeout, or an error inside the tool is returned
        as an error ToolMessage so the model can react rather than crashing the run.
        Timeouts and errors count against the tool's error budget; once a tool exceeds
        ``max_tool_failures`` it is refused for the rest of the run.
        """
        name = call["name"]
        args = override_args if override_args is not None else call.get("args", {})
        tool = self._tools.get(name)

        if tool is None:
            content: Any = f"Error: no tool named '{name}' is registered."
        elif self._budget_exhausted(name):
            content = (
                f"Error: tool '{name}' is disabled for this run after "
                f"{self._failures[name]} failures. Do not call it again."
            )
        else:
            try:
                coro = tool.ainvoke(args)
                if self._tool_timeout is not None:
                    content = await asyncio.wait_for(coro, timeout=self._tool_timeout)
                else:
                    content = await coro
            except asyncio.TimeoutError:
                self._failures[name] = self._failures.get(name, 0) + 1
                content = (
                    f"Error: tool '{name}' timed out after {self._tool_timeout:g}s."
                )
            except Exception as exc:  # noqa: BLE001 — surface tool failures to the model
                self._failures[name] = self._failures.get(name, 0) + 1
                content = f"Error running '{name}': {type(exc).__name__}: {exc}"

        return ToolMessage(
            content=content if isinstance(content, str) else str(content),
            tool_call_id=call.get("id", ""),
            name=name,
        )

    def _budget_exhausted(self, name: str) -> bool:
        """True if ``name`` has already used up its error budget for this run."""
        if self._max_tool_failures is None:
            return False
        return self._failures.get(name, 0) >= self._max_tool_failures

    @staticmethod
    def rejection_message(call: dict[str, Any]) -> ToolMessage:
        """ToolMessage used when a human rejects a gated tool call."""
        return ToolMessage(
            content=f"Action '{call['name']}' was rejected by a human approver and was not run.",
            tool_call_id=call.get("id", ""),
            name=call["name"],
        )
