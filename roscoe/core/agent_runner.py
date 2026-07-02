"""AgentRunner — the main entry point, with middleware wired in.

``from_config()`` builds a provider LLM (tools bound, retry applied), roscoe's own
ReAct executor (``roscoe.core.executor``), and the per-run middleware stack. The core
is **async-first**: ``arun()`` does the work; ``run()`` is a thin ``asyncio.run`` wrapper.

Every run automatically gets: rate limiting (before the call), retry (around each LLM
call), cost tracking (after), and a non-blocking audit record — with zero extra code
from the developer.

Human-in-the-loop (Phase 6) is langgraph-free: if the config lists tools under
``middleware.human_approval.require_approval_for``, the loop stops before running such a
tool and returns a ``paused`` result. Call ``resume(run_id, decision)`` to continue.
"""

from __future__ import annotations

import asyncio
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from roscoe.approval.gate import ApprovalGate, PendingRun, PendingStore
from roscoe.config.loader import load_config
from roscoe.core.agent_result import AgentResult
from roscoe.core.executor import ExecResult, ReactExecutor
from roscoe.llm.provider_factory import ProviderFactory
from roscoe.memory.conversation import ConversationMemory
from roscoe.memory.persistent import PersistentMemory
from roscoe.middleware.audit_logger import get_audit_logger
from roscoe.middleware.cost_tracker import calculate_cost, sum_usage
from roscoe.middleware.rate_limiter import RateLimiter
from roscoe.middleware.retry import apply_retry

#: Approval decisions accepted by ``resume()``.
_DECISIONS = {"approve", "reject", "modify"}


