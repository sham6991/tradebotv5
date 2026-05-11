import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from event_replay import build_session_replay, format_replay_report
from ui_theme import PALETTE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_FOLDER = os.path.join(BASE_DIR, "results")

REPLAY_FILTERS = {
    "All": "timeline",
    "Critical": "critical_events",
    "Warnings": "warning_events",
    "Partial Fills/Exits": "partial_events",
    "Rejected/Failed": "rejected_or_failed_orders",
    "Kill Switch": "kill_switch_events",
    "Reconciliation": "reconciliation_events",
    "Unknown Broker State": "unknown_broker_state_events",
}

REPLAY_COLUMNS = (
    "timestamp",
    "kind",
    "event",
    "level_status",
    "order_id",
    "trade",
    "instrument",
    "quantity",
    "message",
)


def latest_replay_database(result_folder=RESULT_FOLDER):
    if not result_folder or not os.path.isdir(result_folder):
        return ""
    candidates = [
        os.path.join(result_folder, name)
        for name in os.listdir(result_folder)
        if name.lower().endswith(".db")
    ]
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


def replay_table_row(item):
    if item.get("kind") == "event":
        return {
            "timestamp": item.get("timestamp", ""),
            "kind": "EVENT",
            "event": item.get("event_type", "") or "UNSTRUCTURED",
            "level_status": item.get("level", ""),
            "order_id": item.get("order_id", ""),
            "trade": item.get("trade_no", ""),
            "instrument": item.get("instrument", ""),
            "quantity": item.get("quantity", ""),
            "message": item.get("message", ""),
        }
    return {
        "timestamp": item.get("timestamp", ""),
        "kind": "ORDER",
        "event": item.get("action", ""),
        "level_status": item.get("order_status", ""),
        "order_id": item.get("order_id", ""),
        "trade": item.get("related_trade_id", ""),
        "instrument": item.get("instrument", ""),
        "quantity": item.get("quantity", ""),
        "message": item.get("error_reason") or item.get("exit_reason", ""),
    }


