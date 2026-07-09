"""Native tkinter runner for ``roscoe eval --gui``.

Collects the dataset / config / tools inputs, runs the eval suite on a worker
thread (so the window stays responsive), and renders the scored report: a
PASS/FAIL banner, per-scorer means, and a per-case table with score details.
Reuses ``eval_command.run_eval`` unchanged — the GUI is only a front-end.
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path
from tkinter import filedialog, ttk
import tkinter as tk

from roscoe.cli import gui_theme as T

WIN_W, WIN_H = 820, 780


def run_eval_gui(
    *,
    dataset: str | None = None,
    config: str | None = None,
    tools_ref: str | None = None,
    threshold: float = 0.7,
) -> None:
    """Open the eval runner window. Raises ``T._TkUnavailable`` if no display."""
    root = T.make_root("roscoe — eval", WIN_W, WIN_H)
    T.header(root, "eval", "run a scored eval suite against an agent config")

    content, canvas = T.scrollable(root)

    # --- Inputs card ---
    body = T.card(content, "Suite")
    body.columnconfigure(1, weight=1)

    dataset_var = tk.StringVar(value=dataset or "")
    config_var = tk.StringVar(value=config or "")
    tools_var = tk.StringVar(value=tools_ref or "")
    judge_var = tk.BooleanVar(value=False)
    threshold_var = tk.DoubleVar(value=threshold)

    _file_row(body, "Dataset", dataset_var, 0, [("JSON", "*.json")])
    _file_row(body, "Config", config_var, 1, [("YAML", "*.yaml *.yml")])

    tk.Label(body, text="Tools", fg=T.LABEL_FG, bg=T.CARD_BG, anchor="w").grid(
        row=2, column=0, sticky="w", pady=4)
    ttk.Entry(body, textvariable=tools_var).grid(row=2, column=1, sticky="ew", pady=4, columnspan=2)
    tk.Label(body, text="module:attribute — e.g. tools.my_tools:TOOLS",
             fg=T.MUTED, bg=T.CARD_BG, font=(T.FONT, 9)).grid(
        row=3, column=1, sticky="w", columnspan=2)

    ttk.Checkbutton(body, text="LLM-as-judge (also score output quality)",
                    variable=judge_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 2))

    tk.Label(body, text="Pass threshold", fg=T.LABEL_FG, bg=T.CARD_BG, anchor="w").grid(
        row=5, column=0, sticky="w", pady=4)
    ttk.Spinbox(body, from_=0.0, to=1.0, increment=0.05, textvariable=threshold_var,
                width=8).grid(row=5, column=1, sticky="w", pady=4)

    # --- Verdict banner (hidden until a run finishes) ---
    banner = tk.Label(content, text="", font=(T.FONT, 14, "bold"), fg="#FFFFFF",
                      bg=T.BG, anchor="w", padx=16, pady=12)

    # --- Per-scorer means ---
    scorer_body = T.card(content, "Scores by scorer")
    scorer_tree = _tree(scorer_body, ("scorer", "mean"), 4)

    # --- Per-case table ---
    case_body = T.card(content, "Per-case results")
    case_tree = _tree(case_body, ("case", "scores", "detail"), 10)

    # --- Footer ---
    footer = tk.Frame(root, bg=T.BG)
    footer.pack(fill="x", side="bottom")
    tk.Frame(footer, bg=T.BORDER, height=1).pack(fill="x")
    inner = tk.Frame(footer, bg=T.BG)
    inner.pack(fill="x", padx=24, pady=12)

    prog = ttk.Progressbar(inner, mode="indeterminate", length=180)
    run_status = tk.Label(inner, text="", font=(T.FONT, 9), fg=T.MUTED, bg=T.BG)
    run_status.pack(side="left")

    def close() -> None:
        T.unbind_wheel(canvas)
        root.destroy()

    run_btn = T.primary_button(inner, "Run eval", lambda: _start())
    run_btn.pack(side="right", padx=(8, 0))
    T.ghost_button(inner, "Close", close).pack(side="right")

    def _start() -> None:
        ds, cfg = dataset_var.get().strip(), config_var.get().strip()
        if not ds or not cfg:
            _set_banner(banner, "Pick a dataset and a config first.", "#B45309")
            return
        run_btn.config(state="disabled")
        banner.pack_forget()
        run_status.config(text="running…")
        prog.pack(side="left", padx=(10, 0))
        prog.start(12)

        def work() -> None:
            from roscoe.cli.eval_command import run_eval
            try:
                report = run_eval(
                    ds, cfg,
                    tools_ref=tools_var.get().strip() or None,
                    use_judge=judge_var.get(),
                    pass_threshold=float(threshold_var.get()),
                )
                root.after(0, lambda: _show(report))
            except Exception as exc:  # noqa: BLE001 — surface any failure in the UI
                tb = traceback.format_exc()
                root.after(0, lambda: _fail(exc, tb))

        threading.Thread(target=work, daemon=True).start()

    def _finish_run() -> None:
        prog.stop()
        prog.pack_forget()
        run_btn.config(state="normal")

    def _show(report) -> None:  # noqa: ANN001 — EvalReport
        _finish_run()
        run_status.config(text=f"done · run {report.run_id[:8]}")
        verdict = "PASS" if report.passed else "FAIL"
        color = "#16A34A" if report.passed else "#DC2626"
        _set_banner(
            banner,
            f"  {verdict}   overall {report.overall_mean}  vs threshold {report.pass_threshold}",
            color,
        )
        _fill(scorer_tree, sorted(report.overall_scores.items()))
        rows = []
        for cr in report.case_results:
            if cr.scores:
                scores = ", ".join(f"{k}={v}" for k, v in cr.scores.items())
                detail = " | ".join(cr.details.values())
            else:
                scores, detail = "(no scores)", "no applicable scorer"
            rows.append((cr.case_id, scores, detail))
        _fill(case_tree, rows)

    def _fail(exc: Exception, tb: str) -> None:
        _finish_run()
        run_status.config(text="error")
        _set_banner(banner, f"  {type(exc).__name__}: {exc}", "#DC2626")
        _fill(case_tree, [("error", "", line) for line in tb.strip().splitlines()[-6:]])

    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()


# --- helpers ------------------------------------------------------------------


def _file_row(parent: tk.Widget, label: str, var: tk.StringVar, row: int,
              filetypes: list[tuple[str, str]]) -> None:
    tk.Label(parent, text=label, fg=T.LABEL_FG, bg=T.CARD_BG, anchor="w").grid(
        row=row, column=0, sticky="w", pady=4)
    ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=4)

    def browse() -> None:
        path = filedialog.askopenfilename(title=f"Select {label.lower()}", filetypes=filetypes)
        if path:
            var.set(path)

    T.ghost_button(parent, "Browse…", browse).grid(row=row, column=2, sticky="w", padx=(8, 0))


def _tree(parent: tk.Widget, columns: tuple[str, ...], heights: int) -> ttk.Treeview:
    tree = ttk.Treeview(parent, columns=columns, show="headings", height=heights,
                        style="roscoe.Treeview")
    for c in columns:
        tree.heading(c, text=c.upper())
        tree.column(c, anchor="w",
                    width=320 if c == "detail" else 140 if c in ("scores", "case", "scorer") else 90,
                    stretch=True)
    tree.pack(fill="both", expand=True)
    return tree


def _fill(tree: ttk.Treeview, rows: list[tuple]) -> None:
    tree.delete(*tree.get_children())
    for row in rows:
        tree.insert("", "end", values=row)


def _set_banner(banner: tk.Label, text: str, color: str) -> None:
    banner.config(text=text, bg=color)
    banner.pack(fill="x", padx=24, pady=(0, 12))