class AgentRunner:
    """Builds and runs a configured agent with the full middleware stack."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        executor: ReactExecutor,
        agent_name: str,
        provider: str,
        model: str,
        middleware: dict[str, Any],
        rate_limiter: RateLimiter,
        conversation: ConversationMemory | None = None,
        persistent: PersistentMemory | None = None,
        pending_store: PendingStore | None = None,
    ) -> None:
        self.config = config
        self._executor = executor
        self.agent_name = agent_name
        self.provider = provider
        self.model = model
        self._mw = middleware
        self._rate_limiter = rate_limiter
        self._conversation = conversation
        self._persistent = persistent
        self._pending = pending_store or PendingStore()
        self._audit = get_audit_logger()

    # --- construction ---

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        tools: Sequence[Callable[..., Any]] | None = None,
    ) -> "AgentRunner":
        config = load_config(config_path)
        model_cfg = config.get("model")
        if not model_cfg:
            raise ValueError("Config is missing the required 'model' block.")

        provider = model_cfg.get("provider", "")
        middleware = config.get("middleware", {}) or {}
        tool_list = list(tools or [])

        # Fail-fast capability check.
        caps = ProviderFactory.capabilities(provider)
        if tool_list and not caps.get("tool_calling", False):
            warnings.warn(
                f"Provider '{provider}' declares tool_calling=False but {len(tool_list)} "
                f"tool(s) were supplied; tool calls may fail at runtime.",
                stacklevel=2,
            )

        llm = ProviderFactory.get_llm(model_cfg)
        # Bind tools BEFORE applying retry so the retry wrapper keeps a RunnableBinding
        # outermost; the model handed to the executor stays tool-aware.
        model: Any = llm.bind_tools(tool_list) if tool_list else llm
        model = apply_retry(model, middleware.get("retry"), provider)

        gate = _build_gate(middleware.get("human_approval"))
        tool_cfg = middleware.get("tools") or {}
        executor = ReactExecutor(
            model,
            tool_list,
            system_prompt=_load_system_prompt(config),
            max_iterations=int(config.get("max_iterations", 10)),
            approval_gate=gate,
            tool_timeout=_opt_float(tool_cfg.get("timeout_seconds")),
            max_tool_failures=_opt_int(tool_cfg.get("max_failures")),
        )

        rate_limiter = RateLimiter()
        rate_limiter.configure(provider, middleware.get("rate_limiter"))

        conversation, persistent = _build_memory(config.get("memory", {}) or {})

        return cls(
            config=config,
            executor=executor,
            agent_name=config.get("agent_name", "roscoe-agent"),
            provider=provider,
            model=model_cfg.get("deployment") or model_cfg.get("model", ""),
            middleware=middleware,
            rate_limiter=rate_limiter,
            conversation=conversation,
            persistent=persistent,
        )

    # --- execution ---

    async def arun(
        self,
        user_input: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> AgentResult:
        run_id = str(uuid4())
        start = datetime.now(timezone.utc)
        await self._rate_limiter.acquire(self.provider)

        human = HumanMessage(content=user_input)
        input_messages = self._build_input(human, user_id, session_id)

        try:
            exec_result = await self._executor.run(input_messages)
        except Exception as exc:  # noqa: BLE001 — surface any failure as a result
            status = "rate_limited" if _is_rate_limit_exc(exc) else "error"
            error_msg = (
                "The AI service is currently rate-limited. Please try again in a moment."
                if status == "rate_limited"
                else f"{type(exc).__name__}: {exc}"
            )
            result = AgentResult(
                output="", run_id=run_id, error=error_msg, status=status
            )
            self._write_audit(result, user_id, start)
            return result

        return self._record(exec_result, run_id, user_id, session_id, human, start)

    def run(
        self,
        user_input: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> AgentResult:
        return _run_sync(self.arun(user_input, user_id=user_id, session_id=session_id))

    async def astream(
        self,
        user_input: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ):
        """Run a turn, yielding events as they happen.

        Yields the executor's ``token`` / ``tool`` events live, then a terminal
        ``{"type": "final", "result": AgentResult}`` (or ``"paused"``/``"error"``).
        The final event carries the same fully-recorded AgentResult ``run()`` returns,
        so cost, tokens, audit, and memory all behave identically to a non-streamed run.
        """
        run_id = str(uuid4())
        start = datetime.now(timezone.utc)
        await self._rate_limiter.acquire(self.provider)

        human = HumanMessage(content=user_input)
        input_messages = self._build_input(human, user_id, session_id)

        try:
            exec_result: ExecResult | None = None
            async for event in self._executor.astream(input_messages):
                if event["type"] == "done":
                    exec_result = event["result"]
                    break
                yield event
        except Exception as exc:  # noqa: BLE001
            status = "rate_limited" if _is_rate_limit_exc(exc) else "error"
            error_msg = (
                "The AI service is currently rate-limited. Please try again in a moment."
                if status == "rate_limited"
                else f"{type(exc).__name__}: {exc}"
            )
            result = AgentResult(output="", run_id=run_id, error=error_msg, status=status)
            self._write_audit(result, user_id, start)
            yield {"type": "error", "result": result}
            return

        result = self._record(exec_result, run_id, user_id, session_id, human, start)
        kind = "paused" if result.status == "paused" else "error" if result.status == "error" else "final"
        yield {"type": kind, "result": result}

    def stream(
        self,
        user_input: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ):
        """Synchronous wrapper over :meth:`astream` — yields the same events."""
        agen = self.astream(user_input, user_id=user_id, session_id=session_id)
        while True:
            try:
                yield _run_sync(agen.__anext__())
            except StopAsyncIteration:
                return

    async def aresume(
        self,
        run_id: str,
        decision: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Continue a paused run after a human decision.

        ``decision`` is one of ``approve`` | ``reject`` | ``modify``. For ``modify``,
        ``payload`` holds the edited arguments applied to the first held tool call.
        """
        if decision not in _DECISIONS:
            raise ValueError(f"decision must be one of {sorted(_DECISIONS)}, got '{decision}'.")

        pending = self._pending.pop(run_id)
        start = datetime.now(timezone.utc)
        await self._rate_limiter.acquire(self.provider)

        convo = pending.messages
        for i, call in enumerate(pending.tool_calls):
            if decision == "reject":
                convo.append(ReactExecutor.rejection_message(call))
            elif decision == "modify":
                override = payload if i == 0 else None
                convo.append(await self._executor.exec_tool_call(call, override_args=override))
            else:  # approve
                convo.append(await self._executor.exec_tool_call(call))

        try:
            exec_result = await self._executor.resume(convo)
        except Exception as exc:  # noqa: BLE001
            status = "rate_limited" if _is_rate_limit_exc(exc) else "error"
            error_msg = (
                "The AI service is currently rate-limited. Please try again in a moment."
                if status == "rate_limited"
                else f"{type(exc).__name__}: {exc}"
            )
            result = AgentResult(
                output="", run_id=run_id, error=error_msg, status=status
            )
            self._write_audit(result, pending.user_id, start)
            return result

        return self._record(
            exec_result, run_id, pending.user_id, pending.session_id, pending.human_message, start
        )

    def resume(
        self, run_id: str, decision: str, *, payload: dict[str, Any] | None = None
    ) -> AgentResult:
        return _run_sync(self.aresume(run_id, decision, payload=payload))

    # --- result handling ---

    def _record(
        self,
        exec_result: ExecResult,
        run_id: str,
        user_id: str | None,
        session_id: str | None,
        human: Any,
        start: datetime,
    ) -> AgentResult:
        """Turn an ExecResult into an AgentResult, persisting pending/paused state,
        writing conversation memory on success, and always emitting the audit record."""
        inp, out, total = sum_usage(exec_result.messages)
        cost = self._cost(inp, out)

        if exec_result.status == "paused":
            self._pending.save(
                PendingRun(
                    run_id=run_id,
                    messages=exec_result.messages,
                    tool_calls=exec_result.pending_tool_calls or [],
                    user_id=user_id,
                    session_id=session_id,
                    human_message=human,
                )
            )
            result = AgentResult(
                output="",
                run_id=run_id,
                total_tokens=total,
                cost_usd=cost,
                status="paused",
                pending_action={
                    "run_id": run_id,
                    "tool_calls": [
                        {"name": c["name"], "args": c.get("args", {}), "id": c.get("id")}
                        for c in (exec_result.pending_tool_calls or [])
                    ],
                },
            )
        elif exec_result.status == "error":
            result = AgentResult(
                output="", run_id=run_id, total_tokens=total, cost_usd=cost,
                status="error", error=exec_result.error,
            )
        else:
            output = _final_text(exec_result.messages)
            result = AgentResult(
                output=output,
                run_id=run_id,
                total_tokens=total,
                cost_usd=cost,
                status="success",
                tool_calls=_tool_names(exec_result.messages),
            )
            if self._conversation is not None and session_id is not None and human is not None:
                self._conversation.add(session_id, human, AIMessage(content=output))

        self._write_audit(result, user_id, start)
        return result

    def _build_input(
        self, human: HumanMessage, user_id: str | None, session_id: str | None
    ) -> list[Any]:
        """Assemble the input message list: facts + history + the new turn."""
        messages: list[Any] = []
        if self._persistent is not None and user_id is not None:
            facts = self._persistent.all(user_id)
            if facts:
                rendered = "; ".join(f"{k}={v}" for k, v in facts.items())
                messages.append(SystemMessage(content=f"Known facts: {rendered}"))
        if self._conversation is not None and session_id is not None:
            messages.extend(self._conversation.get(session_id))
        messages.append(human)
        return messages

    # --- middleware helpers ---

    def _cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self._mw.get("cost_tracking", {}).get("enabled", True) is False:
            return None
        return calculate_cost(self.provider, self.model, input_tokens, output_tokens)

    def _write_audit(
        self, result: AgentResult, user_id: str | None, start: datetime
    ) -> None:
        if self._mw.get("audit", {}).get("enabled", True) is False:
            return
        self._audit.log(
            {
                "run_id": result.run_id,
                "agent_name": self.agent_name,
                "user_id": user_id,
                "provider": self.provider,
                "model": self.model,
                "start_time": start.isoformat(),
                "end_time": datetime.now(timezone.utc).isoformat(),
                "total_tokens": result.total_tokens,
                "cost_usd": result.cost_usd,
                "nodes_traversed": result.nodes_traversed,
                "status": result.status,
                "error": result.error,
            }
        )


