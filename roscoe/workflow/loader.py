"""Locating and loading a project's workflow definition.

A workflow can live in either of two places, and both are supported so a project can
keep one file or split them:

* a ``workflow:`` key inside ``agent_config.yaml`` (with ``agents:`` alongside it), or
* a separate ``workflow.yaml`` next to the config — either wrapped in a ``workflow:``
  key or with ``nodes:`` at the top level.

Loading goes through :func:`roscoe.config.loader.load_config`, so ``${ENV_VAR}``
references resolve the same way they do everywhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roscoe.config.loader import ConfigError, load_config
from roscoe.workflow.schema import Workflow, WorkflowError

#: Conventional filename when the workflow lives outside the agent config.
DEFAULT_WORKFLOW_FILE = "workflow.yaml"


def find_workflow_file(config_path: str | Path) -> Path | None:
    """Return the sibling ``workflow.yaml`` for a config, if one exists."""
    candidate = Path(config_path).parent / DEFAULT_WORKFLOW_FILE
    return candidate if candidate.is_file() else None


def has_workflow(config_path: str | Path) -> bool:
    """True if this project defines a workflow — used to pick the execution path.

    Deliberately quiet: a malformed or unreadable config answers "no" so the caller
    falls back to the normal agent path rather than crashing on an unrelated file.
    """
    path = Path(config_path)
    if find_workflow_file(path) is not None:
        return True
    try:
        config = load_config(path)
    except (ConfigError, OSError):
        return False
    return bool(config.get("workflow"))


def load_workflow(
    config_path: str | Path = "agent_config.yaml",
    workflow_path: str | Path | None = None,
    *,
    strict: bool = True,
) -> tuple[Workflow, dict[str, Any]]:
    """Load and parse a project's workflow.

    Returns the parsed :class:`Workflow` and the agent config it belongs to (the
    latter still supplies the model, middleware, and connector settings).

    Args:
        strict: Passed through to :func:`load_config`. False lets read-only
            commands (`roscoe validate`, `roscoe graph`) check a project's
            structure before any secrets have been filled in.

    Raises:
        WorkflowError: if no workflow is defined, or the definition is invalid.
        ConfigError: if the agent config itself cannot be read.
    """
    config: dict[str, Any] = {}
    config_file = Path(config_path)
    if config_file.is_file():
        config = load_config(config_file, strict=strict)

    if workflow_path is not None:
        raw = load_config(workflow_path, strict=strict)
        block, agents = _unwrap(raw)
        return Workflow.from_dict(block, agents or config.get("agents")), config

    if config.get("workflow"):
        return Workflow.from_dict(config["workflow"], config.get("agents")), config

    sibling = find_workflow_file(config_file)
    if sibling is not None:
        raw = load_config(sibling, strict=strict)
        block, agents = _unwrap(raw)
        return Workflow.from_dict(block, agents or config.get("agents")), config

    raise WorkflowError(
        f"No workflow found. Add a 'workflow:' block to {config_file}, or create "
        f"{config_file.parent / DEFAULT_WORKFLOW_FILE}."
    )


def _unwrap(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Accept a standalone workflow file wrapped in ``workflow:`` or bare."""
    if "workflow" in raw:
        return raw["workflow"], raw.get("agents")
    return raw, raw.get("agents")
