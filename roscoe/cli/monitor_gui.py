"""Native tkinter dashboard for ``roscoe monitor --gui``.

Re-reads the JSONL audit log on a timer and renders KPIs, a cost-per-day bar
chart (drawn on a plain ``Canvas`` — no matplotlib), latency percentiles, error
breakdown, and a recent-runs table. Pure local read; same aggregation as the
terminal and web dashboards, so all three agree.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from roscoe.cli import gui_theme as T
from roscoe.monitoring.metrics import aggregate, load_audit

_REFRESH_MS = 3000
_RECENT_LIMIT = 50
WIN_W, WIN_H = 900, 820


def run_monitor_gui(audit_path: str | Path) -> None:
    """Open the live monitor window. Raises ``T._TkUnavailable`` if no display."""
    audit_path = str(audit_path)
    root = T.make_root("roscoe — monitor", WIN_W, WIN_H)

    T.header(root, "monitor", f"live dashboard · {audit_path}", live=True)

    content, canvas = T.scrollable(root)

    # --- KPI cards ---
    kpis = T.kpi_row(content)
    k_runs = T.kpi(kpis, "total runs", "—")
    k_err = T.kpi(kpis, "error rate", "—")
    k_cost = T.kpi(kpis, "total cost", "—")
    k_p95 = T.kpi(kpis, "max p95", "—")
    k_rec = T.kpi(kpis, "records", "—")

    # --- Cost per day (Canvas bar chart) ---
    chart_body = T.card(content, "Cost per day (USD)")
    chart = tk.Canvas(chart_body, bg=T.CARD_BG, height=180, highlightthickness=0)
    chart.pack(fill="x")

    # --- Latency + errors side by side ---
    split = tk.Frame(content, bg=T.BG)
    split.pack(fill="x", padx=24, pady=(0, 12))
    split.columnconfigure(0, weight=1)
    split.columnconfigure(1, weight=1)

    lat_tree = _table(split, ("agent", "p50", "p95", "p99", "n"),
                      "Latency by agent (ms)", 0)
    err_tree = _table(split, ("type", "count"), "Errors by type", 1)

    # --- Recent runs ---
    runs_body = T.card(content, "Recent runs")
    runs_tree = _tree(runs_body, ("time", "agent", "user", "status", "tokens", "cost"),
                      heights=12)
    for status, (bg, fg) in T.STATUS_COLORS.items():
        runs_tree.tag_configure(status, background=bg, foreground=fg)

    # --- Footer buttons ---
    footer = tk.Frame(root, bg=T.BG)
    footer.pack(fill="x", side="bottom")
    tk.Frame(footer, bg=T.BORDER, height=1).pack(fill="x")
    inner = tk.Frame(footer, bg=T.BG)
    inner.pack(anchor="e", padx=24, pady=12)
    status_lbl = tk.Label(inner, text="", font=(T.FONT, 9), fg=T.MUTED, bg=T.BG)
    status_lbl.pack(side="left", padx=(0, 14))

    state = {"job": None}  # after() handle for cancellation

    def refresh() -> None:
        records = load_audit(audit_path)
        m = aggregate(records)

        k_runs.config(text=str(m.total_runs))
        err_color = (T.STATUS_COLORS["error"][1] if m.error_rate_pct > 10
                     else "#B45309" if m.error_rate_pct > 0 else "#166534")
        k_err.config(text=f"{m.error_rate_pct}%", fg=err_color)
        k_cost.config(text=f"${m.total_cost_usd:.4f}")
        k_p95.config(text=f"{round(m.max_p95_latency_ms)} ms")
        k_rec.config(text=str(len(records)))

        _draw_bars(chart, m.cost_by_day)
        _fill(lat_tree, [
            (a, l["p50"], l["p95"], l["p99"], l["count"])
            for a, l in sorted(m.latency_ms_by_agent.items())
        ])
        _fill(err_tree, sorted(m.errors_by_type.items(), key=lambda x: -x[1]))

        recent = list(reversed(records[-_RECENT_LIMIT:]))
        runs_tree.delete(*runs_tree.get_children())
        for r in recent:
            status = str(r.get("status", "unknown"))
            runs_tree.insert("", "end", tags=(status,), values=(
                _fmt_time(r.get("start_time")),
                r.get("agent_name", "—"),
                r.get("user_id") or "—",
                status,
                r.get("total_tokens") or 0,
                f"${(r.get('cost_usd') or 0.0):.4f}",
            ))

        from datetime import datetime
        status_lbl.config(text=f"updated {datetime.now().strftime('%H:%M:%S')} · auto-refresh 3s")
        state["job"] = root.after(_REFRESH_MS, refresh)

    def on_close() -> None:
        if state["job"]:
            root.after_cancel(state["job"])
        T.unbind_wheel(canvas)
        root.destroy()

    T.primary_button(inner, "Refresh now", refresh).pack(side="right", padx=(8, 0))
    T.ghost_button(inner, "Close", on_close).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", on_close)

    # Redraw bars when the window resizes.
    chart.bind("<Configure>", lambda _e: _draw_bars(chart, aggregate(load_audit(audit_path)).cost_by_day))

    refresh()
    root.mainloop()


# --- widget helpers -----------------------------------------------------------


def _table(parent: tk.Widget, columns: tuple[str, ...], title: str, col: int) -> ttk.Treeview:
    """A titled card holding a Treeview, gridded into the split frame."""
    outer = tk.Frame(parent, bg=T.CARD_BG, highlightbackground=T.BORDER, highlightthickness=1)
    outer.grid(row=0, column=col, sticky="nsew", padx=(0, 10) if col == 0 else (0, 0))
    tk.Label(outer, text=title, font=(T.FONT, 12, "bold"), fg=T.HEADING, bg=T.CARD_BG,
             anchor="w").pack(fill="x", padx=16, pady=(14, 6))
    body = tk.Frame(outer, bg=T.CARD_BG)
    body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
    return _tree(body, columns, heights=7)


def _tree(parent: tk.Widget, columns: tuple[str, ...], heights: int = 8) -> ttk.Treeview:
    tree = ttk.Treeview(parent, columns=columns, show="headings", height=heights,
                        style="roscoe.Treeview")
    for c in columns:
        tree.heading(c, text=c.upper())
        anchor = "w" if c in ("agent", "type", "user", "status") else "center"
        width = 150 if c in ("agent", "time", "type") else 80
        tree.column(c, anchor=anchor, width=width, stretch=True)
    tree.pack(fill="both", expand=True)
    return tree


def _fill(tree: ttk.Treeview, rows: list[tuple]) -> None:
    tree.delete(*tree.get_children())
    for row in rows:
        tree.insert("", "end", values=row)


def _draw_bars(canvas: tk.Canvas, cost_by_day: dict[str, float]) -> None:
    """Draw a simple bar chart of cost per day on the canvas."""
    canvas.delete("all")
    w = canvas.winfo_width() or 800
    h = canvas.winfo_height() or 180
    pad_b, pad_t, pad_x = 30, 20, 10
    days = sorted(cost_by_day.items())
    if not days:
        canvas.create_text(w // 2, h // 2, text="no cost data yet",
                           fill=T.MUTED, font=(T.FONT, 11))
        return

    max_c = max((c for _, c in days), default=0.0) or 1e-9
    n = len(days)
    avail = w - 2 * pad_x
    bw = min(60, avail / n)
    gap = (avail - bw * n) / (n + 1)

    # baseline
    canvas.create_line(pad_x, h - pad_b, w - pad_x, h - pad_b, fill=T.BORDER)

    for i, (day, cost) in enumerate(days):
        x0 = pad_x + gap + i * (bw + gap)
        x1 = x0 + bw
        bar_h = (cost / max_c) * (h - pad_b - pad_t)
        y0 = h - pad_b - bar_h
        canvas.create_rectangle(x0, y0, x1, h - pad_b, fill=T.BAR_FILL, outline=T.BAR_FILL_TOP)
        canvas.create_text((x0 + x1) / 2, y0 - 8, text=f"${cost:.3f}",
                           fill="#1E40AF", font=(T.FONT, 8))
        canvas.create_text((x0 + x1) / 2, h - pad_b + 12, text=day[5:],
                           fill=T.MUTED, font=(T.FONT, 8))


def _fmt_time(iso: str | None) -> str:
    if not iso:
        return "—"
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return str(iso)[11:19] or str(iso)
