"""Workflow schema — parsing a ``workflow:`` block into validated node objects.

Everything structural is checked here, at parse time: unknown node types, missing
required fields, duplicate ids, and edges pointing at nodes that do not exist. That
keeps the executor free of defensive checks and gives ``roscoe validate`` (Phase 3)
something to call without running anything.

See ``docs/WORKFLOW_SPEC.md`` for the YAML shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Reserved successor id meaning "stop the run".
END = "END"

_EVERY_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_EVERY_PATTERN = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_AT_PATTERN = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")


class WorkflowError(ValueError):
    """Raised when a workflow definition is structurally invalid."""


def parse_every(text: str) -> int:
    """Turn a trigger's ``every`` into seconds. ``"30s" "15m" "2h" "1d"``.

    Deliberately not cron: a plain interval covers what a scheduled agent
    actually needs, reads unambiguously to someone who has never seen a
    crontab, and needs no parsing dependency.
    """
    match = _EVERY_PATTERN.match(str(text or ""))
    if not match:
        raise WorkflowError(
            f"'{text}' is not a valid interval. Use a number followed by "
            f"s, m, h or d — for example '30m', '2h', '1d'."
        )
    amount = int(match.group(1))
    if amount < 1:
        raise WorkflowError(f"'{text}' must be at least 1 — an interval of zero never fires.")
    return amount * _EVERY_UNITS[match.group(2).lower()]


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
class Trigger(Node):
    """Declares *when* a workflow runs when nobody is there to ask it.

    Executing one does nothing — it hands straight on to its successor.
    ``kind="schedule"`` is metadata that ``roscoe schedule`` reads to decide how
    often to start a run; ``kind="webhook"`` instead tells ``roscoe run`` to
    expose a ``POST /webhook`` endpoint that starts a run with the request
    body as ``input``. Either way the workflow itself is an ordinary graph
    that ``roscoe run`` can also execute on demand. Keeping it as a node
    rather than a top-level setting is what lets the canvas show it the way
    every other step is shown, instead of hiding it in a config screen.
    """

    kind: str = "schedule"
    #: Interval between runs — "30s", "15m", "2h", "1d". Only meaningful for a
    #: schedule trigger; a webhook trigger fires on request instead.
    every: str = ""
    #: Wall-clock time for daily runs, "HH:MM" (24h). Only meaningful with
    #: ``every: 1d``; without it a daily trigger fires 24h after it started.
    at: str | None = None

    @property
    def type(self) -> str:
        return "trigger"


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
    #: Rendered and written to `output` instead of the tool's raw return value.
    #: A connector method's return is API-shaped — an id, a status code — which is
    #: fine for a later node to read, but wrong to show a person as "the result".
    output_message: str | None = None

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
    #: When "json", the reply is parsed before being written to state, so a
    #: prompt asking for structured output can be read back as {{ out.field }}
    #: instead of every downstream node re-parsing the same string.
    parse: str | None = None

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
class Parallel(Node):
    """Run several existing nodes at once and merge their outputs into a dict.

    Each branch names another node defined elsewhere in ``nodes`` — the same
    id a sequential ``next`` could point at — run concurrently rather than
    one after another. A branch node's own ``output:`` still lands in the
    shared state exactly as it would running normally; this node's own
    ``output`` additionally collects ``{branch_name: that node's output}``
    once every branch has finished. A branch node's own routing (``next`` /
    ``then`` / ``else``) is ignored — only this node's own ``next`` carries
    the run forward once the branches join back up.
    """

    branches: dict[str, str] = field(default_factory=dict)

    @property
    def type(self) -> str:
        return "parallel"

    def successors(self) -> list[str]:
        return [s for s in (self.next, *self.branches.values()) if s]


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

    @property
    def trigger(self) -> "Trigger | None":
        """The workflow's schedule, if it declares one. ``roscoe schedule`` reads this."""
        return next((n for n in self.nodes if isinstance(n, Trigger)), None)

    def next_after(self, node: Node) -> str:
        """Successor when a node does not choose one itself: the following node, else END."""
        if node.next:
            return node.next
        index = self.nodes.index(node)
        if index + 1 < len(self.nodes):
            return self.nodes[index + 1].id
        return END

    def to_dict(self) -> dict[str, Any]:
        """Serialise back to the ``workflow:`` mapping this was parsed from.

        The visual builder edits a graph and writes the file, so this has to be a
        faithful inverse of :meth:`from_dict` — anything dropped here is silently
        deleted from a user's workflow the first time they hit save.
        """
        data: dict[str, Any] = {"entry": self.entry}
        if self.system:
            data["system"] = self.system
        if self.output:
            data["output"] = self.output
        if self.max_steps != 50:
            data["max_steps"] = self.max_steps
        data["nodes"] = [_node_to_dict(node) for node in self.nodes]
        return data

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


