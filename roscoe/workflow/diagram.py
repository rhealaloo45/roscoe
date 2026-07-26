"""Render a workflow as a Mermaid flowchart.

A workflow is meant to be reviewable by people who will not read YAML — the whole
argument for declarative agents falls apart if checking one still requires a
developer. Mermaid is text, renders client-side, and needs no drawing library here.

Shape carries meaning, so the picture answers the questions people actually ask:

* what calls out to a system (rectangle) versus decides (diamond) versus writes prose
  (rounded) versus hands off to an agent (stadium);
* which steps stop for a human — those are outlined and marked;
* which edges are explicit and which fall through to the next node in the file
  (dotted), since a fall-through silently follows file order.
"""

from __future__ import annotations

from roscoe.workflow.schema import (
    END,
    AgentStep,
    Condition,
    ConnectorAction,
    LLMStep,
    Node,
    Workflow,
)

#: Mermaid delimiters per node type: (open, close).
_SHAPES: dict[str, tuple[str, str]] = {
    "connector_action": ("[", "]"),
    "condition": ("{", "}"),
    "llm_step": ("(", ")"),
    "agent_step": ("([", "])"),
}


def to_mermaid(workflow: Workflow) -> str:
    """Return the workflow as Mermaid ``flowchart`` source."""
    lines = ["flowchart TD"]
    lines.extend(f"  {line}" for line in _node_lines(workflow))
    lines.append("")
    lines.extend(f"  {line}" for line in _edge_lines(workflow))
    lines.append("")
    lines.extend(f"  {line}" for line in _styles(workflow))
    return "\n".join(lines)


def _node_lines(workflow: Workflow) -> list[str]:
    lines = []
    for node in workflow.nodes:
        open_, close = _SHAPES.get(node.type, ("[", "]"))
        lines.append(f'{node.id}{open_}"{_label(node)}"{close}')
    lines.append('END_["end"]')
    return lines


def _label(node: Node) -> str:
    """Node caption: its id, then the one detail that says what it does."""
    detail = ""
    suffix = ""
    if isinstance(node, ConnectorAction):
        target = f"{node.connector}.{node.method}" if node.connector else node.method
        detail = f"{target}()"
        if node.requires_approval:
            suffix = " &#128274;"  # padlock — added after escaping, it is markup
    elif isinstance(node, Condition):
        detail = _clip(node.when)
    elif isinstance(node, AgentStep):
        detail = f"agent: {node.agent}"
    elif isinstance(node, LLMStep):
        detail = _clip(" ".join(node.prompt.split()))
    if not detail:
        return node.id
    return f"{node.id}<br/><small>{_escape(detail)}{suffix}</small>"


def _edge_lines(workflow: Workflow) -> list[str]:
    lines = []
    for node in workflow.nodes:
        if isinstance(node, Condition):
            lines.append(f"{node.id} -->|yes| {_target(node.then)}")
            if node.otherwise:
                lines.append(f"{node.id} -->|no| {_target(node.otherwise)}")
            else:
                # No `else`, so a false test follows file order — worth showing as
                # the implicit edge it is.
                lines.append(f"{node.id} -.->|no| {_target(workflow.next_after(node))}")
            continue

        gated = isinstance(node, ConnectorAction) and (
            node.requires_approval or node.on_reject
        )
        onward = node.next or workflow.next_after(node)
        arrow = "-->" if node.next else "-.->"
        label = "|approved|" if gated else ""
        lines.append(f"{node.id} {arrow}{label} {_target(onward)}")

        if isinstance(node, ConnectorAction) and node.on_reject:
            lines.append(f"{node.id} -->|rejected| {_target(node.on_reject)}")
    return lines


def _styles(workflow: Workflow) -> list[str]:
    """Outline the entry point and anything that pauses for a human."""
    lines = [
        "classDef gated stroke:#b45309,stroke-width:2px",
        "classDef entry stroke:#2563eb,stroke-width:2px",
    ]
    gated = [
        n.id for n in workflow.nodes
        if isinstance(n, ConnectorAction) and n.requires_approval
    ]
    if gated:
        lines.append(f"class {','.join(gated)} gated")
    lines.append(f"class {workflow.entry} entry")
    return lines


def _target(node_id: str) -> str:
    """``END`` is a reserved word in the schema; Mermaid needs a real node id."""
    return "END_" if node_id == END else node_id


def _clip(text: str, limit: int = 46) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _escape(text: str) -> str:
    """Neutralise characters that would end a Mermaid label early."""
    return (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
        .replace("(", "&#40;")
        .replace(")", "&#41;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("|", "&#124;")
    )
