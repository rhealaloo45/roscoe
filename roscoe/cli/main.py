"""roscoe CLI entry point — the ``roscoe`` command group."""

from __future__ import annotations

import sys

import click

# Windows consoles default to a legacy codepage (cp1252), which raises
# UnicodeEncodeError the moment a model emits an em dash, a non-breaking hyphen, or
# an emoji — crashing a run that already succeeded, at the point of printing it.
# Force UTF-8 and degrade unprintable characters instead of failing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a reconfigurable text stream
        pass

# Load .env before any config parsing so ${ENV_VAR} references resolve.
# usecwd=True: search from the current working directory (the user's project),
# not from wherever roscoe itself is installed — without it, a non-editable
# install (e.g. `pip install roscoe`) walks up from inside site-packages and
# never finds a project's .env file at all.
try:
    from dotenv import find_dotenv as _find_dotenv
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_find_dotenv(usecwd=True))
except ImportError:
    pass

from roscoe import __version__
from roscoe.cli.build_command import build_command
from roscoe.cli.eval_command import eval_command
from roscoe.cli.google_auth_command import google_auth_command
from roscoe.cli.graph_command import graph_command
from roscoe.cli.init_command import init_command, init_nc_command
from roscoe.cli.monitor_command import monitor_command
from roscoe.cli.pricing_command import prices_command
from roscoe.cli.run_command import run_command
from roscoe.cli.schedule_command import schedule_command
from roscoe.cli.validate_command import validate_command


@click.group()
@click.version_option(__version__, prog_name="roscoe")
def cli() -> None:
    """roscoe — provider-agnostic agent SDK."""


cli.add_command(init_command)
cli.add_command(init_nc_command)
cli.add_command(monitor_command)
cli.add_command(eval_command)
cli.add_command(prices_command)
cli.add_command(run_command)
cli.add_command(schedule_command)
cli.add_command(validate_command)
cli.add_command(graph_command)
cli.add_command(build_command)
cli.add_command(google_auth_command)


if __name__ == "__main__":
    cli()
