"""Workflow schema — parsing a ``workflow:`` block into validated node objects.

Everything structural is checked here, at parse time: unknown node types, missing
required fields, duplicate ids, and edges pointing at nodes that do not exist. That
keeps the executor free of defensive checks and gives ``roscoe validate`` (Phase 3)
something to call without running anything.

See ``docs/WORKFLOW_SPEC.md`` for the YAML shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Reserved successor id meaning "stop the run".
END = "END"


class WorkflowError(ValueError):
    """Raised when a workflow definition is structurally invalid."""


@dataclass
class Node:
    """One step in a workflow. Subclasses add per-type fields."""

    id: str
    #: State key this node's result is written to. ``None`` writes nothing.
    output: str | None = None
    #: Explicit successor id. ``None`` falls through to the next node in the list.
    next: str | None = None

    @property
    def type(self) -> str:
        raise NotImplementedError

    def successors(self) -> list[str]:
        """Node ids this node can route to — used for reference validation."""
        return [self.next] if self.next else []


@dataclass
class ConnectorAction(Node):
    """Call a single tool with templated arguments.

    ``connector`` is optional: name one to disambiguate, or leave it out and the
    ``method`` is resolved against the project's own tools first, then any configured
    connector. That means a workflow can call a local ``@tool`` function without
    pretending it is a connector.
    """

    connector: str = ""
    method: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    #: Where to go if a human rejects this action. Unset means stop the run — the
    #: normal route usually assumes the action succeeded, so following it after a
    #: rejection would report work that never happened.
    on_reject: str | None = None

    @property
    def type(self) -> str:
        return "connector_action"

    def successors(self) -> list[str]:
        return [s for s in (self.next, self.on_reject) if s]


@dataclass
class Condition(Node):
    """Branch on an expression evaluated against the state."""

    when: str = ""
    then: str = ""
    otherwise: str | None = None  # ``else:`` in YAML — ``else`` is a Python keyword.

    @property
    def type(self) -> str:
        return "condition"

    def successors(self) -> list[str]:
        return [s for s in (self.then, self.otherwise, self.next) if s]


@dataclass
class LLMStep(Node):
    """One prompt to the configured model. No tools, no loop."""

    prompt: str = ""
    system: str | None = None

    @property
    def type(self) -> str:
        return "llm_step"


@dataclass
class AgentStep(Node):
    """Hand a task to a named agent running roscoe's autonomous ReAct loop.

    The escape hatch: when a sub-problem is too open-ended to wire as explicit
    nodes, let the model choose its own tool calls for that stretch of the graph.
    """

    agent: str = ""
    task: str = ""

    @property
    def type(self) -> str:
        return "agent_step"


@dataclass
class AgentSpec:
    """One named agent in the ``agents:`` block, referenced by ``agent_step`` nodes."""

    name: str
    system_prompt: str | None = None
    #: Tool references — ``connector.method`` or a bare tool name.
    tools: list[str] = field(default_factory=list)
    max_iterations: int = 10

    @classmethod
    def from_dict(cls, name: str, raw: Any) -> "AgentSpec":
        if not isinstance(raw, dict):
            raise WorkflowError(
                f"Agent '{name}' must be a mapping, got {type(raw).__name__}."
            )
        tools = raw.get("tools") or []
        if not isinstance(tools, list):
            raise WorkflowError(
                f"Agent '{name}': 'tools' must be a list, got {type(tools).__name__}."
            )
        max_iterations = raw.get("max_iterations", 10)
        if not isinstance(max_iterations, int) or max_iterations < 1:
            raise WorkflowError(f"Agent '{name}': 'max_iterations' must be a positive integer.")
        return cls(
            name=name,
            system_prompt=raw.get("system_prompt"),
            tools=[str(t) for t in tools],
            max_iterations=max_iterations,
        )


@dataclass
class Workflow:
    """A parsed, structurally-valid workflow."""

    nodes: list[Node]
    entry: str
    output: str | None = None
    max_steps: int = 50
    agents: dict[str, AgentSpec] = field(default_factory=dict)
    #: Default system prompt for every ``llm_step``. Without it, shared instructions
    #: ("no greeting, no signature") have to be repeated in each node's prompt. A
    #: node's own ``system:`` overrides this.
    system: str | None = None

    def __post_init__(self) -> None:
        self._by_id = {node.id: node for node in self.nodes}

    def get(self, node_id: str) -> Node:
        node = self._by_id.get(node_id)
        if node is None:
            raise WorkflowError(f"No node with id '{node_id}'.")
        return node

    def next_after(self, node: Node) -> str:
        """Successor when a node does not choose one itself: the following node, else END."""
        if node.next:
            return node.next
        index = self.nodes.index(node)
        if index + 1 < len(self.nodes):
            return self.nodes[index + 1].id
        return END

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], agents: dict[str, Any] | None = None
    ) -> "Workflow":
        """Parse and validate a ``workflow:`` mapping.

        ``agents`` may be passed separately (the ``agents:`` block usually sits
        alongside ``workflow:`` in ``agent_config.yaml``) or nested inside ``data``.

        Raises:
            WorkflowError: on any structural problem, naming the offending node.
        """
        if not isinstance(data, dict):
            raise WorkflowError(
                f"'workflow' must be a mapping, got {type(data).__name__}."
            )

        raw_nodes = data.get("nodes")
        if not raw_nodes:
            raise WorkflowError("'workflow.nodes' is required and must list at least one node.")
        if not isinstance(raw_nodes, list):
            raise WorkflowError(
                f"'workflow.nodes' must be a list, got {type(raw_nodes).__name__}."
            )

        nodes = [_parse_node(raw, index) for index, raw in enumerate(raw_nodes)]

        seen: set[str] = set()
        for node in nodes:
            if node.id in seen:
                raise WorkflowError(f"Duplicate node id '{node.id}'.")
            if node.id == END:
                raise WorkflowError(f"'{END}' is reserved and cannot be used as a node id.")
            seen.add(node.id)

        entry = data.get("entry") or nodes[0].id
        if entry not in seen:
            raise WorkflowError(
                f"'workflow.entry' points at '{entry}', which is not a defined node."
            )

        for node in nodes:
            for target in node.successors():
                if target != END and target not in seen:
                    raise WorkflowError(
                        f"Node '{node.id}' routes to '{target}', which is not a defined node."
                    )

        max_steps = data.get("max_steps", 50)
        if not isinstance(max_steps, int) or max_steps < 1:
            raise WorkflowError("'workflow.max_steps' must be a positive integer.")

        raw_agents = agents if agents is not None else data.get("agents") or {}
        if not isinstance(raw_agents, dict):
            raise WorkflowError(
                f"'agents' must be a mapping of name to definition, got "
                f"{type(raw_agents).__name__}."
            )
        parsed_agents = {
            name: AgentSpec.from_dict(name, spec) for name, spec in raw_agents.items()
        }

        for node in nodes:
            if isinstance(node, AgentStep) and node.agent not in parsed_agents:
                known = ", ".join(sorted(parsed_agents)) or "(no agents defined)"
                raise WorkflowError(
                    f"Node '{node.id}' references agent '{node.agent}', which is not "
                    f"defined in 'agents'. Available: {known}"
                )

        return cls(
            nodes=nodes,
            entry=entry,
            output=data.get("output"),
            max_steps=max_steps,
            agents=parsed_agents,
            system=data.get("system"),
        )


def _parse_node(raw: Any, index: int) -> Node:
    """Build one Node from its YAML mapping, validating type-specific fields."""
    where = f"nodes[{index}]"
    if not isinstance(raw, dict):
        raise WorkflowError(f"{where} must be a mapping, got {type(raw).__name__}.")

    node_id = raw.get("id")
    if not node_id or not isinstance(node_id, str):
        raise WorkflowError(f"{where} is missing a string 'id'.")

    node_type = raw.get("type")
    if not node_type:
        raise WorkflowError(f"Node '{node_id}' is missing 'type'.")

    common = {
        "id": node_id,
        "output": raw.get("output"),
        "next": raw.get("next"),
    }

    if node_type == "connector_action":
        if not raw.get("method"):
            raise WorkflowError(f"Node '{node_id}' (connector_action) is missing 'method'.")
        inputs = raw.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise WorkflowError(
                f"Node '{node_id}': 'inputs' must be a mapping, got {type(inputs).__name__}."
            )
        return ConnectorAction(
            connector=str(raw.get("connector") or ""),
            method=str(raw["method"]),
            inputs=inputs,
            requires_approval=bool(raw.get("requires_approval", False)),
            on_reject=raw.get("on_reject"),
            **common,
        )

    if node_type == "condition":
        if not raw.get("when"):
            raise WorkflowError(f"Node '{node_id}' (condition) is missing 'when'.")
        if not raw.get("then"):
            raise WorkflowError(f"Node '{node_id}' (condition) is missing 'then'.")
        return Condition(
            when=str(raw["when"]),
            then=str(raw["then"]),
            otherwise=raw.get("else"),
            **common,
        )

    if node_type == "llm_step":
        if not raw.get("prompt"):
            raise WorkflowError(f"Node '{node_id}' (llm_step) is missing 'prompt'.")
        return LLMStep(prompt=str(raw["prompt"]), system=raw.get("system"), **common)

    if node_type == "agent_step":
        if not raw.get("agent"):
            raise WorkflowError(f"Node '{node_id}' (agent_step) is missing 'agent'.")
        if not raw.get("task"):
            raise WorkflowError(f"Node '{node_id}' (agent_step) is missing 'task'.")
        return AgentStep(agent=str(raw["agent"]), task=str(raw["task"]), **common)

    known = "connector_action, condition, llm_step, agent_step"
    raise WorkflowError(f"Node '{node_id}' has unknown type '{node_type}'. Known types: {known}")
