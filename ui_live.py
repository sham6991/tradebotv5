import calendar
import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from reporting import timestamped_file
from ui_theme import PALETTE

if TYPE_CHECKING:
    from execution_v2 import Executor
    LiveOptionRow = tuple[ttk.Combobox, tk.Entry, ttk.Combobox, tk.Entry, tk.Entry]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_FOLDER = os.path.join(BASE_DIR, "results")
os.makedirs(RESULT_FOLDER, exist_ok=True)


class LiveViewMixin:
    if TYPE_CHECKING:
        root: tk.Tk
        executor: "Executor"
        status_text: tk.StringVar
        live_mode: str
        live_options: list["LiveOptionRow"]
        live_settings_summary: tk.StringVar
        zerodha_status_text: tk.StringVar
        nifty_token: tk.Entry
        history_days: tk.Entry
        history_interval: ttk.Combobox
        pnl_label: tk.Label
        feed_label: tk.Label
        tick_outputs: dict[str, Any]

        def clear_window(self) -> None: ...
        def set_status(self, text: str) -> None: ...
        def header(self, title: str, subtitle: str = "") -> None: ...
        def content(self) -> tk.Frame: ...
        def status_bar(self) -> None: ...
        def show_home(self) -> None: ...
        def _card(self, parent: tk.Misc, padx: int = 16, pady: int = 14) -> tk.Frame: ...
        def _section_title(self, parent: tk.Misc, text: str, subtitle: str = "") -> None: ...
        def _mode_card(self, parent: tk.Misc, title: str, body: str, button_text: str, command: Any, color: str) -> tk.Frame: ...
        def _field(
            self,
            frame: tk.Misc,
            text: str,
            default: Any,
            row: int,
            column: int = 1,
            width: int = 18,
            show: str | None = None,
        ) -> tk.Entry: ...
        def _interval_field(
            self,
            frame: tk.Misc,
            text: str,
            default: str = "3 min",
            row: int = 0,
            column: int = 1,
            width: int = 18,
        ) -> ttk.Combobox: ...
        def _live_option_field(self, frame: tk.Misc, label: str, row: int) -> "LiveOptionRow": ...
        def make_button(self, parent: tk.Misc, text: str, command: Any, bg: str = "#0f766e", width: int = 20) -> tk.Button: ...
        def _ensure_settings_values(self, attr_name: str) -> dict[str, str]: ...
        def _settings_summary_text(self, values: dict[str, str], mode: str = "") -> str: ...
        def _open_settings_dialog(self, title: str, attr_name: str, on_save: Any = None) -> None: ...
        def _sync_zerodha_client_for_mode(self, mode: str | None = None) -> None: ...
        def _auth_label(self, mode: str | None = None) -> str: ...
        def _update_zerodha_status_for_mode(self, mode: str | None = None) -> None: ...
        def _zerodha_connection_blocked(self, mode: str | None = None, show_message: bool = False) -> bool: ...
        def fetch_nifty_token(self) -> None: ...
        def connect_zerodha(self) -> None: ...
        def auto_fill_instruments(self) -> None: ...
        def start_market_feed(self) -> None: ...
        def load_live_sample_preset(self) -> None: ...
        def start_live_mode(self) -> None: ...
        def activate_kill_switch(self) -> None: ...
        def square_off_open_position(self) -> None: ...
        def show_tick_log_window(self) -> None: ...
        def _build_live_log_tabs(self, parent: tk.Misc) -> None: ...
        def _start_dashboard_refresh(self) -> None: ...

    def show_live_selector(self):
        self.clear_window()
        self.set_status("Choose live execution mode")
        self.header("Live Desk", "Choose paper trading or Zerodha account trading")
        frame = self.content()

        tk.Label(
            frame,
            text="Execution Workspace",
            font=("Segoe UI", 22, "bold"),
            bg=PALETTE["bg"],
            fg=PALETTE["text"]
        ).pack(anchor="w", pady=(28, 8))
        tk.Label(
            frame,
            text="Paper mode and real trading use the same signal engine, risk controls, candle builder, and report path.",
            font=("Segoe UI", 11),
            bg=PALETTE["bg"],
            fg=PALETTE["muted"],
        ).pack(anchor="w", pady=(0, 22))

        actions = tk.Frame(frame, bg=PALETTE["bg"])
        actions.pack(anchor="w")
        self._mode_card(
            actions,
            "Paper Trading",
            "Connect live data, simulate orders, save open state, and export reports without placing real orders.",
            "OPEN PAPER DESK",
            lambda: self.show_live_form("PAPER"),
            "#0f766e",
        ).grid(row=0, column=0, padx=(0, 16), sticky="nsew")
        self._mode_card(
            actions,
            "Zerodha Live",
            "Place real market and limit orders with margin checks, tick exits, and emergency square-off.",
            "OPEN LIVE TRADING",
            lambda: self.show_live_form("LIVE"),
            PALETTE["danger"],
        ).grid(row=0, column=1, padx=(0, 16), sticky="nsew")
        self.make_button(frame, "HOME", self.show_home, PALETTE["neutral"], 14).pack(anchor="w", pady=22)
        self.status_bar()

    def show_live_form(self, mode):
        self.clear_window()
        self.set_status(f"{mode.title()} live desk ready")
        title = "Paper Trading" if mode == "PAPER" else "Zerodha Live Trading"
        self.header(title, "Uses the same signal engine and risk parameters as backtesting")
        frame = self.content()

        frame.grid_rowconfigure(0, weight=0)
        frame.grid_rowconfigure(1, weight=0)
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        top_pane = tk.Frame(frame, bg=PALETTE["bg"])
        top_pane.grid(row=0, column=0, sticky="ew")

        action_pane = tk.Frame(frame, bg=PALETTE["bg"])
        action_pane.grid(row=1, column=0, sticky="ew", pady=(8, 8))

        log_pane = tk.Frame(frame, bg=PALETTE["bg"])
        log_pane.grid(row=2, column=0, sticky="nsew")

        form = tk.Frame(top_pane, bg=PALETTE["bg"])
        form.pack(fill="x")
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=0)

        self.live_mode = mode
        self._sync_zerodha_client_for_mode(mode)
        settings_attr = "paper_settings_values" if mode == "PAPER" else "real_settings_values"

        contracts = self._card(form, padx=12, pady=8)
        contracts.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        self._section_title(contracts, "Option Contracts", "Fetch NIFTY and option instruments before starting the market feed.")

        labels = ["CALL 1", "PUT 1"]
        self.live_options = [self._live_option_field(contracts, label, i + 4) for i, label in enumerate(labels)]
        for column, text in ((1, "Type"), (2, "Strike"), (3, "Expiry"), (6, "Tradingsymbol"), (7, "Token")):
            tk.Label(contracts, text=text, bg=PALETTE["surface"], fg=PALETTE["muted"], font=("Segoe UI", 9, "bold")).grid(row=3, column=column, sticky="w", padx=6)

        settings_panel = self._card(form, padx=12, pady=8)
        settings_panel.grid(row=1, column=0, sticky="ew", padx=(0, 14), pady=(10, 0))
        settings_panel.grid_columnconfigure(0, weight=1)

        self._section_title(settings_panel, "Risk Settings", "Open settings to review, save, or restore defaults for this live workspace.")
        self.live_settings_summary = tk.StringVar(
            value=self._settings_summary_text(self._ensure_settings_values(settings_attr), mode=mode)
        )
        tk.Label(
            settings_panel,
            textvariable=self.live_settings_summary,
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.make_button(
            settings_panel,
            "RISK SETTINGS",
            lambda: self._open_settings_dialog(
                f"{title} Risk Settings",
                settings_attr,
                on_save=lambda values, _parsed: self.live_settings_summary.set(self._settings_summary_text(values, mode=mode)),
            ),
            PALETTE["primary"],
            16,
        ).grid(row=3, column=3, sticky="e", padx=8)

        connection = self._card(form, padx=12, pady=8)
        connection.grid(row=0, column=1, rowspan=2, sticky="n", padx=(0, 0))

        self._section_title(connection, "Zerodha Connection", "Fetch instruments, start feeds, and manage live connectivity.")

        self.zerodha_status_text = tk.StringVar(value=f"{self._auth_label(mode)}: not connected")
        self._update_zerodha_status_for_mode(mode)
        tk.Label(
            connection,
            textvariable=self.zerodha_status_text,
            bg=PALETTE["surface"],
            fg=PALETTE["text"],
            font=("Segoe UI", 10, "bold"),
            wraplength=260,
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=(4, 8))

        self.nifty_token = self._field(connection, "NIFTY Token", "", 4, column=1, width=28)
        self.history_days = self._field(connection, "History Days", "5", 5, column=1, width=28)
        self.history_interval = self._interval_field(connection, "Interval", "3 min", 6, column=1, width=28)

        self.make_button(connection, "Fetch NIFTY", self.fetch_nifty_token, PALETTE["neutral"], 12).grid(row=4, column=2, padx=(8, 0), sticky="w")
        connect_label = "Connect Paper Data" if mode == "PAPER" else "Connect Real Money"
        connect_button = self.make_button(connection, connect_label, self.connect_zerodha, PALETTE["primary"], 20)
        if self._zerodha_connection_blocked(mode):
            connect_button.configure(
                text=f"{connect_label} Locked",
                state="disabled",
                disabledforeground="#e5e7eb",
                bg=PALETTE["neutral"],
                activebackground=PALETTE["neutral"],
            )
        connect_button.grid(row=7, column=0, columnspan=2, pady=(12, 0), padx=4, sticky="ew")
        self.make_button(connection, "Fetch All", self.auto_fill_instruments, PALETTE["neutral"], 12).grid(row=8, column=0, pady=6, padx=4, sticky="ew")
        self.make_button(connection, "Start Feed", self.start_market_feed, PALETTE["success"], 12).grid(row=8, column=1, pady=6, padx=4, sticky="ew")
        self.make_button(connection, "Stop Feed", self.executor.stop_market_feed, PALETTE["danger"], 12).grid(row=9, column=0, pady=6, padx=4, sticky="ew")
        self.make_button(connection, "Load Live Preset", self.load_live_sample_preset, PALETTE["neutral"], 16).grid(row=9, column=1, pady=6, padx=4, sticky="ew")

        actions = tk.Frame(action_pane, bg=PALETTE["bg"])
        actions.pack(side="left", anchor="w")
        label = "START LIVE PAPER" if mode == "PAPER" else "START REAL TRADING"
        color = "#0f766e" if mode == "PAPER" else "#dc2626"
        self.make_button(actions, label, self.start_live_mode, color, 20).grid(row=0, column=0, padx=4)
        self.make_button(actions, "STOP", self.executor.stop, "#111827", 10).grid(row=0, column=1, padx=4)
        self.make_button(actions, "KILL SWITCH", self.activate_kill_switch, "#991b1b", 13).grid(row=0, column=2, padx=4)
        self.make_button(actions, "SQUARE OFF", self.square_off_open_position, "#f97316", 12).grid(row=0, column=3, padx=4)
        self.make_button(actions, "TICK LOG", self.show_tick_log_window, "#2563eb", 10).grid(row=0, column=4, padx=4)
        self.make_button(actions, "BACK", self.show_live_selector, "#6b7280", 9).grid(row=0, column=5, padx=4)
        metrics = tk.Frame(action_pane, bg=PALETTE["surface"], padx=10, pady=5, highlightbackground=PALETTE["border"], highlightthickness=1)
        metrics.pack(side="right", fill="x", expand=True, padx=(14, 0))

        pnl_text = "Total P&L: 0.00" if mode == "PAPER" else "Available Margin: not connected"
        self.pnl_label = tk.Label(metrics, text=pnl_text, bg=PALETTE["surface"], fg=PALETTE["text"], font=("Segoe UI", 9, "bold"))
        self.pnl_label.pack(side="left", padx=(0, 14))

        self.feed_label = tk.Label(
            metrics,
            text="Feed: disconnected",
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 9, "bold")
        )
        self.feed_label.pack(side="left", fill="x", expand=True, anchor="w")

        tk.Label(
            log_pane,
            text="Live Logs",
            bg=PALETTE["bg"],
            fg=PALETTE["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 4))

        self._build_live_log_tabs(log_pane)

        self.tick_outputs = {}
        self.status_bar()
        self._start_dashboard_refresh()

