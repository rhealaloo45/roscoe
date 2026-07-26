"""WorkflowExecutor — runs a declarative workflow graph.

This is a sibling of :class:`roscoe.core.executor.ReactExecutor`, not a replacement.
The ReAct executor lets the *model* choose what happens next; this one follows edges
the author wrote down. Projects with no ``workflow:`` block never touch this module.

The loop is deliberately small: resolve the node's templates against the shared state,
run it, write its output back to the state, pick the next edge.

Two things pause a run, and both surface through the same ``paused`` result:

* a ``connector_action`` marked ``requires_approval`` (or whose method is globally
  gated) — held *before* it executes, with its arguments already resolved so a
  reviewer sees real values rather than a template;
* a gated tool call chosen by an ``agent_step``'s inner ReAct loop — the workflow
  stops with the agent mid-flight and resumes exactly where it left off, so wrapping
  work in an agent never silently escapes an approval gate.

Async-first, matching the rest of the core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from roscoe.approval.gate import ApprovalGate
from roscoe.core.executor import ExecResult, ReactExecutor
from roscoe.workflow.expressions import ExpressionError, render, truthy
from roscoe.workflow.schema import (
    END,
    AgentStep,
    Condition,
    ConnectorAction,
    LLMStep,
    Node,
    Workflow,
    WorkflowError,
)

#: Decisions accepted by :meth:`WorkflowExecutor.resume` — same vocabulary as
#: ``AgentRunner.resume`` so a UI handles both without special-casing.
_DECISIONS = ("approve", "reject", "modify")


@dataclass
class PendingNode:
    """A workflow suspended awaiting a human decision.

    ``kind`` says what is being approved: ``"connector"`` for a gated
    ``connector_action`` (see ``connector`` / ``method`` / ``args``), or ``"agent"``
    for a gated tool call an ``agent_step``'s inner loop wants to make (see ``agent``
    / ``tool_calls``). The remaining fields carry the workflow state needed to resume.
    """

    node_id: str
    kind: str = "connector"
    state: dict[str, Any] = field(default_factory=dict)
    nodes_traversed: list[str] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)

    # kind == "connector"
    connector: str = ""
    method: str = ""
    args: dict[str, Any] = field(default_factory=dict)

    # kind == "agent"
    agent: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    inner_messages: list[Any] = field(default_factory=list)


@dataclass
class WorkflowResult:
    """Outcome of running (or resuming) a workflow."""

    state: dict[str, Any]
    status: str = "success"  # "success" | "paused" | "error"
    output: str = ""
    nodes_traversed: list[str] = field(default_factory=list)
    #: Messages from ``llm_step`` / ``agent_step`` nodes, for token and cost accounting.
    messages: list[Any] = field(default_factory=list)
    pending: PendingNode | None = None
    error: str | None = None
    failed_node: str | None = None


class _Paused(Exception):
    """Internal: a node stopped for approval. Carries the pending record."""

    def __init__(self, pending: PendingNode) -> None:
        super().__init__(pending.node_id)
        self.pending = pending


class WorkflowExecutor:
    """Walks a :class:`Workflow`, executing each node against a shared state dict."""

    def __init__(
        self,
        workflow: Workflow,
        *,
        connectors: dict[str, Any] | None = None,
        llm: Any | None = None,
        tools: list[Any] | None = None,
        approval_gate: ApprovalGate | None = None,
    ) -> None:
        self._wf = workflow
        self._tools = _index_tools(connectors or {})
        self._extra_tools = {tool.name: tool for tool in (tools or [])}
        self._llm = llm
        self._gate = approval_gate
        self._agent_cache: dict[str, ReactExecutor] = {}

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

        ``approve`` runs the held action as-is, ``modify`` runs it with
        ``override_args``, and ``reject`` declines it. A rejected
        ``connector_action`` writes ``None`` to its output key and the run carries on
        along the normal route; a rejected agent tool call is reported back to the
        agent, which decides how to proceed.
        """
        if decision not in _DECISIONS:
            raise ValueError(f"decision must be one of {list(_DECISIONS)}, got '{decision}'.")

        state = dict(pending.state)
        traversed = list(pending.nodes_traversed)
        messages = list(pending.messages)
        node = self._wf.get(pending.node_id)

        try:
            if pending.kind == "agent":
                return await self._resume_agent(
                    node, pending, decision, override_args, state, traversed, messages
                )
            return await self._resume_connector(
                node, pending, decision, override_args, state, traversed, messages
            )
        except _Paused as paused:
            return WorkflowResult(
                state=state, status="paused", nodes_traversed=traversed,
                messages=messages, pending=paused.pending,
            )
        except (ExpressionError, WorkflowError) as exc:
            return _failure(state, traversed, messages, str(exc), node.id)
        except Exception as exc:  # noqa: BLE001
            return _failure(state, traversed, messages, f"{type(exc).__name__}: {exc}", node.id)

    # --- resume paths ---

    async def _resume_connector(
        self, node: Node, pending: PendingNode, decision: str,
        override_args: dict[str, Any] | None, state: dict[str, Any],
        traversed: list[str], messages: list[Any],
    ) -> WorkflowResult:
        if not isinstance(node, ConnectorAction):
            raise WorkflowError(f"Node '{node.id}' is not an approvable connector action.")

        if decision == "reject":
            if node.output:
                state[node.output] = None
        else:
            args = (
                override_args
                if decision == "modify" and override_args is not None
                else pending.args
            )
            value = await self._call_tool(node, args)
            if node.output:
                state[node.output] = value

        return await self._walk(self._wf.next_after(node), state, traversed, messages)

    async def _resume_agent(
        self, node: Node, pending: PendingNode, decision: str,
        override_args: dict[str, Any] | None, state: dict[str, Any],
        traversed: list[str], messages: list[Any],
    ) -> WorkflowResult:
        """Feed the human's decision back into the agent's own loop and continue it."""
        if not isinstance(node, AgentStep):
            raise WorkflowError(f"Node '{node.id}' is not an agent step.")

        executor = self._agent_executor(node.agent)
        convo = list(pending.inner_messages)
        for index, call in enumerate(pending.tool_calls):
            if decision == "reject":
                convo.append(ReactExecutor.rejection_message(call))
            elif decision == "modify":
                override = override_args if index == 0 else None
                convo.append(await executor.exec_tool_call(call, override_args=override))
            else:
                convo.append(await executor.exec_tool_call(call))

        inner = await executor.resume(convo)
        value = self._settle_agent(node, inner, state, traversed, messages)
        if node.output:
            state[node.output] = value
        return await self._walk(self._wf.next_after(node), state, traversed, messages)

    # --- the loop ---

    async def _walk(
        self, start: str, state: dict[str, Any], traversed: list[str], messages: list[Any]
    ) -> WorkflowResult:
        current = start
        steps = 0

        while current != END:
            if steps >= self._wf.max_steps:
                return _failure(
                    state, traversed, messages,
                    f"Workflow exceeded max_steps ({self._wf.max_steps}) without reaching "
                    f"END — check for a routing cycle.",
                    current,
                )
            steps += 1

            try:
                node = self._wf.get(current)
            except WorkflowError as exc:
                return _failure(state, traversed, messages, str(exc), current)

            traversed.append(node.id)

            try:
                current = await self._execute(node, state, traversed, messages)
            except _Paused as paused:
                return WorkflowResult(
                    state=state, status="paused", nodes_traversed=traversed,
                    messages=messages, pending=paused.pending,
                )
            except (ExpressionError, WorkflowError) as exc:
                return _failure(state, traversed, messages, str(exc), node.id)
            except Exception as exc:  # noqa: BLE001 — surface, don't raise through
                return _failure(
                    state, traversed, messages, f"{type(exc).__name__}: {exc}", node.id
                )

        return WorkflowResult(
            state=state,
            status="success",
            output=self._final_output(state, traversed),
            nodes_traversed=traversed,
            messages=messages,
        )

    async def _execute(
        self, node: Node, state: dict[str, Any], traversed: list[str], messages: list[Any]
    ) -> str:
        """Run one node, write its output into ``state``, and return the next node id."""
        if isinstance(node, Condition):
            branch = node.then if truthy(node.when, state) else node.otherwise
            return branch or self._wf.next_after(node)

        if isinstance(node, ConnectorAction):
            args = render(node.inputs, state)
            if self._needs_approval(node):
                raise _Paused(
                    PendingNode(
                        node_id=node.id, kind="connector", connector=node.connector,
                        method=node.method, args=args, state=state,
                        nodes_traversed=traversed, messages=messages,
                    )
                )
            value: Any = await self._call_tool(node, args)
        elif isinstance(node, LLMStep):
            value = await self._call_llm(node, state, messages)
        elif isinstance(node, AgentStep):
            value = await self._call_agent(node, state, traversed, messages)
        else:
            raise WorkflowError(f"Node '{node.id}' has unsupported type '{node.type}'.")

        if node.output:
            state[node.output] = value
        return self._wf.next_after(node)

    # --- node kinds ---

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

    async def _call_agent(
        self, node: AgentStep, state: dict[str, Any], traversed: list[str], messages: list[Any]
    ) -> str:
        executor = self._agent_executor(node.agent)
        task = str(render(node.task, state))
        inner = await executor.run([HumanMessage(content=task)])
        return self._settle_agent(node, inner, state, traversed, messages)

    def _settle_agent(
        self, node: AgentStep, inner: ExecResult, state: dict[str, Any],
        traversed: list[str], messages: list[Any],
    ) -> str:
        """Turn a finished (or paused) inner ReAct result into this node's output."""
        if inner.status == "paused":
            raise _Paused(
                PendingNode(
                    node_id=node.id, kind="agent", agent=node.agent,
                    tool_calls=inner.pending_tool_calls or [],
                    inner_messages=inner.messages, state=state,
                    nodes_traversed=traversed, messages=messages,
                )
            )
        if inner.status == "error":
            raise WorkflowError(f"Agent '{node.agent}' failed in node '{node.id}': {inner.error}")

        messages.extend(inner.messages)
        return _final_text(inner.messages)

    def _agent_executor(self, name: str) -> ReactExecutor:
        """Build (and cache) the ReAct executor backing a named agent.

        The inner loop gets the same approval gate as the workflow, so a tool listed
        in ``require_approval_for`` still pauses when an agent reaches for it.
        """
        cached = self._agent_cache.get(name)
        if cached is not None:
            return cached

        spec = self._wf.agents.get(name)
        if spec is None:
            known = ", ".join(sorted(self._wf.agents)) or "(no agents defined)"
            raise WorkflowError(f"No agent named '{name}'. Available: {known}")
        if self._llm is None:
            raise WorkflowError(
                f"Agent '{name}' needs a model, but none was configured."
            )

        tools = self._resolve_tools(spec.tools, name)
        model = self._llm.bind_tools(tools) if tools else self._llm
        executor = ReactExecutor(
            model,
            tools,
            system_prompt=spec.system_prompt,
            max_iterations=spec.max_iterations,
            approval_gate=self._gate,
        )
        self._agent_cache[name] = executor
        return executor

    def _resolve_tools(self, refs: list[str], agent_name: str) -> list[Any]:
        """Resolve ``connector.method`` / bare-name tool references to tool objects."""
        resolved: list[Any] = []
        for ref in refs:
            if "." in ref:
                connector, method = ref.split(".", 1)
                bag = self._tools.get(connector)
                if bag is None:
                    known = ", ".join(sorted(self._tools)) or "(none configured)"
                    raise WorkflowError(
                        f"Agent '{agent_name}' wants '{ref}', but connector "
                        f"'{connector}' is not configured. Available: {known}"
                    )
                tool = bag.get(method)
                if tool is None:
                    known = ", ".join(sorted(bag)) or "(no methods)"
                    raise WorkflowError(
                        f"Agent '{agent_name}' wants '{ref}', but connector "
                        f"'{connector}' has no method '{method}'. Available: {known}"
                    )
                resolved.append(tool)
                continue

            tool = self._extra_tools.get(ref)
            if tool is None:
                for bag in self._tools.values():
                    if ref in bag:
                        tool = bag[ref]
                        break
            if tool is None:
                raise WorkflowError(
                    f"Agent '{agent_name}' wants tool '{ref}', which is not available "
                    f"from any configured connector or the supplied tool list."
                )
            resolved.append(tool)
        return resolved

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


def _failure(
    state: dict[str, Any], traversed: list[str], messages: list[Any],
    error: str, node_id: str | None,
) -> WorkflowResult:
    return WorkflowResult(
        state=state, status="error", nodes_traversed=traversed,
        messages=messages, error=error, failed_node=node_id,
    )


def _final_text(messages: list[Any]) -> str:
    """Last assistant text in a message list — an agent step's answer."""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
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
