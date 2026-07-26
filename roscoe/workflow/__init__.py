"""Declarative workflows — agent behaviour defined in YAML instead of Python.

A workflow describes *what the agent does* as a graph of typed nodes, rather than
leaving that to `@tool` functions plus an autonomous loop. It layers over roscoe's
existing machinery — connectors supply the actions, ``ApprovalGate`` supplies HITL,
``ProviderFactory`` supplies the model — and adds no new agent runtime.

Projects without a ``workflow:`` block are unaffected; this is opt-in.

See ``docs/WORKFLOW_SPEC.md`` for the YAML shape.
"""

from roscoe.workflow.executor import PendingNode, WorkflowExecutor, WorkflowResult
from roscoe.workflow.expressions import ExpressionError, evaluate, render
from roscoe.workflow.schema import (
    END,
    Condition,
    ConnectorAction,
    LLMStep,
    Node,
    Workflow,
    WorkflowError,
)

__all__ = [
    "END",
    "Condition",
    "ConnectorAction",
    "ExpressionError",
    "LLMStep",
    "Node",
    "PendingNode",
    "Workflow",
    "WorkflowError",
    "WorkflowExecutor",
    "WorkflowResult",
    "evaluate",
    "render",
]
