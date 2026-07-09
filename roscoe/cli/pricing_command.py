"""``roscoe prices`` — view and edit LLM pricing overrides.

Opens the desktop editor by default; ``--terminal`` prints the effective table and
the path of the override file. User rates live in ``~/.roscoe/prices.json`` and are
layered over the built-in rates on every run.
"""

from __future__ import annotations

import click


@click.command("prices")
@click.option("--terminal", "as_text", is_flag=True, help="Print the price table instead of the GUI.")
def prices_command(as_text: bool) -> None:
    """View or edit LLM pricing (add your own rates for any provider)."""
    if not as_text:
        from roscoe.cli.gui_theme import _TkUnavailable
        from roscoe.cli.pricing_gui import run_pricing_gui

        try:
            run_pricing_gui()
            return
        except _TkUnavailable:
            click.echo("No display available — printing the price table instead.\n")

    from roscoe.middleware.cost_tracker import COST_TABLE
    from roscoe.pricing import load_custom_prices, prices_path

    custom = load_custom_prices()
    click.echo(f"Pricing (per 1,000 tokens). Overrides file: {prices_path()}\n")
    for provider in sorted(COST_TABLE):
        models = COST_TABLE[provider]
        if not models:
            click.echo(f"{provider}: (free / $0.00)")
            continue
        click.echo(f"{provider}:")
        for model in sorted(models):
            r = models[model]
            src = "custom" if provider in custom and model in custom[provider] else "built-in"
            click.echo(f"  {model:<40} in ${r.get('input', 0):<10} out ${r.get('output', 0):<10} [{src}]")
    click.echo("\nEdit with: roscoe prices   (desktop editor)")
