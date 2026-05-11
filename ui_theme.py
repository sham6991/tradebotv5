import tkinter as tk
from tkinter import ttk

INTERVAL_CHOICES = ("1 min", "2 min", "3 min", "5 min")
INTERVAL_VALUES = {
    "1 min": "minute",
    "2 min": "2minute",
    "3 min": "3minute",
    "5 min": "5minute",
    "minute": "minute",
    "1minute": "minute",
    "2minute": "2minute",
    "3minute": "3minute",
    "5minute": "5minute",
}

PALETTE = {
    "bg": "#eef2f6",
    "surface": "#ffffff",
    "surface_alt": "#f8fafc",
    "border": "#cbd5e1",
    "text": "#0f172a",
    "muted": "#64748b",
    "header": "#0b1220",
    "header_2": "#111827",
    "primary": "#2563eb",
    "success": "#059669",
    "danger": "#dc2626",
    "warning": "#d97706",
    "neutral": "#475569",
    "table_even": "#f8fafc",
    "table_odd": "#ffffff",
}


def configure_theme():
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TCombobox", padding=5, relief="flat")
    style.configure(
        "Trade.Treeview",
        background=PALETTE["surface"],
        fieldbackground=PALETTE["surface"],
        foreground=PALETTE["text"],
        borderwidth=0,
        rowheight=28,
        font=("Segoe UI", 9),
    )
    style.configure(
        "Trade.Treeview.Heading",
        background=PALETTE["header_2"],
        foreground="#ffffff",
        relief="flat",
        font=("Segoe UI", 9, "bold"),
    )
    style.map("Trade.Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", PALETTE["text"])])
