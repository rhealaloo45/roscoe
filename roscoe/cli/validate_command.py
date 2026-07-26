"""``roscoe validate`` — pre-flight check a workflow without running it.

Reports structural problems, expression syntax errors, unreachable nodes, and —
when connectors can be built from the config — unknown methods and missing inputs.
Exits non-zero if any error-level issue is found, so it drops straight into CI.
"""

from __future__ import annotations

import click

from roscoe.config.loader import ConfigError
from roscoe.workflow.loader import load_workflow
from roscoe.workflow.schema import WorkflowError
from roscoe.workflow.validate import ERROR, Issue, validate_workflow


@click.command("validate")
@click.option("--config", default="agent_config.yaml", show_default=True,
              help="Path to the agent config YAML.")
@click.option("--workflow", "workflow_path", default=None,
              help="Workflow file to check. Defaults to the config's 'workflow:' "
                   "block, or a sibling workflow.yaml.")
@click.option("--tools", "tools_ref", default="tools.my_tools:TOOLS", show_default=True,
              help="module:attribute resolving to the project's own tools.")
def validate_command(config: str, workflow_path: str | None, tools_ref: str | None) -> None:
    """Check a workflow definition for problems before running it."""
    try:
        workflow, cfg = load_workflow(config, workflow_path)
    except (WorkflowError, ConfigError) as exc:
        raise click.ClickException(str(exc)) from exc

    source = workflow_path or config
    click.secho(f"roscoe validate — {source}", fg="blue", bold=True)
    click.secho(
        f"  {len(workflow.nodes)} node(s), entry='{workflow.entry}'"
        + (f", {len(workflow.agents)} agent(s)" if workflow.agents else ""),
        dim=True,
    )

    connectors, note = _build_connectors(cfg)
    if note:
        click.secho(f"  {note}", dim=True)

    tools = _load_extra_tools(tools_ref)
    if tools:
        click.secho(f"  tools: {', '.join(sorted(t.name for t in tools))}", dim=True)

    issues = validate_workflow(workflow, connectors=connectors, tools=tools)
    _report(issues)


def _report(issues: list[Issue]) -> None:
    """Print findings grouped by severity and exit non-zero on any error."""
    if not issues:
        click.secho("\n  No problems found.", fg="green")
        return

    errors = [i for i in issues if i.level == ERROR]
    warnings = [i for i in issues if i.level != ERROR]

    click.echo()
    for issue in errors:
        where = f" [{issue.node}]" if issue.node else ""
        click.secho(f"  error{where}: {issue.message}", fg="red")
    for issue in warnings:
        where = f" [{issue.node}]" if issue.node else ""
        click.secho(f"  warning{where}: {issue.message}", fg="yellow")

    click.echo()
    summary = f"  {len(errors)} error(s), {len(warnings)} warning(s)."
    if errors:
        click.secho(summary, fg="red")
        raise SystemExit(1)
    click.secho(summary, fg="yellow")


def _build_connectors(config: dict) -> tuple[dict | None, str]:
    """Best-effort build of the config's ``connectors:`` block.

    Method and argument checks need real tool schemas, but building a connector needs
    credentials that may not be present on the machine running validation. A failure
    here downgrades to structure-only checking rather than failing the command.
    """
    block = config.get("connectors") or {}
    if not block:
        return None, "connectors: none declared — checking structure only."

    from roscoe.workflow.registry import build_connectors

    try:
        connectors = build_connectors(block)
    except Exception as exc:  # noqa: BLE001 — validation must survive bad credentials
        return None, f"connectors: could not build ({type(exc).__name__}) — checking structure only."
    return connectors, f"connectors: {', '.join(sorted(connectors))}"


def _load_extra_tools(tools_ref: str | None) -> list:
    """Resolve ``--tools module:attr``, tolerating a project that has none."""
    if not tools_ref:
        return []
    from roscoe.cli.eval_command import _load_tools

    try:
        return _load_tools(tools_ref)
    except (ImportError, AttributeError, ValueError):
        return []
