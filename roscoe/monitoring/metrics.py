"""Offline aggregation of audit logs into fleet-level metrics.

This is the heart of monitoring: pure, deterministic, network-free functions that read
the JSONL audit log (written by ``roscoe.middleware.audit_logger``) and compute
cost / latency / error / token metrics. Exporters and the CLI dashboard are thin layers
on top of :func:`aggregate`, so the hard logic is fully unit-testable without a server.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_PATH = Path("logs") / "audit.jsonl"


@dataclass
class Metrics:
    """Aggregated view over a set of audit records."""

    total_runs: int = 0
    runs_by_status: dict[str, int] = field(default_factory=dict)
    cost_by_day: dict[str, float] = field(default_factory=dict)
    cost_by_agent: dict[str, float] = field(default_factory=dict)
    cost_by_user: dict[str, float] = field(default_factory=dict)
    tokens_by_day: dict[str, int] = field(default_factory=dict)
    latency_ms_by_agent: dict[str, dict[str, float]] = field(default_factory=dict)
    error_rate_pct: float = 0.0
    errors_by_type: dict[str, int] = field(default_factory=dict)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(self.cost_by_day.values()), 6)

    @property
    def max_daily_cost_usd(self) -> float:
        return max(self.cost_by_day.values(), default=0.0)

    @property
    def max_p95_latency_ms(self) -> float:
        return max((a.get("p95", 0.0) for a in self.latency_ms_by_agent.values()), default=0.0)


def load_audit(path: str | Path = DEFAULT_AUDIT_PATH) -> list[dict[str, Any]]:
    """Read a JSONL audit log into a list of records. Missing file -> empty list.

    Malformed lines are skipped rather than aborting the whole report.
    """
    p = Path(path)
    if not p.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def aggregate(records: list[dict[str, Any]]) -> Metrics:
    """Compute fleet metrics from audit records. Pure and deterministic."""
    m = Metrics(total_runs=len(records))
    latencies: dict[str, list[float]] = {}

    for rec in records:
        status = rec.get("status", "unknown")
        m.runs_by_status[status] = m.runs_by_status.get(status, 0) + 1

        day = _day(rec.get("start_time"))
        cost = rec.get("cost_usd") or 0.0
        if day:
            m.cost_by_day[day] = round(m.cost_by_day.get(day, 0.0) + cost, 6)
            m.tokens_by_day[day] = m.tokens_by_day.get(day, 0) + int(rec.get("total_tokens") or 0)

        agent = rec.get("agent_name", "unknown")
        m.cost_by_agent[agent] = round(m.cost_by_agent.get(agent, 0.0) + cost, 6)

        user = rec.get("user_id")
        if user is not None:
            m.cost_by_user[user] = round(m.cost_by_user.get(user, 0.0) + cost, 6)

        lat = _latency_ms(rec.get("start_time"), rec.get("end_time"))
        if lat is not None:
            latencies.setdefault(agent, []).append(lat)

        if status == "error":
            m.errors_by_type[_error_type(rec.get("error"))] = (
                m.errors_by_type.get(_error_type(rec.get("error")), 0) + 1
            )

    for agent, values in latencies.items():
        values.sort()
        m.latency_ms_by_agent[agent] = {
            "p50": round(_percentile(values, 50), 2),
            "p95": round(_percentile(values, 95), 2),
            "p99": round(_percentile(values, 99), 2),
            "count": len(values),
        }

    errors = m.runs_by_status.get("error", 0)
    m.error_rate_pct = round(100.0 * errors / m.total_runs, 2) if m.total_runs else 0.0
    return m


# --- helpers ---


def _day(iso: str | None) -> str | None:
    if not iso:
        return None
    return iso[:10]  # YYYY-MM-DD


def _latency_ms(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except ValueError:
        return None
    return delta.total_seconds() * 1000.0


def _error_type(error: str | None) -> str:
    if not error:
        return "Unknown"
    # audit errors are stored as "TypeName: message"
    return error.split(":", 1)[0].strip() or "Unknown"


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile. ``sorted_values`` must be ascending."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac
