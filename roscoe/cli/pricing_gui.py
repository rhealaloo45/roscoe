"""Native tkinter editor for LLM pricing — ``roscoe prices``.

Shows every effective rate (built-in + user overrides) in one table and lets the
user add, edit, or remove their own rates for any provider — a listed one or a
brand-new bring-your-own provider. Saving writes ``~/.roscoe/prices.json``, which
the cost tracker layers over the built-ins on the next run.
"""

from __future__ import annotations

import copy
from tkinter import ttk
import tkinter as tk

from roscoe.cli import gui_theme as T
from roscoe.middleware.cost_tracker import COST_TABLE
from roscoe.pricing import load_custom_prices, prices_path, save_custom_prices

WIN_W, WIN_H = 760, 720


def run_pricing_gui() -> None:
    """Open the pricing editor. Raises ``T._TkUnavailable`` if no display."""
    # Split the effective table into a built-in base + the user's overrides so the
    # two are editable independently within the session.
    custom = load_custom_prices()
    base = copy.deepcopy(COST_TABLE)
    for provider, models in custom.items():
        for model in models:
            base.get(provider, {}).pop(model, None)

    root = T.make_root("roscoe — pricing", WIN_W, WIN_H)
    T.header(root, "pricing", f"rates per 1,000 tokens · {prices_path()}")

    content, canvas = T.scrollable(root)

    # --- table of effective rates ---
    table_body = T.card(content, "Rates (built-in + your overrides)")
    tree = ttk.Treeview(table_body, columns=("provider", "model", "input", "output", "source"),
                        show="headings", height=12, style="roscoe.Treeview")
    for c, w in (("provider", 120), ("model", 240), ("input", 90), ("output", 90), ("source", 90)):
        tree.heading(c, text=c.upper())
        tree.column(c, anchor="w" if c in ("provider", "model") else "center", width=w, stretch=True)
    tree.tag_configure("custom", background="#EFF6FF", foreground="#1E40AF")
    tree.pack(fill="both", expand=True)

    # --- editor form (placeholder text shows the expected input for each field) ---
    form = T.card(content, "Add / edit a rate")
    form.columnconfigure(1, weight=1)
    form.columnconfigure(3, weight=1)

    tk.Label(form, text="Provider", fg=T.LABEL_FG, bg=T.CARD_BG).grid(row=0, column=0, sticky="w", pady=4, padx=(0, 6))
    prov_entry = T.PlaceholderEntry(form, "e.g. openai")
    prov_entry.grid(row=0, column=1, sticky="ew", pady=4, padx=(0, 12))
    tk.Label(form, text="Model", fg=T.LABEL_FG, bg=T.CARD_BG).grid(row=0, column=2, sticky="w", pady=4, padx=(0, 6))
    model_entry = T.PlaceholderEntry(form, "e.g. gpt-4o")
    model_entry.grid(row=0, column=3, sticky="ew", pady=4)

    tk.Label(form, text="Input $/1K", fg=T.LABEL_FG, bg=T.CARD_BG).grid(row=1, column=0, sticky="w", pady=4, padx=(0, 6))
    in_entry = T.PlaceholderEntry(form, "e.g. 0.005")
    in_entry.grid(row=1, column=1, sticky="ew", pady=4, padx=(0, 12))
    tk.Label(form, text="Output $/1K", fg=T.LABEL_FG, bg=T.CARD_BG).grid(row=1, column=2, sticky="w", pady=4, padx=(0, 6))
    out_entry = T.PlaceholderEntry(form, "e.g. 0.015")
    out_entry.grid(row=1, column=3, sticky="ew", pady=4)

    tk.Label(form, text="Rates are USD per 1,000 tokens. Select a row above to edit it.",
             fg=T.MUTED, bg=T.CARD_BG, font=(T.FONT, 9), anchor="w").grid(
        row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

    msg = tk.Label(form, text="", fg=T.MUTED, bg=T.CARD_BG, font=(T.FONT, 9), anchor="w")
    msg.grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 0))

    btns = tk.Frame(form, bg=T.CARD_BG)
    btns.grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def effective() -> dict:
        merged = copy.deepcopy(base)
        for p, models in custom.items():
            merged.setdefault(p, {}).update(models)
        return merged

    def refresh() -> None:
        tree.delete(*tree.get_children())
        for provider in sorted(effective()):
            for model in sorted(effective()[provider]):
                r = effective()[provider][model]
                is_custom = provider in custom and model in custom[provider]
                tree.insert("", "end", tags=("custom",) if is_custom else (), values=(
                    provider, model, r.get("input", 0), r.get("output", 0),
                    "custom" if is_custom else "built-in",
                ))

    def on_select(_e: object) -> None:
        sel = tree.selection()
        if not sel:
            return
        provider, model, inp, outp, _src = tree.item(sel[0], "values")
        prov_entry.set_value(provider); model_entry.set_value(model)
        in_entry.set_value(str(inp)); out_entry.set_value(str(outp))

    def add_update() -> None:
        provider, model = prov_entry.value().strip(), model_entry.value().strip()
        if not provider or not model:
            msg.config(text="Provider and model are required.", fg="#B45309"); return
        try:
            rate = {"input": float(in_entry.value()), "output": float(out_entry.value())}
        except ValueError:
            msg.config(text="Input/output must be numbers (e.g. 0.005).", fg="#B45309"); return
        custom.setdefault(provider, {})[model] = rate
        refresh()
        msg.config(text=f"Set {provider}/{model} = in ${rate['input']}, out ${rate['output']}", fg="#166534")

    def delete() -> None:
        provider, model = prov_entry.value().strip(), model_entry.value().strip()
        if provider in custom and model in custom[provider]:
            del custom[provider][model]
            if not custom[provider]:
                del custom[provider]
            refresh()
            msg.config(text=f"Removed override {provider}/{model}.", fg="#166534")
        else:
            msg.config(text="Only your own (custom) rates can be deleted.", fg="#B45309")

    def save_close() -> None:
        path = save_custom_prices(custom)
        msg.config(text=f"Saved to {path}", fg="#166534")
        root.after(400, close)

    def close() -> None:
        T.unbind_wheel(canvas)
        root.destroy()

    T.primary_button(btns, "Add / Update", add_update).pack(side="left")
    T.ghost_button(btns, "Delete", delete).pack(side="left", padx=(8, 0))
    tree.bind("<<TreeviewSelect>>", on_select)

    footer = tk.Frame(root, bg=T.BG)
    footer.pack(fill="x", side="bottom")
    tk.Frame(footer, bg=T.BORDER, height=1).pack(fill="x")
    fi = tk.Frame(footer, bg=T.BG)
    fi.pack(anchor="e", padx=24, pady=12)
    T.primary_button(fi, "Save & Close", save_close).pack(side="right", padx=(8, 0))
    T.ghost_button(fi, "Discard", close).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", close)

    refresh()
    root.mainloop()
