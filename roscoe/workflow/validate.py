"""Static validation for workflows — catch problems before anything runs.

YAML has no compiler, which is the usual reason "no-code" pipelines fail in
production: a typo'd field or a dead edge only shows up mid-run, halfway through
whatever side effects already happened. This module is the missing pre-flight check.

Two tiers:

* **Structural** — handled by :meth:`Workflow.from_dict` at parse time (unknown node
  types, missing fields, duplicate ids, edges to nowhere). Those raise.
* **Advisory** — everything here: expression syntax, unreachable nodes, and, when
  connectors are supplied, whether each referenced method actually exists and its
  required arguments are provided.

Connector-aware checks are skipped rather than failed when no connectors are given,
so validation still works without credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from roscoe.workflow.expressions import ExpressionError, check_syntax, iter_expressions
from roscoe.workflow.schema import (
    END,
    AgentStep,
    Condition,
    ConnectorAction,
    LLMStep,
    Workflow,
)

ERROR = "error"
WARNING = "warning"


@dataclass
class Issue:
    """One validation finding."""

    level: str
    message: str
    node: str | None = None

    def __str__(self) -> str:
        where = f" [{self.node}]" if self.node else ""
        return f"{self.level}{where}: {self.message}"


def validate_workflow(
    workflow: Workflow,
    *,
    connectors: dict[str, Any] | None = None,
    tools: list[Any] | None = None,
) -> list[Issue]:
    """Check a parsed workflow, returning every issue found.

    An empty list means the workflow is structurally sound and every expression
    parses. It does not guarantee the run will succeed — state values are only known
    at runtime.
    """
    issues: list[Issue] = []
    issues.extend(_check_expressions(workflow))
    issues.extend(_check_implicit_branches(workflow))
    issues.extend(_check_reachability(workflow))
    if connectors is not None or tools:
        indexed = _index(connectors or {})
        extra = {tool.name: tool for tool in (tools or [])}
        issues.extend(_check_connectors(workflow, indexed, extra))
        issues.extend(_check_agent_tools(workflow, indexed, set(extra)))
    return issues


def _check_expressions(workflow: Workflow) -> list[Issue]:
    """Every template and condition must parse and stay inside the grammar."""
    issues: list[Issue] = []

    for node in workflow.nodes:
        templates: list[Any] = []
        if isinstance(node, ConnectorAction):
            templates.append(node.inputs)
        elif isinstance(node, LLMStep):
            templates.extend([node.prompt, node.system])
        elif isinstance(node, AgentStep):
            templates.append(node.task)

        for template in templates:
            for expression in iter_expressions(template):
                try:
                    check_syntax(expression)
                except ExpressionError as exc:
                    issues.append(Issue(ERROR, str(exc), node.id))

        if isinstance(node, Condition):
            # A `when:` is a bare expression, but tolerate a {{ }} wrapper.
            wrapped = iter_expressions(node.when)
            for expression in wrapped or [node.when]:
                try:
                    check_syntax(expression)
                except ExpressionError as exc:
                    issues.append(Issue(ERROR, str(exc), node.id))

    if workflow.output:
        for expression in iter_expressions(workflow.output):
            try:
                check_syntax(expression)
            except ExpressionError as exc:
                issues.append(Issue(ERROR, f"workflow.output: {exc}"))

    return issues


def _check_implicit_branches(workflow: Workflow) -> list[Issue]:
    """Flag a condition whose false branch depends on the order of the node list.

    With no ``else`` and no ``next``, a false test falls through to whatever node
    happens to come next in the file. That works, but it is invisible: moving nodes
    around silently repoints the branch. Naming the current target makes the
    behaviour checkable, and adding either key silences the warning.
    """
    issues: list[Issue] = []
    for node in workflow.nodes:
        if not isinstance(node, Condition) or node.otherwise or node.next:
            continue
        target = workflow.next_after(node)
        where = "the end of the run" if target == END else f"'{target}'"
        issues.append(
            Issue(
                WARNING,
                f"Condition '{node.id}' has no 'else': a false test falls through to "
                f"{where} because it is next in the list. Set 'else' (or 'next') to "
                f"make that explicit.",
                node.id,
            )
        )
    return issues


def _check_reachability(workflow: Workflow) -> list[Issue]:
    """Warn about nodes no edge leads to — usually a rename that missed a reference."""
    reachable: set[str] = set()
    frontier = [workflow.entry]

    while frontier:
        current = frontier.pop()
        if current in reachable or current == END:
            continue
        reachable.add(current)
        node = workflow.get(current)
        targets = node.successors() or [workflow.next_after(node)]
        # An explicit branch may still fall through, so consider both.
        if isinstance(node, Condition) and not node.otherwise:
            targets = list(targets) + [workflow.next_after(node)]
        frontier.extend(t for t in targets if t != END)

    return [
        Issue(WARNING, f"Node '{node.id}' is never reached from '{workflow.entry}'.", node.id)
        for node in workflow.nodes
        if node.id not in reachable
    ]


def _check_connectors(
    workflow: Workflow,
    indexed: dict[str, dict[str, Any]],
    extra: dict[str, Any],
) -> list[Issue]:
    """Confirm each ``connector_action`` names a real method and supplies its arguments."""
    issues: list[Issue] = []

    for node in workflow.nodes:
        if not isinstance(node, ConnectorAction):
            continue

        tool = None
        if node.connector:
            bag = indexed.get(node.connector)
            if bag is None:
                known = ", ".join(sorted(indexed)) or "(none configured)"
                issues.append(
                    Issue(ERROR, f"Connector '{node.connector}' is not configured. Available: {known}", node.id)
                )
                continue
            tool = bag.get(node.method)
            if tool is None:
                known = ", ".join(sorted(bag)) or "(no methods)"
                issues.append(
                    Issue(
                        ERROR,
                        f"Connector '{node.connector}' has no method '{node.method}'. Available: {known}",
                        node.id,
                    )
                )
                continue
        else:
            # No connector named: the project's own tools take precedence, then any
            # connector — matching how the executor resolves it.
            tool = extra.get(node.method)
            if tool is None:
                for bag in indexed.values():
                    if node.method in bag:
                        tool = bag[node.method]
                        break
            if tool is None:
                known = ", ".join(sorted(_all_names(indexed, extra))) or "(none available)"
                issues.append(
                    Issue(
                        ERROR,
                        f"'{node.method}' is not a known tool or connector method. "
                        f"Available: {known}",
                        node.id,
                    )
                )
                continue

        required, known_args = _tool_arguments(tool)
        for missing in sorted(required - set(node.inputs)):
            issues.append(
                Issue(ERROR, f"'{node.method}' requires input '{missing}', which is not set.", node.id)
            )
        if known_args:
            for unexpected in sorted(set(node.inputs) - known_args):
                issues.append(
                    Issue(
                        WARNING,
                        f"'{node.method}' does not take an argument named '{unexpected}'.",
                        node.id,
                    )
                )

    return issues


def _check_agent_tools(
    workflow: Workflow, indexed: dict[str, dict[str, Any]], extra: set[str]
) -> list[Issue]:
    """Confirm every tool an agent asks for can actually be resolved."""
    issues: list[Issue] = []
    available = {name for bag in indexed.values() for name in bag} | extra

    for spec in workflow.agents.values():
        for ref in spec.tools:
            if "." in ref:
                connector, method = ref.split(".", 1)
                bag = indexed.get(connector)
                if bag is None:
                    issues.append(
                        Issue(ERROR, f"Agent '{spec.name}' wants '{ref}', but connector "
                                     f"'{connector}' is not configured.")
                    )
                elif method not in bag:
                    issues.append(
                        Issue(ERROR, f"Agent '{spec.name}' wants '{ref}', but connector "
                                     f"'{connector}' has no method '{method}'.")
                    )
            elif ref not in available:
                issues.append(
                    Issue(ERROR, f"Agent '{spec.name}' wants tool '{ref}', which is not "
                                 f"available from any configured connector.")
                )

    return issues


def _all_names(indexed: dict[str, dict[str, Any]], extra: dict[str, Any]) -> set[str]:
    """Every callable name a connector-less node could resolve to."""
    names = set(extra)
    for bag in indexed.values():
        names.update(bag)
    return names


def _tool_arguments(tool: Any) -> tuple[set[str], set[str]]:
    """Return ``(required, all)`` argument names from a tool's schema.

    Connector tools are LangChain ``StructuredTool``s, so their JSON schema is already
    there — no separate schema authoring needed to validate a node's ``inputs:``.
    """
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return set(), set()
    try:
        fields = schema.model_fields  # pydantic v2
    except AttributeError:
        return set(), set()
    required = {name for name, field in fields.items() if field.is_required()}
    return required, set(fields)


def _index(connectors: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Same normalisation the executor uses: ``{connector: {method: tool}}``."""
    indexed: dict[str, dict[str, Any]] = {}
    for name, source in connectors.items():
        if isinstance(source, dict):
            indexed[name] = source
            continue
        tools = getattr(source, "tools", source)
        indexed[name] = {tool.name: tool for tool in tools}
    return indexed