def _node_to_dict(node: Node) -> dict[str, Any]:
    """One node as a YAML-ready mapping, omitting anything left at its default."""
    data: dict[str, Any] = {"id": node.id, "type": node.type}

    if isinstance(node, Trigger):
        if node.kind != "schedule":
            data["kind"] = node.kind
        if node.every:
            data["every"] = node.every
        if node.at:
            data["at"] = node.at
    elif isinstance(node, ConnectorAction):
        if node.connector:
            data["connector"] = node.connector
        data["method"] = node.method
        if node.inputs:
            data["inputs"] = node.inputs
        if node.requires_approval:
            data["requires_approval"] = True
        if node.output_message:
            data["output_message"] = node.output_message
    elif isinstance(node, Condition):
        data["when"] = node.when
        data["then"] = node.then
        if node.otherwise:
            data["else"] = node.otherwise
    elif isinstance(node, LLMStep):
        data["prompt"] = node.prompt
        if node.system:
            data["system"] = node.system
        if node.parse:
            data["parse"] = node.parse
    elif isinstance(node, AgentStep):
        data["agent"] = node.agent
        data["task"] = node.task
    elif isinstance(node, Parallel):
        data["branches"] = node.branches

    if node.output:
        data["output"] = node.output
    if node.next:
        data["next"] = node.next
    if isinstance(node, ConnectorAction) and node.on_reject:
        data["on_reject"] = node.on_reject
    return data


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

    if node_type == "trigger":
        kind = raw.get("kind") or "schedule"
        if kind not in ("schedule", "webhook"):
            raise WorkflowError(
                f"Node '{node_id}' (trigger) has kind='{kind}'. Use 'schedule' or 'webhook'."
            )
        every = raw.get("every")
        if kind == "schedule":
            if not every:
                raise WorkflowError(
                    f"Node '{node_id}' (trigger) is missing 'every' — how often it should "
                    f"run, for example '1d' or '30m'."
                )
            parse_every(every)  # reject a bad interval here, not at schedule time
        at = raw.get("at")
        if at is not None and not _AT_PATTERN.match(str(at)):
            raise WorkflowError(
                f"Node '{node_id}' (trigger) has at='{at}'. Use 24-hour HH:MM, "
                f"for example '06:00'."
            )
        return Trigger(
            kind=kind, every=str(every) if every else "", at=str(at) if at else None, **common
        )

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
            output_message=raw.get("output_message"),
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
        parse = raw.get("parse")
        if parse is not None and parse != "json":
            raise WorkflowError(
                f"Node '{node_id}' (llm_step) has parse='{parse}'. Only 'json' is supported."
            )
        return LLMStep(
            prompt=str(raw["prompt"]), system=raw.get("system"), parse=parse, **common
        )

    if node_type == "agent_step":
        if not raw.get("agent"):
            raise WorkflowError(f"Node '{node_id}' (agent_step) is missing 'agent'.")
        if not raw.get("task"):
            raise WorkflowError(f"Node '{node_id}' (agent_step) is missing 'task'.")
        return AgentStep(agent=str(raw["agent"]), task=str(raw["task"]), **common)

    if node_type == "parallel":
        branches = raw.get("branches")
        if not isinstance(branches, dict) or not branches:
            raise WorkflowError(
                f"Node '{node_id}' (parallel) needs a non-empty 'branches' mapping of "
                f"branch name to the id of another node to run concurrently."
            )
        return Parallel(branches={str(k): str(v) for k, v in branches.items()}, **common)

    known = "trigger, connector_action, condition, llm_step, agent_step, parallel"
    raise WorkflowError(f"Node '{node_id}' has unknown type '{node_type}'. Known types: {known}")
