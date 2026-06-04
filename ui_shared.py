import os
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from engine import parse_option_metadata_from_text
from indicators import clean_and_add_indicators
from settings_service import (
    DEFAULT_SETTINGS,
    SETTING_LABELS,
    SETTINGS_PROFILE_PATH,
    interval_label,
    load_settings_profiles,
    normalise_interval,
    normalise_order_product,
    normalise_trend_set,
    parse_runtime_setting_value,
    save_settings_profile,
    setting_value,
    settings_from_values,
)
from settings_validation import raise_for_fast_ohlcv_settings
from strategy import ensure_option_formula_columns
from ui_theme import INTERVAL_CHOICES, PALETTE, configure_theme


BOOLEAN_SETTING_KEYS = {
    "fast_ohlcv_entry_enabled",
    "enable_chop_filter",
    "aggressive_live_entry_enabled",
    "one_entry_attempt_per_candle",
    "trailing_sl_enabled",
    "live_option_market_entry_as_limit_enabled",
}


class SharedUIMixin:
    root: tk.Tk
    status_text: tk.StringVar

    def _cancel_dashboard_refresh(self) -> None: ...
    def show_backtest(self) -> None: ...
    def show_live_selector(self) -> None: ...
    def show_session_replay(self) -> None: ...
    def load_expiry_choices(self, type_entry: ttk.Combobox, strike_entry: tk.Entry, expiry_entry: ttk.Combobox) -> None: ...
    def show_expiry_calendar(self, expiry_entry: ttk.Combobox) -> None: ...
    def fetch_option_row_safe(
        self,
        type_entry: ttk.Combobox,
        strike_entry: tk.Entry,
        expiry_entry: ttk.Combobox,
        symbol_entry: tk.Entry,
        token_entry: tk.Entry,
    ) -> None: ...

    def run(self):
        self.root.mainloop()

    def clear_window(self):
        if hasattr(self, "_cancel_dashboard_refresh"):
            self._cancel_dashboard_refresh()
        for widget in self.root.winfo_children():
            widget.destroy()

    def _configure_theme(self):
        configure_theme()

    def _card(self, parent, padx=16, pady=14):
        return tk.Frame(
            parent,
            bg=PALETTE["surface"],
            padx=padx,
            pady=pady,
            highlightbackground=PALETTE["border"],
            highlightthickness=1,
        )

    def _section_title(self, parent, text, subtitle=""):
        tk.Label(
            parent,
            text=text,
            bg=parent["bg"],
            fg=PALETTE["text"],
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 2))
        if subtitle:
            tk.Label(
                parent,
                text=subtitle,
                bg=parent["bg"],
                fg=PALETTE["muted"],
                font=("Segoe UI", 9),
            ).grid(row=1, column=0, columnspan=8, sticky="w", pady=(0, 10))

    def header(self, title, subtitle=""):
        bar = tk.Frame(self.root, bg=PALETTE["header"], height=96)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        title_block = tk.Frame(bar, bg=PALETTE["header"])
        title_block.pack(side="left", padx=26, pady=14)
        tk.Label(
            title_block,
            text=title,
            font=("Segoe UI", 22, "bold"),
            fg="white",
            bg=PALETTE["header"]
        ).pack(anchor="w")

        if subtitle:
            tk.Label(
            title_block,
            text=subtitle,
            font=("Segoe UI", 10),
            fg="#cbd5e1",
            bg=PALETTE["header"],
            wraplength=780,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        tk.Label(
            bar,
            text=datetime.now().strftime("%d %b %Y"),
            font=("Segoe UI", 10, "bold"),
            fg="#bfdbfe",
            bg=PALETTE["header"],
        ).pack(side="right", padx=26)

    def content(self):
        frame = tk.Frame(self.root, bg=PALETTE["bg"])
        frame.pack(fill="both", expand=True, padx=18, pady=14)
        return frame

    def status_bar(self):
        bar = tk.Frame(self.root, bg=PALETTE["surface"], height=28, highlightbackground=PALETTE["border"], highlightthickness=1)
        bar.pack(fill="x", side="bottom")
        tk.Label(
            bar,
            textvariable=self.status_text,
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=12)

    def set_status(self, text):
        self.status_text.set(text)
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass

    def make_button(self, parent, text, command, bg: str = "#0f766e", width=20):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg="white",
            activebackground=bg,
            activeforeground="white",
            width=width,
            height=1,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
            padx=6,
            pady=5,
        )
        button.bind("<Enter>", lambda _event: button.configure(bg=self._hover_color(bg)))
        button.bind("<Leave>", lambda _event: button.configure(bg=bg))
        return button

    def _hover_color(self, color: str) -> str:
        return {
            PALETTE["primary"]: "#1d4ed8",
            PALETTE["success"]: "#047857",
            PALETTE["danger"]: "#b91c1c",
            PALETTE["warning"]: "#b45309",
            PALETTE["neutral"]: "#334155",
            "#111827": "#020617",
            "#0f766e": "#115e59",
            "#16a34a": "#15803d",
            "#2563eb": "#1d4ed8",
            "#dc2626": "#b91c1c",
            "#6b7280": "#4b5563",
            "#0ea5e9": "#0284c7",
            "#f97316": "#ea580c",
        }.get(color, color)

    def show_home(self):
        self.clear_window()
        self.set_status("Ready")
        self.header("TradeBotV5 Control Center", "Backtesting, paper/live execution, session replay, and risk-engine exports")
        frame = self.content()

        tk.Label(
            frame,
            text="Select Workspace",
            font=("Segoe UI", 24, "bold"),
            bg=PALETTE["bg"],
            fg=PALETTE["text"]
        ).pack(anchor="w", pady=(22, 8))
        tk.Label(
            frame,
            text="Run repeatable research, monitor live ticks, and replay prior sessions without touching broker connectivity.",
            font=("Segoe UI", 11),
            bg=PALETTE["bg"],
            fg=PALETTE["muted"],
        ).pack(anchor="w", pady=(0, 24))

        actions = tk.Frame(frame, bg="#f4f6f8")
        actions.pack(anchor="w")

        self._mode_card(
            actions,
            "Backtest Mode",
            "CSV research, Excel workbooks, settings snapshots, and risk-engine SQLite output.",
            "OPEN BACKTEST",
            self.show_backtest,
            PALETTE["success"],
        ).grid(row=0, column=0, padx=(0, 16), sticky="nsew")
        self._mode_card(
            actions,
            "Live Desk",
            "Paper trading, Zerodha live trading, tick tabs, square-off controls, and persistent state.",
            "OPEN LIVE DESK",
            self.show_live_selector,
            PALETTE["primary"],
        ).grid(row=0, column=1, padx=(0, 16), sticky="nsew")
        self._mode_card(
            actions,
            "Session Replay",
            "Read-only timeline, highlights, payloads, and exports from previous SQLite sessions.",
            "OPEN REPLAY",
            self.show_session_replay,
            "#0ea5e9",
        ).grid(row=0, column=2, padx=(0, 16), sticky="nsew")
        self.status_bar()

    def _mode_card(self, parent, title, body, button_text, command, color):
        card = self._card(parent, padx=20, pady=18)
        card.configure(width=390, height=190)
        card.grid_propagate(False)
        tk.Label(card, text=title, bg=PALETTE["surface"], fg=PALETTE["text"], font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(
            card,
            text=body,
            bg=PALETTE["surface"],
            fg=PALETTE["muted"],
            font=("Segoe UI", 10),
            wraplength=335,
            justify="left",
        ).pack(anchor="w", pady=(10, 18))
        self.make_button(card, button_text, command, color, 22).pack(anchor="w")
        return card

    def browse(self, entry):
        file = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if file:
            entry.delete(0, tk.END)
            entry.insert(0, file)
            self.set_status(f"Selected {os.path.basename(file)}")

    def _field(self, frame, text, default, row, column=1, width=18, show: str | None = None):
        tk.Label(frame, text=text, bg=frame["bg"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(
            row=row, column=column - 1, pady=3, padx=6, sticky="e"
        )
        entry = tk.Entry(
            frame,
            width=width,
            relief="solid",
            bd=1,
            bg=PALETTE["surface_alt"],
            fg=PALETTE["text"],
            insertbackground=PALETTE["text"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["primary"],
        )
        if show is not None:
            entry["show"] = show
        entry.insert(0, default)
        entry.grid(row=row, column=column, pady=3, padx=6, sticky="w")
        return entry

    def _interval_field(self, frame, text, default="3 min", row=0, column=1, width=18):
        tk.Label(frame, text=text, bg=frame["bg"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(
            row=row, column=column - 1, pady=3, padx=6, sticky="e"
        )
        field = ttk.Combobox(frame, width=width, values=INTERVAL_CHOICES, state="readonly")
        field.set(self._interval_label(default))
        field.grid(row=row, column=column, pady=3, padx=6, sticky="w")
        return field

    def _interval_label(self, value):
        return interval_label(value)

    def _normalise_interval(self, value):
        return normalise_interval(value)

    def _normalise_order_product(self, value):
        return normalise_order_product(value)

    def _normalise_trend_set(self, value):
        return normalise_trend_set(value)

    def _order_product_field(self, frame, text, default="NRML", row=0, column=1, width=18):
        tk.Label(frame, text=text, bg=frame["bg"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(
            row=row, column=column - 1, pady=3, padx=6, sticky="e"
        )
        field = ttk.Combobox(frame, width=width, values=("NRML", "MIS"), state="readonly")
        field.set(self._normalise_order_product(default))
        field.grid(row=row, column=column, pady=3, padx=6, sticky="w")
        return field

    def _trend_set_field(self, frame, text, default="Auto", row=0, column=1, width=18):
        tk.Label(frame, text=text, bg=frame["bg"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(
            row=row, column=column - 1, pady=3, padx=6, sticky="e"
        )
        field = ttk.Combobox(frame, width=width, values=("Auto", "Bullish", "Bearish"), state="readonly")
        field.set(self._normalise_trend_set(default))
        field.grid(row=row, column=column, pady=3, padx=6, sticky="w")
        return field

    def _set_field_value(self, field, value):
        if isinstance(field, ttk.Combobox):
            field.set(value)
            return
        field.delete(0, tk.END)
        field.insert(0, value)

    def _file_field(self, frame, label, row):
        tk.Label(frame, text=label, bg=frame["bg"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=row, column=0, pady=3, sticky="e")
        path_entry = tk.Entry(frame, width=58, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
        path_entry.grid(row=row, column=1, pady=3, padx=6)
        self.make_button(frame, "Browse", lambda: self.browse(path_entry), PALETTE["neutral"], 10).grid(row=row, column=2, padx=4)
        return path_entry

    def _option_field(self, frame, label, row, with_token=False, with_metadata=False):
        path_entry = self._file_field(frame, label, row)
        symbol_entry = tk.Entry(frame, width=22, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
        symbol_entry.insert(0, label.replace(" ", "_"))
        symbol_entry.grid(row=row, column=3, pady=5, padx=6)
        if with_metadata:
            strike_entry = tk.Entry(frame, width=12, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
            strike_entry.grid(row=row, column=4, pady=5, padx=6)
            expiry_entry = tk.Entry(frame, width=14, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
            expiry_entry.grid(row=row, column=5, pady=5, padx=6)
            return path_entry, symbol_entry, strike_entry, expiry_entry
        if not with_token:
            return path_entry, symbol_entry

        token_entry = tk.Entry(frame, width=16, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
        token_entry.grid(row=row, column=4, pady=5, padx=6)
        return path_entry, symbol_entry, token_entry

    def _live_option_field(self, frame, label, row):
        tk.Label(frame, text=label, bg=frame["bg"], fg=PALETTE["muted"], font=("Segoe UI", 9)).grid(row=row, column=0, pady=3, sticky="e")

        type_entry = ttk.Combobox(frame, width=5, values=("CE", "PE"), state="readonly")
        type_entry.set("CE" if "CALL" in label else "PE")
        type_entry.grid(row=row, column=1, pady=3, padx=6, sticky="w")

        strike_entry = tk.Entry(frame, width=10, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
        strike_entry.grid(row=row, column=2, pady=3, padx=6, sticky="w")

        expiry_entry = ttk.Combobox(frame, width=12)
        expiry_entry.grid(row=row, column=3, pady=3, padx=6, sticky="w")
        expiry_entry.bind(
            "<Button-1>",
            lambda _event, t=type_entry, s=strike_entry, e=expiry_entry: self.load_expiry_choices(t, s, e)
        )

        calendar_button = tk.Button(
            frame,
            text="...",
            width=3,
            cursor="hand2",
            command=lambda e=expiry_entry: self.show_expiry_calendar(e)
        )
        calendar_button.grid(row=row, column=4, pady=3, padx=(0, 6), sticky="w")

        fetch_button = tk.Button(
            frame,
            text="Fetch",
            width=8,
            cursor="hand2",
            bg=PALETTE["neutral"],
            fg="white",
            relief="flat",
            activebackground="#334155",
            activeforeground="white",
        )
        fetch_button.grid(row=row, column=5, pady=3, padx=6, sticky="w")

        symbol_entry = tk.Entry(frame, width=24, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
        symbol_entry.grid(row=row, column=6, pady=3, padx=6, sticky="w")

        token_entry = tk.Entry(frame, width=16, relief="solid", bd=1, bg=PALETTE["surface_alt"], fg=PALETTE["text"])
        token_entry.grid(row=row, column=7, pady=3, padx=6, sticky="w")

        fetch_button.configure(
            command=lambda t=type_entry, s=strike_entry, e=expiry_entry, sym=symbol_entry, tok=token_entry:
                self.fetch_option_row_safe(t, s, e, sym, tok)
        )

        return type_entry, strike_entry, expiry_entry, symbol_entry, token_entry

    def _settings(self, frame, start_row):
        fields = {
            "balance": self._field(frame, "Balance", "100000", start_row, column=1),
            "lot_size": self._field(frame, "Lots", "1", start_row, column=4),
            "max_trades": self._field(frame, "Max Trades", "5", start_row + 1, column=1),
            "profit_points": self._field(frame, "Profit Points", "20", start_row + 1, column=4),
            "safety_points": self._field(frame, "Safety Points", "10", start_row + 2, column=1),
            "time_exit": self._field(frame, "Time Exit (candles)", "10", start_row + 2, column=4),
            "cooldown": self._field(frame, "Cooldown", "5", start_row + 3, column=1),
            "chart_interval": self._interval_field(frame, "Chart Interval", "3 min", start_row + 3, column=4),
            "trend_set": self._trend_set_field(frame, "Trend Set", "Auto", start_row + 4, column=1),
            "bullish_threshold": self._field(frame, "Bullish Threshold", "16", start_row + 5, column=1),
            "bearish_threshold": self._field(frame, "Bearish Threshold", "-15", start_row + 5, column=4),
            "rsi_bull": self._field(frame, "RSI Bull", "55", start_row + 6, column=1),
            "rsi_bear": self._field(frame, "RSI Bear", "45", start_row + 6, column=4),
            "rsi_reversal_bullish": self._field(frame, "RSI Reversal Bullish", "70", start_row + 7, column=1),
            "rsi_reversal_bearish": self._field(frame, "RSI Reversal Bearish", "20", start_row + 7, column=4),
            "bullish_reversal_condition": self._field(frame, "Bullish Reversal Condition", "-20", start_row + 8, column=1),
            "bearish_reversal_condition": self._field(frame, "Bearish Reversal Condition", "10", start_row + 8, column=4),
            "buy_limit_score_low": self._field(frame, "Buy Limit Score Low", "40", start_row + 9, column=1),
            "market_entry_score": self._field(frame, "Market Entry Score", "50", start_row + 9, column=4),
            "minimum_body_percent": self._field(frame, "Min Body %", "20", start_row + 10, column=1),
            "minimum_close_position": self._field(frame, "Min Close Position", "55", start_row + 10, column=4),
            "market_entry_minimum_body_percent": self._field(frame, "Market Body %", "25", start_row + 11, column=1),
            "market_entry_minimum_close_position": self._field(frame, "Market Close Position", "60", start_row + 11, column=4),
            "trigger_upper_wick_max": self._field(frame, "Trigger Wick Max", "45", start_row + 12, column=1),
            "hard_rejection_upper_wick_max": self._field(frame, "Hard Wick Max", "50", start_row + 12, column=4),
            "volume_previous_multiplier": self._field(frame, "Prev Vol Mult", "0.80", start_row + 13, column=1),
            "avg_volume_minimum_multiplier": self._field(frame, "Avg Vol Min", "0.50", start_row + 13, column=4),
            "volume_pickup_avg_multiplier": self._field(frame, "Vol Pickup Avg", "0.70", start_row + 14, column=1),
            "large_candle_multiplier": self._field(frame, "Large Candle Mult", "2.2", start_row + 14, column=4),
            "move_from_low_max_multiplier": self._field(frame, "Move From Low Max", "1.10", start_row + 15, column=1),
            "gap_spike_multiplier": self._field(frame, "Gap Spike Mult", "1.2", start_row + 15, column=4),
            "buy_limit_offset_multiplier": self._field(frame, "Limit Offset Mult", "0.15", start_row + 16, column=1),
            "minimum_offset": self._field(frame, "Minimum Offset", "1", start_row + 16, column=4),
            "maximum_offset": self._field(frame, "Maximum Offset", "2", start_row + 17, column=1),
            "buy_limit_validity_seconds": self._field(frame, "Limit Validity Sec", "30", start_row + 17, column=4),
            "backtest_limit_fill_mode": self._field(frame, "Backtest Fill Mode", "CONSERVATIVE", start_row + 18, column=1),
            "enable_chop_filter": self._field(frame, "Enable Chop Filter", "false", start_row + 18, column=4),
            "aggressive_live_entry_enabled": self._field(frame, "Aggressive Live Entry", "false", start_row + 19, column=1),
            "aggressive_entry_score": self._field(frame, "Aggressive Score", "50", start_row + 19, column=4),
            "aggressive_upper_wick_max": self._field(frame, "Aggressive Wick Max", "35", start_row + 20, column=1),
            "aggressive_minimum_body_percent": self._field(frame, "Aggressive Body %", "25", start_row + 20, column=4),
            "aggressive_minimum_close_position": self._field(frame, "Aggressive Close Pos", "65", start_row + 21, column=1),
            "aggressive_move_from_low_max_multiplier": self._field(frame, "Aggressive Move Max", "0.90", start_row + 21, column=4),
            "aggressive_setup_score": self._field(frame, "Setup Forming Score", "40", start_row + 22, column=1),
            "one_entry_attempt_per_candle": self._field(frame, "One Attempt/Candle", "true", start_row + 22, column=4),
            "missed_limit_cooldown_candles": self._field(frame, "Missed Limit Cooldown", "0", start_row + 23, column=1),
            "max_spread_points": self._field(frame, "Max Spread Points", "2.0", start_row + 23, column=4),
            "chop_lookback_candles": self._field(frame, "Chop Lookback", "3", start_row + 24, column=1),
            "chop_overlap_count": self._field(frame, "Chop Overlap Count", "2", start_row + 24, column=4),
            "fast_ohlcv_entry_enabled": self._field(frame, "Fast OHLCV Entry", "true", start_row + 25, column=1),
            "max_daily_loss": self._field(frame, "Max Daily Loss", "0", start_row + 26, column=1),
            "max_daily_profit": self._field(frame, "Max Daily Profit", "0", start_row + 26, column=4),
            "max_consecutive_losses": self._field(frame, "Max Loss Streak", "0", start_row + 27, column=1),
            "square_off_time": self._field(frame, "Square Off Time", "15:20", start_row + 27, column=4),
            "order_product": self._order_product_field(frame, "Order Product", "NRML", start_row + 28, column=1),
            "stoploss_limit_buffer_points": self._field(frame, "Stoploss Limit Buffer", "2", start_row + 28, column=4),
            "live_option_market_entry_as_limit_enabled": self._field(frame, "Market Entry As Limit", "false", start_row + 29, column=1),
            "live_option_market_entry_limit_buffer_points": self._field(frame, "Market Entry Limit Buffer", "2", start_row + 29, column=4),
        }
        return fields

    def _default_settings_values(self):
        return dict(DEFAULT_SETTINGS)

    def _load_settings_profiles(self):
        return load_settings_profiles(SETTINGS_PROFILE_PATH)

    def _profile_name_for_attr(self, attr_name: str) -> str:
        return {
            "backtest_settings_values": "backtest",
            "paper_settings_values": "paper",
            "real_settings_values": "real",
        }.get(attr_name, attr_name)

    def _save_settings_profile(self, attr_name, values):
        profile_name = self._profile_name_for_attr(attr_name)
        saved = save_settings_profile(profile_name, values, SETTINGS_PROFILE_PATH)
        if attr_name in {
            "backtest_settings_values",
            "paper_settings_values",
            "real_settings_values",
        }:
            setattr(self, attr_name, saved)
        return saved

    def _ensure_settings_values(self, attr_name):
        if not hasattr(self, attr_name):
            setattr(self, attr_name, self._default_settings_values())
        return getattr(self, attr_name)

    def _populate_settings_fields(self, fields, values):
        for key, field in fields.items():
            value = setting_value(values, key)
            if key == "chart_interval":
                value = self._interval_label(value)
            if key == "trend_set":
                value = self._normalise_trend_set(value)
            if key == "order_product":
                value = self._normalise_order_product(value)
            if key in BOOLEAN_SETTING_KEYS:
                value = "Enabled" if self._setting_enabled(value) else "Disabled"
            self._set_field_value(field, str(value))

    def _settings_from_values(self, values):
        return settings_from_values(values)

    def _parse_setting_runtime_value(self, key, value):
        return parse_runtime_setting_value(key, value)

    def _setting_enabled(self, value):
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}

    def _open_settings_dialog(self, title, attr_name, on_save=None):
        current_values = dict(self._ensure_settings_values(attr_name))

        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.geometry("820x840")
        popup.minsize(760, 720)
        popup.configure(bg=PALETTE["bg"])
        popup.transient(self.root)
        popup.grab_set()

        body = self._card(popup, padx=16, pady=14)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        self._section_title(body, title, "Save applies these settings to the current workspace. Defaults restores the standard template.")
        fields = self._settings(body, 3)
        self._populate_settings_fields(fields, current_values)

        actions = tk.Frame(body, bg=PALETTE["surface"])
        actions.grid(row=35, column=0, columnspan=4, pady=(16, 0), sticky="w")

        def save():
            try:
                parsed = self._read_settings(fields)
            except Exception as exc:
                messagebox.showerror("Settings", f"Invalid setting: {exc}")
                return
            raw_values = {
                key: (
                    self._interval_label(field.get())
                    if key == "chart_interval"
                    else self._normalise_trend_set(field.get())
                    if key == "trend_set"
                    else self._normalise_order_product(field.get())
                    if key == "order_product"
                    else field.get().strip()
                )
                for key, field in fields.items()
            }
            setattr(self, attr_name, raw_values)
            self._save_settings_profile(attr_name, raw_values)
            if on_save:
                on_save(raw_values, parsed)
            self.set_status(f"{title} saved")
            popup.destroy()

        def defaults():
            self._populate_settings_fields(fields, self._default_settings_values())

        self.make_button(actions, "SAVE SETTINGS", save, PALETTE["success"], 16).grid(row=0, column=0, padx=(0, 8))
        self.make_button(actions, "DEFAULTS", defaults, PALETTE["neutral"], 12).grid(row=0, column=1, padx=8)
        self.make_button(actions, "CLOSE", popup.destroy, "#6b7280", 10).grid(row=0, column=2, padx=8)

    def _settings_summary_text(self, values, mode=""):
        if mode == "LIVE" and not values.get("zerodha_margin_fetched"):
            return (
                "Zerodha: not connected | Balance: not connected | "
                f"Lots {values.get('lot_size')} | Max trades {values.get('max_trades')} | "
                f"Target {values.get('profit_points')} | SL {values.get('safety_points')} | "
                f"Trend Set {self._normalise_trend_set(values.get('trend_set', 'Auto'))} | "
                f"Product {self._normalise_order_product(values.get('order_product', 'NRML'))}"
            )
        parts = [
            f"Balance {values.get('balance')}",
            f"Lots {values.get('lot_size')}",
            f"Max trades {values.get('max_trades')}",
            f"Target {values.get('profit_points')}",
            f"SL {values.get('safety_points')}",
            f"Trend Set {self._normalise_trend_set(values.get('trend_set', 'Auto'))}",
            f"RSI {values.get('rsi_bull')}/{values.get('rsi_bear')}",
            f"RSI reversal {values.get('rsi_reversal_bullish', DEFAULT_SETTINGS['rsi_reversal_bullish'])}/{values.get('rsi_reversal_bearish', DEFAULT_SETTINGS['rsi_reversal_bearish'])}",
            f"Reversal diff {values.get('bullish_reversal_condition', DEFAULT_SETTINGS['bullish_reversal_condition'])}/{values.get('bearish_reversal_condition', DEFAULT_SETTINGS['bearish_reversal_condition'])}",
            f"Fast score {values.get('buy_limit_score_low')}/{values.get('market_entry_score')}",
            f"Body/Close {values.get('minimum_body_percent')}%/{values.get('minimum_close_position')}%",
            f"Square-off {values.get('square_off_time')}",
            f"Product {self._normalise_order_product(values.get('order_product', 'NRML'))}",
        ]
        return " | ".join(parts)

    def _read_settings(self, fields):
        parsed = {
            key: self._parse_setting_runtime_value(key, field.get())
            for key, field in fields.items()
        }
        raise_for_fast_ohlcv_settings(parsed)
        return parsed

    def _load_df(self, path_entry, instrument="", option_data=False, strike="", expiry="", option_type=""):
        path = path_entry.get()
        df = clean_and_add_indicators(pd.read_csv(path))
        parsed = parse_option_metadata_from_text(os.path.basename(path))
        if option_data:
            df = ensure_option_formula_columns(df)
            df.attrs["data_kind"] = "option"
            strike = strike or parsed.get("strike", "")
            expiry = expiry or parsed.get("expiry", "")
            option_type = option_type or parsed.get("option_type", "")
        if instrument:
            df.attrs["instrument"] = instrument
            df.attrs["tradingsymbol"] = instrument
        if strike:
            df.attrs["strike"] = strike
        if expiry:
            df.attrs["expiry"] = expiry
        if option_type:
            df.attrs["option_type"] = option_type
        return df
