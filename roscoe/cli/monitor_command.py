"""``roscoe monitor`` — dashboards over the local audit log.

Reads the JSONL audit log and aggregates it (Phase 7). Three surfaces over the same
data: the desktop (tkinter) dashboard (default), a browser dashboard (``--web``), and a
one-shot terminal report (``--terminal``). Pure local read — no exporter or network.
"""

from __future__ import annotations

import click

from roscoe.monitoring import aggregate, load_audit, render
from roscoe.monitoring.metrics import DEFAULT_AUDIT_PATH


@click.command("monitor")
@click.option(
    "--path",
    "audit_path",
    default=str(DEFAULT_AUDIT_PATH),
    show_default=True,
    help="Path to the JSONL audit log.",
)
@click.option("--web", is_flag=True, help="Serve a live web dashboard in the browser.")
@click.option("--terminal", "as_text", is_flag=True, help="Print a one-shot terminal report.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Web dashboard host.")
@click.option("--port", default=8080, show_default=True, help="Web dashboard port.")
def monitor_command(audit_path: str, web: bool, as_text: bool, host: str, port: int) -> None:
    """Show cost, latency, and error metrics from local audit logs.

    Opens the live desktop (tkinter) dashboard by default. Pass ``--web`` for a
    browser dashboard, or ``--terminal`` for a one-shot text report. On a headless
    machine the desktop dashboard falls back to the terminal report automatically.
    """
    if web:
        from roscoe.monitoring.web import serve

        serve(audit_path, host=host, port=port)
        return

    if not as_text:
        from roscoe.cli.gui_theme import _TkUnavailable
        from roscoe.cli.monitor_gui import run_monitor_gui

        try:
            run_monitor_gui(audit_path)
            return
        except _TkUnavailable:
            click.echo("No display available — printing terminal report instead.\n")

    records = load_audit(audit_path)
    if not records:
        click.echo(f"No audit records found at {audit_path}. Run an agent first.")
        return
    click.echo(render(aggregate(records)))
