"""``roscoe schedule`` — run a workflow on a repeating interval.

The counterpart to a ``trigger`` node on the canvas: the node records *when* a
workflow should run, this command is what actually keeps it running. Everything
else is unchanged — each firing is an ordinary ``WorkflowRunner.run()``, so the
audit log, cost tracking and ``roscoe monitor`` all keep working with no special
handling for scheduled runs.

Sleeping in a loop rather than shelling out to cron keeps this portable (the
same command works on Windows, macOS and Linux) and keeps the schedule visible
in the workflow itself rather than in a machine's crontab. For a machine that
should survive a reboot, wrapping this in a service manager — or exporting the
workflow and using the OS scheduler directly — is still the right answer, and
the docs say so.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import click

from roscoe.workflow.schema import Trigger, parse_every


def _seconds_until(at: str, now: datetime) -> float:
    """Seconds from ``now`` to the next occurrence of wall-clock ``at`` ("HH:MM")."""
    hour, minute = (int(part) for part in at.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _describe(trigger: Trigger) -> str:
    return f"every {trigger.every}" + (f", at {trigger.at}" if trigger.at else "")


@click.command("schedule")
@click.option("--config", default="agent_config.yaml", show_default=True,
              help="Path to the agent config.")
@click.option("--every", "every_override", default=None,
              help="Override the trigger's interval — 30s, 15m, 2h, 1d.")
@click.option("--at", "at_override", default=None,
              help="Override the trigger's daily time, as 24-hour HH:MM.")
@click.option("--once", is_flag=True,
              help="Wait for the first firing, run once, then exit.")
@click.option("--now", "run_now", is_flag=True,
              help="Run immediately on start instead of waiting for the first interval.")
@click.option("--user", "user_id", default="scheduler", show_default=True,
              help="user_id recorded in the audit log.")
def schedule_command(
    config: str, every_override: str | None, at_override: str | None,
    once: bool, run_now: bool, user_id: str,
) -> None:
    """Run this project's workflow repeatedly, on a schedule.

    Reads the interval from the workflow's ``trigger`` node. ``--every``/``--at``
    override it without editing the file.
    """
    from roscoe.workflow.runner import WorkflowRunner

    try:
        agent = WorkflowRunner.from_config(config)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc

    trigger = agent.workflow.trigger
    every = every_override or (trigger.every if trigger else None)
    if not every:
        raise click.ClickException(
            "This workflow has no trigger node and no --every was given, so there "
            "is no schedule to run. Add a Schedule node in `roscoe build`, or pass "
            "--every 1d."
        )
    at = at_override or (trigger.at if trigger else None)

    try:
        interval = parse_every(every)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    label = _describe(trigger) if trigger and not (every_override or at_override) else (
        f"every {every}" + (f", at {at}" if at else "")
    )
    click.secho(f"roscoe schedule — {agent.agent_name}", fg="blue", bold=True)
    click.secho(f"  {label}   (Ctrl-C to stop)", dim=True)

    # `at` pins the first run to a wall-clock time; after that the interval
    # carries it, so a daily job stays on its hour without re-deriving the date.
    delay = 0.0 if run_now else (
        _seconds_until(at, datetime.now()) if at else float(interval)
    )

    try:
        while True:
            if delay > 0:
                nxt = datetime.now() + timedelta(seconds=delay)
                click.secho(f"  next run at {nxt:%Y-%m-%d %H:%M:%S}", dim=True)
                time.sleep(delay)

            started = datetime.now()
            result = agent.run({}, user_id=user_id)
            if result.status == "error":
                # A failed run must not kill the schedule — tomorrow's run is
                # still worth attempting, and the audit log has the detail.
                click.secho(f"  {started:%H:%M:%S}  error: {result.error}", fg="red")
            else:
                click.secho(f"  {started:%H:%M:%S}  {result.status}", fg="green")

            if once:
                return
            delay = float(interval)
    except KeyboardInterrupt:
        click.echo("\nstopped.")