class ReplayViewMixin:
    def show_session_replay(self):
        self.clear_window()
        self.set_status("Session replay ready")
        self.header("Session Replay", "Read-only audit view for previous paper, live, and backtest SQLite sessions")
        frame = self.content()
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        controls = self._card(frame, padx=14, pady=12)
        controls.grid(row=0, column=0, sticky="ew")
        controls.grid_columnconfigure(1, weight=1)
        self._section_title(controls, "Replay Source", "Select a session database from results and load a read-only timeline.")

        tk.Label(controls, text="Database", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=3, column=0, sticky="e", pady=3)
        self.replay_db_path = tk.Entry(controls, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
        self.replay_db_path.grid(row=3, column=1, sticky="ew", padx=8, pady=3)
        self.make_button(controls, "Browse DB", self.browse_replay_database, PALETTE["neutral"], 12).grid(row=3, column=2, padx=4)
        self.make_button(controls, "Latest", self.load_latest_replay_database, "#0ea5e9", 10).grid(row=3, column=3, padx=4)

        tk.Label(controls, text="Session ID", bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=4, column=0, sticky="e", pady=3)
        self.replay_session_id = tk.Entry(controls, width=28, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
        self.replay_session_id.grid(row=4, column=1, sticky="w", padx=8, pady=3)

        self.replay_filter = ttk.Combobox(controls, width=24, values=tuple(REPLAY_FILTERS.keys()), state="readonly")
        self.replay_filter.set("All")
        self.replay_filter.grid(row=4, column=2, padx=4, sticky="ew")
        self.replay_filter.bind("<<ComboboxSelected>>", lambda _event: self.render_replay_timeline())

        action_row = tk.Frame(controls, bg=PALETTE["surface"])
        action_row.grid(row=5, column=1, columnspan=3, sticky="w", pady=(8, 0))
        self.make_button(action_row, "Load Replay", self.load_session_replay, PALETTE["success"], 14).grid(row=0, column=0, padx=(0, 6))
        self.make_button(action_row, "Export Text", self.export_replay_text, PALETTE["primary"], 12).grid(row=0, column=1, padx=6)
        self.make_button(action_row, "Export JSON", self.export_replay_json, PALETTE["primary"], 12).grid(row=0, column=2, padx=6)
        self.make_button(action_row, "Home", self.show_home, PALETTE["neutral"], 10).grid(row=0, column=3, padx=6)

        self.replay_summary_text = tk.StringVar(value="No replay loaded")
        summary = self._card(frame, padx=12, pady=10)
        summary.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        tk.Label(
            summary,
            textvariable=self.replay_summary_text,
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=("Segoe UI", 10, "bold"),
            justify="left",
            wraplength=1180,
        ).pack(anchor="w")

        body = tk.Frame(frame, bg=PALETTE["bg"])
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=1)

        table_panel = self._card(body, padx=8, pady=8)
        table_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        table_panel.grid_rowconfigure(0, weight=1)
        table_panel.grid_columnconfigure(0, weight=1)
        self.replay_tree = self._make_replay_tree(table_panel)

        detail_panel = self._card(body, padx=8, pady=8)
        detail_panel.grid(row=0, column=1, sticky="nsew")
        detail_panel.grid_rowconfigure(1, weight=1)
        detail_panel.grid_columnconfigure(0, weight=1)
        tk.Label(detail_panel, text="Selected Payload", bg=PALETTE["surface"], fg=PALETTE["text"], font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.replay_payload = scrolledtext.ScrolledText(detail_panel, height=18, font=("Consolas", 9), bg="#020617", fg="#d1fae5", insertbackground="#d1fae5", relief="flat")
        self.replay_payload.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        self.replay_report = None
        self.replay_visible_items = []
        self.load_latest_replay_database(update_status=False)
        self.status_bar()

    def _make_replay_tree(self, parent):
        widths = {
            "timestamp": 145,
            "kind": 70,
            "event": 150,
            "level_status": 110,
            "order_id": 120,
            "trade": 120,
            "instrument": 165,
            "quantity": 85,
            "message": 280,
        }
        tree = ttk.Treeview(parent, columns=REPLAY_COLUMNS, show="headings", style="Trade.Treeview", height=16)
        for column in REPLAY_COLUMNS:
            tree.heading(column, text=column.replace("_", " ").title())
            tree.column(column, width=widths[column], anchor="w", stretch=column == "message")
        y_scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        x_scroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        tree.bind("<<TreeviewSelect>>", self.on_replay_row_selected)
        return tree

    def browse_replay_database(self):
        path = filedialog.askopenfilename(
            initialdir=RESULT_FOLDER,
            filetypes=[("SQLite database", "*.db"), ("All files", "*.*")],
        )
        if path:
            self._set_field_value(self.replay_db_path, path)
            self.set_status(f"Selected replay DB {os.path.basename(path)}")

    def load_latest_replay_database(self, update_status=True):
        path = latest_replay_database(RESULT_FOLDER)
        if path:
            self._set_field_value(self.replay_db_path, path)
            if update_status:
                self.set_status(f"Selected latest replay DB {os.path.basename(path)}")
        elif update_status:
            self.set_status("No SQLite session DB found in results")

    def load_session_replay(self):
        path = self.replay_db_path.get().strip()
        if not path:
            messagebox.showerror("SESSION REPLAY", "Select a SQLite session database first.")
            return
        if not os.path.exists(path):
            messagebox.showerror("SESSION REPLAY", f"Database not found:\n{path}")
            return
        try:
            session_id = self.replay_session_id.get().strip()
            self.replay_report = build_session_replay(path, session_id=session_id)
            self.render_replay_summary()
            self.render_replay_timeline()
            total = self.replay_report["summary"]["total_items"]
            self.set_status(f"Replay loaded: {total} timeline rows from {os.path.basename(path)}")
        except Exception as exc:
            self.replay_report = None
            self.set_status("Session replay load failed")
            messagebox.showerror("SESSION REPLAY ERROR", str(exc))

    def render_replay_summary(self):
        if not self.replay_report:
            self.replay_summary_text.set("No replay loaded")
            return
        summary = self.replay_report.get("summary", {})
        highlights = self.replay_report.get("highlights", {})
        self.replay_summary_text.set(
            " | ".join([
                f"Rows {summary.get('total_items', 0)}",
                f"Events {summary.get('event_items', 0)}",
                f"Order Rows {summary.get('order_history_items', 0)}",
                f"Orders {summary.get('unique_order_count', 0)}",
                f"Critical {len(highlights.get('critical_events', []))}",
                f"Warnings {len(highlights.get('warning_events', []))}",
                f"Partial {len(highlights.get('partial_events', []))}",
                f"Rejected/Failed {len(highlights.get('rejected_or_failed_orders', []))}",
                f"Window {summary.get('first_timestamp', '') or 'n/a'} to {summary.get('last_timestamp', '') or 'n/a'}",
            ])
        )

    def render_replay_timeline(self):
        if not hasattr(self, "replay_tree"):
            return
        for row_id in self.replay_tree.get_children():
            self.replay_tree.delete(row_id)
        self.replay_visible_items = []
        self.replay_payload.delete("1.0", tk.END)
        if not self.replay_report:
            return

        filter_name = self.replay_filter.get() if hasattr(self, "replay_filter") else "All"
        key = REPLAY_FILTERS.get(filter_name, "timeline")
        items = self.replay_report.get("timeline", []) if key == "timeline" else self.replay_report.get("highlights", {}).get(key, [])
        self.replay_visible_items = list(items)
        for index, item in enumerate(self.replay_visible_items):
            row = replay_table_row(item)
            values = [row.get(column, "") for column in REPLAY_COLUMNS]
            self.replay_tree.insert("", "end", iid=str(index), values=values)

    def on_replay_row_selected(self, _event=None):
        selected = self.replay_tree.selection()
        if not selected:
            return
        try:
            item = self.replay_visible_items[int(selected[0])]
        except (IndexError, ValueError):
            return
        payload = {
            key: value
            for key, value in item.items()
            if key not in {"payload"}
        }
        payload["payload"] = item.get("payload", {})
        self.replay_payload.delete("1.0", tk.END)
        self.replay_payload.insert(tk.END, json.dumps(payload, default=str, indent=2))

    def export_replay_text(self):
        self._export_replay("text")

    def export_replay_json(self):
        self._export_replay("json")

    def _export_replay(self, output_format):
        if not self.replay_report:
            messagebox.showerror("SESSION REPLAY", "Load a replay before exporting.")
            return
        extension = ".json" if output_format == "json" else ".txt"
        path = filedialog.asksaveasfilename(
            initialdir=RESULT_FOLDER,
            defaultextension=extension,
            filetypes=[("JSON", "*.json")] if output_format == "json" else [("Text", "*.txt")],
        )
        if not path:
            return
        if output_format == "json":
            text = json.dumps(self.replay_report, default=str, indent=2)
        else:
            text = "\n".join(format_replay_report(self.replay_report, include_payload=True))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")
        self.set_status(f"Replay exported: {os.path.basename(path)}")
