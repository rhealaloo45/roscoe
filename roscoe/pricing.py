"""User-editable LLM pricing overrides.

Built-in rates in ``middleware.cost_tracker.COST_TABLE`` drift constantly and can't
cover every model or a bring-your-own provider. This module layers a user-owned JSON
file on top: ``~/.roscoe/prices.json`` (override with ``ROSCOE_PRICES_PATH``). Anything
in that file wins over — and extends — the built-ins, so a user can price a brand-new
model, a custom provider, or correct a stale rate without touching the SDK.

Shape mirrors ``COST_TABLE`` — rates are per 1,000 tokens::

    {"openai": {"gpt-4o": {"input": 0.005, "output": 0.015}},
     "my_provider": {"my-model": {"input": 0.001, "output": 0.002}}}

The `roscoe prices` GUI reads and writes this file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def prices_path() -> Path:
    """Location of the user price file (env override → default under home)."""
    env = os.environ.get("ROSCOE_PRICES_PATH")
    if env:
        return Path(env)
    return Path.home() / ".roscoe" / "prices.json"


def load_custom_prices() -> dict[str, dict[str, dict[str, float]]]:
    """Load the user's price overrides, or an empty dict if none/invalid."""
    p = prices_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_custom_prices(prices: dict[str, dict[str, dict[str, float]]]) -> Path:
    """Write price overrides to disk (creating the directory). Returns the path."""
    p = prices_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prices, indent=2, sort_keys=True), encoding="utf-8")
    return p


def apply_custom_prices(cost_table: dict[str, Any]) -> dict[str, Any]:
    """Merge the user's overrides into ``cost_table`` in place, then return it.

    Provider-level merge is shallow-per-model: a user model rate replaces a built-in
    of the same name; models the user doesn't mention are left untouched.
    """
    for provider, models in load_custom_prices().items():
        if not isinstance(models, dict):
            continue
        cost_table.setdefault(provider, {}).update(models)
    return cost_table
