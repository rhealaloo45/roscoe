"""Eval reporting — render a report to text and persist it to JSON.

Persisted reports are what ``regression.compare_runs`` diffs, so the JSON shape is the
stable contract: ``run_id``, ``timestamp``, ``overall_scores``, ``overall_mean``,
``passed``, and per-case ``scores``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from roscoe.evals.eval_runner import EvalReport


def render_report(report: EvalReport) -> str:
    """Render an EvalReport as a human-readable text block."""
    verdict = "PASS" if report.passed else "FAIL"
    lines = [
        "=== roscoe eval report ===",
        f"run: {report.run_id}   {report.timestamp}",
        f"verdict: {verdict} (overall {report.overall_mean} vs threshold {report.pass_threshold})",
    ]
    if report.overall_scores:
        lines.append("\noverall by scorer:")
        for name, score in sorted(report.overall_scores.items()):
            lines.append(f"  {name:<18} {score}")

    lines.append("\nper case:")
    for cr in report.case_results:
        scores = "  ".join(f"{k}={v}" for k, v in sorted(cr.scores.items())) or "(no scores)"
        lines.append(f"  [{cr.case_id}] {scores}")
    return "\n".join(lines)


def to_dict(report: EvalReport) -> dict:
    """Serialise an EvalReport to a plain dict."""
    return asdict(report)


def save_report(report: EvalReport, path: str | Path) -> Path:
    """Write the report as JSON, creating parent dirs. Returns the path written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(to_dict(report), indent=2), encoding="utf-8")
    return p
