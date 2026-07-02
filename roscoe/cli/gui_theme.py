"""Shared tkinter theme + widget helpers for roscoe's desktop GUIs.

Centralises the palette and the little card/label/table builders so the init
wizard, the monitor dashboard, and the eval runner all look like one product.
Matches the colours the init wizard (``wizard_gui``) established.

``_TkUnavailable`` is raised when Tk can't start (headless box, no display) so
callers can fall back to the terminal path.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# --- Palette (shared across every roscoe GUI) --------------------------------
BG = "#FAFAFA"
CARD_BG = "#FFFFFF"
ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
ACCENT_FG = "#FFFFFF"
MUTED = "#6B7280"
BORDER = "#E5E7EB"
HEADING = "#111827"
LABEL_FG = "#374151"
CANCEL_BG = "#F3F4F6"
CANCEL_HOVER = "#E5E7EB"

# Status → (background, foreground) for pills/rows.
STATUS_COLORS = {
    "success": ("#DCFCE7", "#166534"),
    "error": ("#FEE2E2", "#991B1B"),
    "paused": ("#FEF3C7", "#92400E"),
    "rate_limited": ("#F3E8FF", "#6B21A8"),
    "unknown": ("#F3F4F6", "#374151"),
}

# Chart palette.
BAR_FILL = "#3B82F6"
BAR_FILL_TOP = "#60A5FA"
GRID = "#EEF2F7"

FONT = "Helvetica"


class _TkUnavailable(Exception):
    """Raised when Tk cannot initialise (no display)."""


def make_root(title: str, width: int, height: int) -> tk.Tk:
    """Create a themed, centered root window. Raises ``_TkUnavailable`` if no display."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # headless / no display
        raise _TkUnavailable(str(exc)) from exc

    root.title(title)
    root.configure(bg=BG)
    root.minsize(width, min(height, 400))

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure("TEntry", fieldbackground=CARD_BG)
    style.configure("TSpinbox", fieldbackground=CARD_BG)
    style.configure("TCombobox", fieldbackground=CARD_BG)
    style.configure("TCheckbutton", background=CARD_BG)
    _style_treeview(style)

    screen_h = root.winfo_screenheight()
    h = min(height, screen_h - 100)
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (screen_h // 2) - (h // 2)
    root.geometry(f"{width}x{h}+{x}+{y}")
    return root


def _style_treeview(style: ttk.Style) -> None:
    style.configure(
        "roscoe.Treeview",
        background=CARD_BG,
        fieldbackground=CARD_BG,
        foreground=HEADING,
        rowheight=26,
        borderwidth=0,
        font=(FONT, 10),
    )
    style.configure(
        "roscoe.Treeview.Heading",
        background="#F3F4F6",
        foreground=MUTED,
        font=(FONT, 9, "bold"),
        relief="flat",
    )
    style.map("roscoe.Treeview", background=[("selected", "#DBEAFE")],
              foreground=[("selected", HEADING)])


def header(parent: tk.Widget, title: str, subtitle: str, *, live: bool = False) -> tk.Frame:
    """Big product header with an optional live-dot."""
    frame = tk.Frame(parent, bg=BG)
    frame.pack(fill="x", padx=24, pady=(20, 2))

    row = tk.Frame(frame, bg=BG)
    row.pack(fill="x")
    tk.Label(row, text="roscoe", font=(FONT, 22, "bold"), fg=ACCENT, bg=BG).pack(side="left")
    tk.Label(row, text=f"  {title}", font=(FONT, 15), fg=HEADING, bg=BG).pack(side="left")
    if live:
        dot = tk.Label(row, text="●", font=(FONT, 12), fg="#22C55E", bg=BG)
        dot.pack(side="left", padx=(8, 0))

    sub = tk.Label(parent, text=subtitle, font=(FONT, 10), fg=MUTED, bg=BG, anchor="w")
    sub.pack(fill="x", padx=24, pady=(0, 14))
    return frame


def card(parent: tk.Widget, title: str | None = None) -> tk.Frame:
    """Titled white card. Returns the body frame to pack widgets into."""
    outer = tk.Frame(parent, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
    outer.pack(fill="x", padx=24, pady=(0, 12))
    if title:
        head = tk.Frame(outer, bg=CARD_BG)
        head.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(head, text=title, font=(FONT, 12, "bold"), fg=HEADING, bg=CARD_BG,
                 anchor="w").pack(side="left")
    body = tk.Frame(outer, bg=CARD_BG)
    body.pack(fill="both", expand=True, padx=16, pady=(6, 14))
    return body


def kpi_row(parent: tk.Widget) -> tk.Frame:
    """A horizontal strip that holds KPI mini-cards."""
    strip = tk.Frame(parent, bg=BG)
    strip.pack(fill="x", padx=24, pady=(0, 14))
    return strip


def kpi(parent: tk.Widget, label: str, value: str, color: str = HEADING) -> tk.Label:
    """One KPI mini-card. Returns the value label so it can be updated live."""
    cell = tk.Frame(parent, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
    cell.pack(side="left", fill="both", expand=True, padx=(0, 10))
    tk.Label(cell, text=label.upper(), font=(FONT, 8, "bold"), fg=MUTED, bg=CARD_BG).pack(
        anchor="w", padx=14, pady=(12, 0))
    val = tk.Label(cell, text=value, font=(FONT, 20, "bold"), fg=color, bg=CARD_BG)
    val.pack(anchor="w", padx=14, pady=(2, 12))
    return val


def primary_button(parent: tk.Widget, text: str, command) -> tk.Button:
    return tk.Button(parent, text=text, command=command, bg=ACCENT, fg=ACCENT_FG,
                     activebackground=ACCENT_HOVER, activeforeground=ACCENT_FG,
                     relief="flat", padx=20, pady=6, font=(FONT, 11, "bold"),
                     cursor="hand2", highlightthickness=0, bd=0)


def ghost_button(parent: tk.Widget, text: str, command) -> tk.Button:
    return tk.Button(parent, text=text, command=command, bg=CANCEL_BG, fg=LABEL_FG,
                     activebackground=CANCEL_HOVER, relief="flat", padx=16, pady=6,
                     font=(FONT, 11), cursor="hand2", highlightthickness=0, bd=0)


def scrollable(root: tk.Widget) -> tuple[tk.Frame, tk.Canvas]:
    """Return a (content_frame, canvas) pair with vertical scroll + mousewheel."""
    outer = tk.Frame(root, bg=BG)
    outer.pack(fill="both", expand=True)
    canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
    bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    content = tk.Frame(canvas, bg=BG)
    window = canvas.create_window((0, 0), window=content, anchor="nw")
    content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(window, width=e.width))

    def _wheel(event: object) -> None:
        delta = getattr(event, "delta", 0)
        step = -1 * (delta // 120) if delta else (-1 if getattr(event, "num", 0) == 4 else 1)
        canvas.yview_scroll(step, "units")

    canvas.bind_all("<MouseWheel>", _wheel)
    canvas.bind_all("<Button-4>", _wheel)
    canvas.bind_all("<Button-5>", _wheel)
    return content, canvas


def unbind_wheel(canvas: tk.Canvas) -> None:
    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        try:
            canvas.unbind_all(seq)
        except Exception:  # noqa: BLE001
            pass
