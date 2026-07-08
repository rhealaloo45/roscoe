"""roscoe CLI entry point — the ``roscoe`` command group."""

from __future__ import annotations

import click

# Load .env before any config parsing so ${ENV_VAR} references resolve.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

from roscoe import __version__
from roscoe.cli.eval_command import eval_command
from roscoe.cli.google_auth_command import google_auth_command
from roscoe.cli.init_command import init_command
from roscoe.cli.monitor_command import monitor_command
from roscoe.cli.pricing_command import prices_command
from roscoe.cli.run_command import run_command


@click.group()
@click.version_option(__version__, prog_name="roscoe")
def cli() -> None:
    """roscoe — provider-agnostic agent SDK."""


cli.add_command(init_command)
cli.add_command(monitor_command)
cli.add_command(eval_command)
cli.add_command(prices_command)
cli.add_command(run_command)
cli.add_command(google_auth_command)


if __name__ == "__main__":
    cli()