# --- helpers ---


def _build_gate(approval_cfg: dict[str, Any] | None) -> ApprovalGate | None:
    """Build an ApprovalGate from the ``middleware.human_approval`` block, if active."""
    cfg = approval_cfg or {}
    if cfg.get("enabled") is False:
        return None
    names = cfg.get("require_approval_for") or []
    return ApprovalGate(names) if names else None


def _build_memory(
    memory_cfg: dict[str, Any],
) -> tuple[ConversationMemory | None, PersistentMemory | None]:
    """Build conversation + persistent memory from the ``memory:`` config block."""
    conversation = None
    conv_cfg = memory_cfg.get("conversation", {}) or {}
    if conv_cfg.get("enabled"):
        conversation = ConversationMemory(window_size=conv_cfg.get("window_size", 10))

    persistent = None
    pers_cfg = memory_cfg.get("persistent", {}) or {}
    if pers_cfg.get("enabled"):
        persistent = PersistentMemory(
            backend=pers_cfg.get("backend", "sqlite"),
            connection=pers_cfg.get("connection", ":memory:"),
        )

    return conversation, persistent


def _opt_float(value: Any) -> float | None:
    """Coerce an optional config value to float, or None if unset."""
    return float(value) if value is not None else None


def _opt_int(value: Any) -> int | None:
    """Coerce an optional config value to int, or None if unset."""
    return int(value) if value is not None else None


def _load_system_prompt(config: dict[str, Any]) -> str | None:
    if config.get("system_prompt"):
        return str(config["system_prompt"])
    prompt_file = config.get("system_prompt_file")
    if prompt_file:
        return Path(prompt_file).read_text()
    return None


def _final_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def _tool_names(messages: list[Any]) -> list[str]:
    """Ordered names of every tool the agent called across the run."""
    names: list[str] = []
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or []:
            names.append(call["name"])
    return names


_thread_local = threading.local()


def _run_sync(coro):
    """Run a coroutine from sync code using a persistent per-thread event loop.

    ``asyncio.run()`` closes the loop after every call, which invalidates any
    asyncio primitives (locks, semaphores) held on the AgentRunner instance
    (e.g. the rate-limiter's TokenBucket lock). Reusing the loop per thread
    avoids the "Event loop is closed" error that appears on the second request
    in Flask / other sync web frameworks.
    """
    loop = getattr(_thread_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _thread_local.loop = loop
    return loop.run_until_complete(coro)


def _is_rate_limit_exc(exc: BaseException) -> bool:
    """Return True if the exception is a rate-limit error from any provider."""
    _MARKERS = ("429", "ratelimit", "rate_limit", "rate-limit", "resource exhausted", "quota")
    msg = f"{type(exc).__name__} {exc}".lower()
    return any(m in msg for m in _MARKERS)
