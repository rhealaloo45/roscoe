"""Declarative workflows — agent behaviour defined in YAML instead of Python.

A workflow describes *what the agent does* as a graph of typed nodes, rather than
leaving that to `@tool` functions plus an autonomous loop. It layers over roscoe's
existing machinery — connectors supply the actions, ``ApprovalGate`` supplies HITL,
``ProviderFactory`` supplies the model, and ``ReactExecutor`` still runs any stretch
of the graph that needs an agent to decide for itself.

Projects without a ``workflow:`` block are unaffected; this is opt-in.

See ``docs/WORKFLOW_SPEC.md`` for the YAML shape.
"""

from roscoe.workflow.diagram import to_mermaid
from roscoe.workflow.executor import PendingNode, WorkflowExecutor, WorkflowResult
from roscoe.workflow.expressions import (
    ExpressionError,
    check_syntax,
    evaluate,
    render,
)
from roscoe.workflow.loader import find_workflow_file, has_workflow, load_workflow
from roscoe.workflow.registry import ConnectorError, available_types, build_connectors
from roscoe.workflow.schema import (
    END,
    AgentSpec,
    AgentStep,
    Condition,
    ConnectorAction,
    LLMStep,
    Node,
    Workflow,
    WorkflowError,
)
from roscoe.workflow.validate import ERROR, WARNING, Issue, validate_workflow

__all__ = [
    "END",
    "ERROR",
    "WARNING",
    "AgentSpec",
    "AgentStep",
    "Condition",
    "ConnectorAction",
    "ConnectorError",
    "ExpressionError",
    "Issue",
    "LLMStep",
    "Node",
    "PendingNode",
    "Workflow",
    "WorkflowError",
    "WorkflowExecutor",
    "WorkflowResult",
    "available_types",
    "build_connectors",
    "check_syntax",
    "evaluate",
    "find_workflow_file",
    "has_workflow",
    "load_workflow",
    "render",
    "to_mermaid",
    "validate_workflow",
]
