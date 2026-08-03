"""Eval datasets — load and validate test cases.

A test case is one input plus optional expectations. Only ``input`` is required; the
other fields turn specific scorers on (e.g. ``expected_tools`` enables the tool-usage
scorer for that case). Datasets are JSON: a top-level list, or ``{"cases": [...]}``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class DatasetError(ValueError):
    """Raised when a dataset file is malformed or a case is missing required fields."""


@dataclass
class EvalCase:
    """A single eval test case."""

    id: str
    input: str
    expected_output: str | None = None
    expected_tools: list[str] | None = None
    context_docs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def load_dataset(path: str | Path) -> list[EvalCase]:
    """Load and validate a dataset file into ``EvalCase`` objects."""
    p = Path(path)
    if not p.exists():
        raise DatasetError(f"Dataset file not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetError(f"Dataset is not valid JSON: {exc}") from exc

    cases_raw = raw.get("cases", raw) if isinstance(raw, dict) else raw
    if not isinstance(cases_raw, list):
        raise DatasetError("Dataset must be a JSON list of cases, or {'cases': [...]}.")

    return [_parse_case(item, i) for i, item in enumerate(cases_raw)]


def _parse_case(item: Any, index: int) -> EvalCase:
    if not isinstance(item, dict):
        raise DatasetError(f"Case #{index} must be an object, got {type(item).__name__}.")
    if "input" not in item or not item["input"]:
        raise DatasetError(f"Case #{index} is missing the required 'input' field.")
    return EvalCase(
        id=str(item.get("id", index)),
        input=str(item["input"]),
        expected_output=item.get("expected_output"),
        expected_tools=item.get("expected_tools"),
        context_docs=item.get("context_docs", []) or [],
        metadata=item.get("metadata", {}) or {},
    )
