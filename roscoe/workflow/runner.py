"""WorkflowRunner — a workflow with roscoe's middleware stack around it.

:class:`~roscoe.workflow.executor.WorkflowExecutor` walks the graph; this wraps it in
everything a run is expected to have — rate limiting, cost tracking, audit logging —
and returns the same :class:`~roscoe.core.agent_result.AgentResult` as
:class:`~roscoe.core.agent_runner.AgentRunner`.

That shared return type is the point: ``roscoe run``, the browser chat, and anything
else built on ``AgentResult`` keep working when a project switches to a workflow. A
paused workflow reports its held action in the same ``pending_action`` shape as a
paused tool call, so an approval UI needs no special case.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from roscoe.core.agent_result import AgentResult
from roscoe.core.agent_runner import _build_gate, _is_rate_limit_exc, _run_sync
from roscoe.llm.provider_factory import ProviderFactory
from roscoe.middleware.audit_logger import get_audit_logger
from roscoe.middleware.cost_tracker import calculate_cost, sum_usage
from roscoe.middleware.rate_limiter import RateLimiter
from roscoe.middleware.retry import apply_retry
from roscoe.workflow.executor import PendingNode, WorkflowExecutor, WorkflowResult
from roscoe.workflow.loader import load_workflow
from roscoe.workflow.registry import build_connectors
from roscoe.workflow.schema import Workflow

_DECISIONS = {"approve", "reject", "modify"}


class WorkflowRunner:
    """Runs a declarative workflow with middleware, returning ``AgentResult``."""

    def __init__(
        self,
        *,
        workflow: Workflow,
        executor: WorkflowExecutor,
        config: dict[str, Any],
        agent_name: str,
        provider: str,
        model: str,
        rate_limiter: RateLimiter,
    ) -> None:
        self.workflow = workflow
        self.config = config
        self.agent_name = agent_name
        self.provider = provider
        self.model = model
        self._executor = executor
        self._mw = config.get("middleware", {}) or {}
        self._rate_limiter = rate_limiter
        self._pending: dict[str, PendingNode] = {}
        self._audit = get_audit_logger()

    @classmethod
    def from_config(
        cls,
        config_path: str | Path = "agent_config.yaml",
        *,
        workflow_path: str | Path | None = None,
        tools: Sequence[Any] | None = None,
    ) -> "WorkflowRunner":
        """Build a runner from a project's config and workflow definition."""
        workflow, config = load_workflow(config_path, workflow_path)

        model_cfg = config.get("model") or {}
        if not model_cfg:
            raise ValueError("Config is missing the required 'model' block.")
        provider = model_cfg.get("provider", "")
        middleware = config.get("middleware", {}) or {}

        # The model stays unbound: WorkflowExecutor binds per-agent tool sets itself.
        llm = apply_retry(ProviderFactory.get_llm(model_cfg), middleware.get("retry"), provider)
        connectors = build_connectors(config.get("connectors") or {})

        executor = WorkflowExecutor(
            workflow,
            connectors=connectors,
            llm=llm,
            tools=list(tools or []),
            approval_gate=_build_gate(middleware.get("human_approval")),
        )

        rate_limiter = RateLimiter()
        rate_limiter.configure(provider, middleware.get("rate_limiter"))

        return cls(
            workflow=workflow,
            executor=executor,
            config=config,
            agent_name=config.get("agent_name", "roscoe-workflow"),
            provider=provider,
            model=model_cfg.get("deployment") or model_cfg.get("model", ""),
            rate_limiter=rate_limiter,
        )

    # --- execution ---

    async def arun(
        self,
        user_input: str | dict[str, Any] = "",
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> AgentResult:
        """Run the workflow.

        A dict is passed straight through as the workflow's inputs; a string is
        supplied as ``input.message`` so a chat UI can drive a workflow unchanged.
        """
        run_id = str(uuid4())
        start = datetime.now(timezone.utc)
        await self._rate_limiter.acquire(self.provider)

        inputs = user_input if isinstance(user_input, dict) else {"message": user_input}

        try:
            result = await self._executor.run(inputs)
        except Exception as exc:  # noqa: BLE001
            return self._error_result(exc, run_id, user_id, start)

        return self._record(result, run_id, user_id, start)

    def run(
        self,
        user_input: str | dict[str, Any] = "",
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> AgentResult:
        return _run_sync(self.arun(user_input, user_id=user_id, session_id=session_id))

    async def aresume(
        self, run_id: str, decision: str, *, payload: dict[str, Any] | None = None
    ) -> AgentResult:
        """Continue a paused workflow after a human decision."""
        if decision not in _DECISIONS:
            raise ValueError(f"decision must be one of {sorted(_DECISIONS)}, got '{decision}'.")

        pending = self._pending.pop(run_id, None)
        if pending is None:
            raise KeyError(
                f"No paused workflow with id '{run_id}'. It may have already been "
                f"resumed, or this process didn't create it."
            )

        start = datetime.now(timezone.utc)
        await self._rate_limiter.acquire(self.provider)

        try:
            result = await self._executor.resume(pending, decision, override_args=payload)
        except Exception as exc:  # noqa: BLE001
            return self._error_result(exc, run_id, None, start)

        return self._record(result, run_id, None, start)

    def resume(
        self, run_id: str, decision: str, *, payload: dict[str, Any] | None = None
    ) -> AgentResult:
        return _run_sync(self.aresume(run_id, decision, payload=payload))

    # --- result handling ---

    def _record(
        self, result: WorkflowResult, run_id: str, user_id: str | None, start: datetime
    ) -> AgentResult:
        inp, out, total = sum_usage(result.messages)
        cost = self._cost(inp, out)

        if result.status == "paused" and result.pending is not None:
            self._pending[run_id] = result.pending
            agent_result = AgentResult(
                output="",
                run_id=run_id,
                total_tokens=total,
                cost_usd=cost,
                status="paused",
                nodes_traversed=result.nodes_traversed,
                pending_action=_pending_action(run_id, result.pending),
            )
        elif result.status == "error":
            where = f" (node '{result.failed_node}')" if result.failed_node else ""
            agent_result = AgentResult(
                output="", run_id=run_id, total_tokens=total, cost_usd=cost,
                status="error", error=f"{result.error}{where}",
                nodes_traversed=result.nodes_traversed,
            )
        else:
            agent_result = AgentResult(
                output=result.output,
                run_id=run_id,
                total_tokens=total,
                cost_usd=cost,
                status="success",
                nodes_traversed=result.nodes_traversed,
            )

        self._write_audit(agent_result, user_id, start)
        return agent_result

    def _error_result(
        self, exc: Exception, run_id: str, user_id: str | None, start: datetime
    ) -> AgentResult:
        status = "rate_limited" if _is_rate_limit_exc(exc) else "error"
        message = (
            "The AI service is currently rate-limited. Please try again in a moment."
            if status == "rate_limited"
            else f"{type(exc).__name__}: {exc}"
        )
        result = AgentResult(output="", run_id=run_id, error=message, status=status)
        self._write_audit(result, user_id, start)
        return result

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


def _pending_action(run_id: str, pending: PendingNode) -> dict[str, Any]:
    """Describe a held action in the same shape a paused tool call uses.

    Approval UIs read ``pending_action["tool_calls"]``; presenting a gated node the
    same way means they render a workflow pause with no special case.
    """
    if pending.kind == "agent":
        calls = [
            {"name": call.get("name", ""), "args": call.get("args", {}), "id": call.get("id")}
            for call in pending.tool_calls
        ]
    else:
        calls = [{"name": f"{pending.connector}.{pending.method}", "args": pending.args, "id": None}]

    return {
        "run_id": run_id,
        "node": pending.node_id,
        "kind": pending.kind,
        "tool_calls": calls,
    }
