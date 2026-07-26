"""WorkflowExecutor — runs a declarative workflow graph.

This is a sibling of :class:`roscoe.core.executor.ReactExecutor`, not a replacement.
The ReAct executor lets the *model* choose what happens next; this one follows edges
the author wrote down. Projects with no ``workflow:`` block never touch this module.

The loop is deliberately small: resolve the node's templates against the shared state,
run it, write its output back to the state, pick the next edge. Human approval reuses
the existing pause/resume contract — a gated node stops the run *before* it executes
and returns ``status="paused"`` with its already-resolved arguments, so a reviewer sees
the actual call rather than a template.

Async-first, matching the rest of the core: ``run()`` is the real implementation and
``AgentRunner`` awaits it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from roscoe.approval.gate import ApprovalGate
from roscoe.workflow.expressions import ExpressionError, render, truthy
from roscoe.workflow.schema import (
    END,
    Condition,
    ConnectorAction,
    LLMStep,
    Node,
    Workflow,
    WorkflowError,
)


@dataclass
class WorkflowResult:
    """Outcome of running (or resuming) a workflow."""

    state: dict[str, Any]
    status: str = "success"  # "success" | "paused" | "error"
    output: str = ""
    nodes_traversed: list[str] = field(default_factory=list)
    #: AIMessages produced by ``llm_step`` nodes, for token/cost accounting.
    messages: list[Any] = field(default_factory=list)
    pending: "PendingNode | None" = None
    error: str | None = None
    failed_node: str | None = None


@dataclass
class PendingNode:
    """A workflow suspended before a gated node runs.

    Carries everything needed to resume: which node stopped, the arguments it was
    about to be called with (already resolved, so a human reviews real values), and
    the state/traversal captured at that moment.
    """

    node_id: str
    connector: str
    method: str
    args: dict[str, Any]
    state: dict[str, Any]
    nodes_traversed: list[str] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)


class WorkflowExecutor:
    """Walks a :class:`Workflow`, executing each node against a shared state dict."""

    def __init__(
        self,
        workflow: Workflow,
        *,
        connectors: dict[str, Any] | None = None,
        llm: Any | None = None,
        approval_gate: ApprovalGate | None = None,
    ) -> None:
        self._wf = workflow
        self._tools = _index_tools(connectors or {})
        self._llm = llm
        self._gate = approval_gate

    async def run(self, inputs: dict[str, Any] | None = None) -> WorkflowResult:
        """Execute from the workflow's entry node with ``inputs`` under ``input.*``."""
        state: dict[str, Any] = {"input": dict(inputs or {})}
        return await self._walk(self._wf.entry, state, [], [])

    async def resume(
        self,
        pending: PendingNode,
        decision: str,
        *,
        override_args: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Continue a paused workflow after a human decision.

        ``approve`` runs the held node as-is, ``modify`` runs it with ``override_args``,
        and ``reject`` skips it — writing ``None`` to its output key and carrying on
        along the normal route, so a rejection is a branch rather than a crash.
        """
        if decision not in ("approve", "reject", "modify"):
            raise ValueError(
                f"decision must be 'approve', 'reject', or 'modify', got '{decision}'."
            )

        node = self._wf.get(pending.node_id)
        if not isinstance(node, ConnectorAction):
            raise WorkflowError(f"Node '{pending.node_id}' is not an approvable node.")

        state = dict(pending.state)
        traversed = list(pending.nodes_traversed)
        messages = list(pending.messages)

        if decision == "reject":
            if node.output:
                state[node.output] = None
        else:
            args = override_args if decision == "modify" and override_args is not None else pending.args
            try:
                value = await self._call_tool(node, args)
            except Exception as exc:  # noqa: BLE001 — a failed node ends the run
                return WorkflowResult(
                    state=state,
                    status="error",
                    nodes_traversed=traversed,
                    messages=messages,
                    error=f"{type(exc).__name__}: {exc}",
                    failed_node=node.id,
                )
            if node.output:
                state[node.output] = value

        return await self._walk(self._wf.next_after(node), state, traversed, messages)

    # --- the loop ---

    async def _walk(
        self,
        start: str,
        state: dict[str, Any],
        traversed: list[str],
        messages: list[Any],
    ) -> WorkflowResult:
        current = start
        steps = 0

        while current != END:
            if steps >= self._wf.max_steps:
                return WorkflowResult(
                    state=state,
                    status="error",
                    nodes_traversed=traversed,
                    messages=messages,
                    error=(
                        f"Workflow exceeded max_steps ({self._wf.max_steps}) without "
                        f"reaching END — check for a routing cycle."
                    ),
                    failed_node=current,
                )
            steps += 1

            try:
                node = self._wf.get(current)
            except WorkflowError as exc:
                return WorkflowResult(
                    state=state, status="error", nodes_traversed=traversed,
                    messages=messages, error=str(exc), failed_node=current,
                )

            traversed.append(node.id)

            try:
                # A gated node stops the run *before* doing anything.
                if isinstance(node, ConnectorAction) and self._needs_approval(node):
                    args = render(node.inputs, state)
                    return WorkflowResult(
                        state=state,
                        status="paused",
                        nodes_traversed=traversed,
                        messages=messages,
                        pending=PendingNode(
                            node_id=node.id,
                            connector=node.connector,
                            method=node.method,
                            args=args,
                            state=state,
                            nodes_traversed=traversed,
                            messages=messages,
                        ),
                    )
                current = await self._execute(node, state, messages)
            except (ExpressionError, WorkflowError) as exc:
                return WorkflowResult(
                    state=state, status="error", nodes_traversed=traversed,
                    messages=messages, error=str(exc), failed_node=node.id,
                )
            except Exception as exc:  # noqa: BLE001 — surface, don't raise through
                return WorkflowResult(
                    state=state, status="error", nodes_traversed=traversed,
                    messages=messages, error=f"{type(exc).__name__}: {exc}",
                    failed_node=node.id,
                )

        return WorkflowResult(
            state=state,
            status="success",
            output=self._final_output(state, traversed),
            nodes_traversed=traversed,
            messages=messages,
        )

    async def _execute(self, node: Node, state: dict[str, Any], messages: list[Any]) -> str:
        """Run one node, write its output into ``state``, and return the next node id."""
        if isinstance(node, Condition):
            branch = node.then if truthy(node.when, state) else node.otherwise
            return branch or self._wf.next_after(node)

        if isinstance(node, ConnectorAction):
            value = await self._call_tool(node, render(node.inputs, state))
        elif isinstance(node, LLMStep):
            value = await self._call_llm(node, state, messages)
        else:
            raise WorkflowError(f"Node '{node.id}' has unsupported type '{node.type}'.")

        if node.output:
            state[node.output] = value
        return self._wf.next_after(node)

    async def _call_tool(self, node: ConnectorAction, args: dict[str, Any]) -> Any:
        tools = self._tools.get(node.connector)
        if tools is None:
            known = ", ".join(sorted(self._tools)) or "(none configured)"
            raise WorkflowError(
                f"Node '{node.id}' uses connector '{node.connector}', which is not "
                f"configured. Available: {known}"
            )
        tool = tools.get(node.method)
        if tool is None:
            known = ", ".join(sorted(tools)) or "(no methods)"
            raise WorkflowError(
                f"Connector '{node.connector}' has no method '{node.method}' "
                f"(node '{node.id}'). Available: {known}"
            )
        return await tool.ainvoke(args)

    async def _call_llm(
        self, node: LLMStep, state: dict[str, Any], messages: list[Any]
    ) -> str:
        if self._llm is None:
            raise WorkflowError(
                f"Node '{node.id}' is an llm_step but no model was configured."
            )
        prompt: list[Any] = []
        if node.system:
            prompt.append(SystemMessage(content=str(render(node.system, state))))
        prompt.append(HumanMessage(content=str(render(node.prompt, state))))

        reply = await self._llm.ainvoke(prompt)
        messages.append(reply)
        content = getattr(reply, "content", "")
        return content if isinstance(content, str) else str(content)

    # --- helpers ---

    def _needs_approval(self, node: ConnectorAction) -> bool:
        """A node is gated by its own flag, or by the method being globally gated.

        The second half means a method already listed in
        ``middleware.human_approval.require_approval_for`` stays gated here without
        having to be re-declared on every node that calls it.
        """
        if node.requires_approval:
            return True
        return self._gate is not None and self._gate.needs_approval(node.method)

    def _final_output(self, state: dict[str, Any], traversed: list[str]) -> str:
        """The run's answer: the workflow's ``output:`` template, else the last value written."""
        if self._wf.output:
            return str(render(self._wf.output, state))
        for node_id in reversed(traversed):
            node = self._wf.get(node_id)
            if node.output and node.output in state:
                value = state[node.output]
                return value if isinstance(value, str) else str(value)
        return ""


def _index_tools(connectors: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalise the connector mapping to ``{connector_name: {method_name: tool}}``.

    Accepts a connector instance (anything exposing ``.tools``), a bare list of tools,
    or an already-indexed dict — so tests can pass fakes without building HTTP clients.
    """
    indexed: dict[str, dict[str, Any]] = {}
    for name, source in connectors.items():
        if isinstance(source, dict):
            indexed[name] = source
            continue
        tools = getattr(source, "tools", source)
        indexed[name] = {tool.name: tool for tool in tools}
    return indexed
