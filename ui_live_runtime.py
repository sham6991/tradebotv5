import calendar
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from reporting import timestamped_file
from result_paths import result_category_folder

if TYPE_CHECKING:
    from execution_v2 import Executor
    LiveOptionRow = tuple[ttk.Combobox, tk.Entry, ttk.Combobox, tk.Entry, tk.Entry]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_FOLDER = os.path.join(BASE_DIR, "results")
os.makedirs(RESULT_FOLDER, exist_ok=True)


class LiveRuntimeMixin:
    if TYPE_CHECKING:
        root: tk.Tk
        executor: "Executor"
        live_mode: str
        pnl_label: tk.Label
        feed_label: tk.Label
        live_settings_summary: tk.StringVar
        history_interval: ttk.Combobox
        history_days: tk.Entry
        nifty_token: tk.Entry
        dashboard_refresh_after_id: str | None
        dashboard_refresh_interval_ms: int
        margin_refresh_interval_ms: int
        last_margin_refresh_at: float
        margin_refresh_in_progress: bool
        tick_render_scheduled: bool
        tick_render_interval_ms: int
        max_tick_lines_per_render: int
        tick_buffer: dict[str, list[str]]
        pending_tick_lines: dict[str, deque[str]]
        latest_tick_by_bucket: dict[str, str]
        tick_outputs: dict[str, Any]
        current_token_map: dict[int, str]
        live_options: list["LiveOptionRow"]
        live_log_active_rows: dict[str, Any]
        live_order_history_rows: list[dict[str, Any]]
        live_trade_snapshot: dict[str, Any]
        log_trade_table: ttk.Treeview
        live_trade_table: ttk.Treeview
        order_history_table: ttk.Treeview
        real_settings_values: dict[str, str]

        def _card(self, parent: tk.Misc, padx: int = 16, pady: int = 14) -> tk.Frame: ...
        def _ensure_settings_values(self, attr_name: str) -> dict[str, str]: ...
        def _save_settings_profile(self, attr_name: str, values: dict[str, str]) -> None: ...
        def _settings_summary_text(self, values: dict[str, str], mode: str = "") -> str: ...
        def _settings_from_values(self, values: dict[str, str]) -> Any: ...
        def _normalise_interval(self, value: Any) -> str: ...
        def _default_settings_values(self) -> dict[str, str]: ...
        def _set_field_value(self, field: tk.Entry | ttk.Combobox, value: Any) -> None: ...
        def _sync_zerodha_client_for_mode(self, mode: str | None = None) -> None: ...
        def open_zerodha_auth_wizard(self, auto_started: bool = False) -> None: ...
        def set_status(self, text: str) -> None: ...

    LOG_TRADE_COLUMNS = (
        "Trade ID",
        "Time",
        "Symbol / Instrument",
        "Option Type",
        "Order Side",
        "Order Type",
        "Product Type",
        "Quantity",
        "Ordered Quantity",
        "Filled Quantity",
        "Pending Quantity",
        "Cancelled Quantity",
        "Is Partial Fill",
        "Order Status",
        "Entry Price",
        "Exit Price",
        "Limit Price",
        "Trigger Price",
        "Early Score",
        "Buy Setup",
        "NIFTY Trend",
        "Trend Alignment",
        "Entry Type",
        "Final Decision",
        "Decision Reason",
        "Main Fast Trigger Passed",
        "Rejection Active",
        "Rejection Reason",
        "BodyPercent",
        "ClosePosition",
        "UpperWickPercent",
        "AvgRange10",
        "AvgVolume10",
        "Entry Filters Passed",
        "Entry Block Reason",
        "Stop Loss Price",
        "Target Price",
        "Current LTP",
        "Live PnL",
        "Exit Reason",
        "Zerodha Order ID",
        "Remarks / Error Message",
    )
    LIVE_TRADE_FIELDS = (
        "Trade ID",
        "Instrument / Symbol",
        "Option Type",
        "Current Trade Side",
        "Entry Time",
        "Entry Price",
        "Early Score at Entry",
        "Quantity",
        "Target Price",
        "Stop Loss Price",
        "Current LTP",
        "Live PnL",
        "Live PnL %",
        "Status",
    )
    ORDER_HISTORY_COLUMNS = (
        "Session Trade No",
        "Timestamp",
        "Instrument / Symbol",
        "Option Type",
        "Action",
        "Order Type",
        "Quantity",
        "Ordered Quantity",
        "Filled Quantity",
        "Pending Quantity",
        "Cancelled Quantity",
        "Is Partial Fill",
        "Order Status",
        "Entry Price",
        "Early Score",
        "Buy Setup",
        "NIFTY Trend",
        "Trend Alignment",
        "Entry Type",
        "Final Decision",
        "Decision Reason",
        "Main Fast Trigger Passed",
        "Rejection Active",
        "Rejection Reason",
        "BodyPercent",
        "ClosePosition",
        "UpperWickPercent",
        "AvgRange10",
        "AvgVolume10",
        "Entry Filters Passed",
        "Entry Block Reason",
        "Exit Price",
        "Exit Reason",
        "Target Price",
        "Stop Loss Price",
        "LTP at Order Placement",
        "Zerodha Order ID",
        "Parent Order ID",
        "Related Trade ID",
        "Error / Rejection Reason",
    )

    def _build_live_log_tabs(self, parent):
        self.live_log_active_rows = {}
        self.live_order_history_rows = []
        self.live_trade_snapshot = {}
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)

        log_tab = tk.Frame(notebook, bg="#f4f6f8")
        live_tab = tk.Frame(notebook, bg="#f4f6f8")
        history_tab = tk.Frame(notebook, bg="#f4f6f8")
        notebook.add(log_tab, text="Log Trade")
        notebook.add(live_tab, text="Live Trade")
        notebook.add(history_tab, text="Order History")

        self.log_trade_table = self._make_live_tree(log_tab, self.LOG_TRADE_COLUMNS, height=8)
        self.live_trade_table = self._make_live_tree(live_tab, self.LIVE_TRADE_FIELDS, height=4)
        self.order_history_table = self._make_live_tree(history_tab, self.ORDER_HISTORY_COLUMNS, height=8)
        self._render_live_trade_snapshot({})

    def _make_live_tree(self, parent, columns, height=8):
        frame = self._card(parent, padx=8, pady=8)
        frame.pack(fill="both", expand=True, padx=0, pady=0)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height, style="Trade.Treeview")
        compact_columns = {
            "Trade ID": 150,
            "Option Type": 90,
            "Current Trade Side": 130,
            "Entry Time": 150,
            "Entry Price": 100,
            "Early Score at Entry": 130,
            "Buy Setup": 100,
            "NIFTY Trend": 105,
            "Trend Alignment": 120,
            "Entry Type": 115,
            "Final Decision": 140,
            "Decision Reason": 180,
            "Main Fast Trigger Passed": 170,
            "Rejection Active": 125,
            "Rejection Reason": 180,
            "BodyPercent": 105,
            "ClosePosition": 120,
            "UpperWickPercent": 135,
            "AvgRange10": 105,
            "AvgVolume10": 115,
            "Entry Filters Passed": 145,
            "Quantity": 90,
            "Ordered Quantity": 115,
            "Filled Quantity": 110,
            "Pending Quantity": 115,
            "Cancelled Quantity": 125,
            "Is Partial Fill": 105,
            "Target Price": 105,
            "Stop Loss Price": 115,
            "Current LTP": 105,
            "Live PnL": 105,
            "Live PnL %": 105,
            "Status": 95,
        }
        for col in columns:
            tree.heading(col, text=col)
            width = compact_columns.get(col, 120)
            if col in ("Symbol / Instrument", "Instrument / Symbol", "Remarks / Error Message", "Error / Rejection Reason", "Entry Block Reason"):
                width = 180
            if col in ("Field", "Value"):
                width = 220 if col == "Field" else 320
            tree.column(col, width=width, anchor="center", minwidth=90)
        tree.tag_configure("odd", background="#f8fafc")
        tree.tag_configure("even", background="#eef2ff")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        return tree

    def _render_table_rows(self, tree, columns, rows):
        if not tree or not tree.winfo_exists():
            return
        tree.delete(*tree.get_children())
        for index, row in enumerate(rows):
            tag = "even" if index % 2 == 0 else "odd"
            values = [self._display_value(row.get(col, "")) for col in columns]
            tree.insert("", "end", values=values, tags=(tag,))

    def _render_live_trade_snapshot(self, snapshot):
        if not hasattr(self, "live_trade_table"):
            return
        rows = [snapshot] if snapshot else []
        self._render_table_rows(self.live_trade_table, self.LIVE_TRADE_FIELDS, rows)

    def _display_value(self, value):
        if value == "" or value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)

    def _cancel_dashboard_refresh(self):
        after_id = getattr(self, "dashboard_refresh_after_id", None)
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self.dashboard_refresh_after_id = None

    def _start_dashboard_refresh(self):
        self._cancel_dashboard_refresh()
        self._schedule_dashboard_refresh()

    def _schedule_dashboard_refresh(self):
        self.dashboard_refresh_after_id = self.root.after(
            self.dashboard_refresh_interval_ms,
            self._dashboard_refresh_tick,
        )

    def _dashboard_refresh_tick(self):
        self.dashboard_refresh_after_id = None
        if not hasattr(self, "pnl_label"):
            return

        session = self.executor.live_real_session if self.live_mode == "LIVE" else self.executor.live_paper_session
        if self.live_mode == "PAPER":
            if session:
                self.pnl_label.config(text=f"Total P&L: {session.balance:.2f}")
            else:
                balance = self._ensure_settings_values("paper_settings_values").get("balance", "0")
                self.pnl_label.config(text=f"Total P&L: {float(balance):.2f}")
        else:
            self._maybe_refresh_real_margin_async()
            values = self._ensure_settings_values("real_settings_values")
            if values.get("zerodha_margin_fetched"):
                margin = float(values.get("balance", 0) or 0)
                pnl_text = ""
                if session:
                    pnl = session.balance - session.daily_start_balance
                    pnl_text = f" | Session P&L: {pnl:.2f}"
                self.pnl_label.config(text=f"Available Margin: {margin:.2f}{pnl_text}")
            else:
                self.pnl_label.config(text="Available Margin: not connected")

        if hasattr(self, "feed_label"):
            metrics = self.executor.feed_metrics()
            if metrics["processed_ticks"] or metrics["backlog"] or metrics["dropped_batches"]:
                last_tick = ""
                if metrics["last_tick_processed_at"]:
                    last_tick = datetime.fromtimestamp(metrics["last_tick_processed_at"]).strftime("%H:%M:%S")
                self.feed_label.config(
                    text=(
                        f"Feed: {metrics['feed_status']} | {metrics['ticks_per_second']:.0f} ticks/s | "
                        f"backlog {metrics['backlog']} | dropped {metrics['dropped_batches']} | "
                        f"last {last_tick or '-'}"
                    )
                )

        if session and hasattr(session, "latest_live_trade"):
            self._apply_live_log_payload({
                "active_orders": list(getattr(session, "active_orders", {}).values()),
                "live_trade": dict(getattr(session, "latest_live_trade", {}) or {}),
                "order_event": None,
            })

        self._schedule_dashboard_refresh()

    def _maybe_refresh_real_margin_async(self):
        zerodha: Any = self.executor.zerodha
        if self.live_mode != "LIVE" or not zerodha:
            return
        now = time.monotonic()
        if now - self.last_margin_refresh_at < self.margin_refresh_interval_ms / 1000:
            return
        if self.margin_refresh_in_progress:
            return

        self.last_margin_refresh_at = now
        self.margin_refresh_in_progress = True

        def worker():
            try:
                margin = zerodha.available_margin()
                error = None
            except Exception as exc:
                margin = None
                error = str(exc)

            def apply_result():
                self.margin_refresh_in_progress = False
                if error:
                    self.set_status(f"Margin refresh failed: {error}")
                    return
                if margin is not None:
                    self._apply_real_margin(float(margin), status_prefix="Margin refreshed")

            self.root.after(0, apply_result)

        threading.Thread(target=worker, name="tradebot_margin_refresh", daemon=True).start()

    def _apply_real_margin(self, margin, status_prefix="Zerodha available margin fetched"):
        values = dict(self._ensure_settings_values("real_settings_values"))
        values["balance"] = f"{float(margin):.2f}"
        values["zerodha_margin_fetched"] = "true"
        self.real_settings_values = values
        self._save_settings_profile("real_settings_values", values)
        if hasattr(self, "live_settings_summary"):
            self.live_settings_summary.set(self._settings_summary_text(values, mode="LIVE"))
        if hasattr(self, "pnl_label"):
            session = self.executor.live_real_session
            pnl_text = ""
            if session:
                pnl_text = f" | Session P&L: {session.balance - session.daily_start_balance:.2f}"
            self.pnl_label.config(text=f"Available Margin: {float(margin):.2f}{pnl_text}")
        self.set_status(f"{status_prefix}: {float(margin):.2f}")

    def _refresh_real_margin(self, show_errors=False):
        zerodha: Any = self.executor.zerodha
        if self.live_mode != "LIVE" or not zerodha:
            return None
        try:
            margin = zerodha.available_margin()
        except Exception as exc:
            if show_errors:
                messagebox.showerror("ZERODHA MARGIN ERROR", str(exc))
            self.set_status("Zerodha connected, margin fetch failed")
            return None
        if margin is None:
            self.set_status("Zerodha connected, margin unavailable")
            return None

        self._apply_real_margin(float(margin))
        return float(margin)

    def connect_zerodha(self):
        self.open_zerodha_auth_wizard()

    def start_market_feed(self):
        try:
            self.set_status("Starting market feed...")
            self._ensure_zerodha_connected()
            self._resolve_live_instruments()
            token_map = self._live_token_map()
            self.current_token_map = token_map
            tokens = list(token_map.keys())

            self.executor.start_market_feed(
                tokens,
                on_ticks=self._queue_ticks,
                on_connect=lambda response: self._queue_feed_status("Feed: connected"),
                on_close=lambda code, reason: self._queue_feed_status(f"Feed: closed ({code}) {reason}")
            )
            self.feed_label.config(text="Feed: connecting...")
        except Exception as exc:
            self.set_status("Market feed failed")
            messagebox.showerror("KITE TICKER ERROR", str(exc))

    def _live_token_map(self):
        self._resolve_live_instruments()

        token_map = {int(self.nifty_token.get().strip()): "NIFTY"}

        for index, (_, _, _, _, token_entry) in enumerate(self.live_options):
            token = token_entry.get().strip()
            if not token:
                raise ValueError(f"Enter option token for option {index + 1}.")
            token_map[int(token)] = f"OPTION_{index}"

        return token_map

    def _option_contracts(self):
        self._resolve_live_instruments()
        contracts = []

        for index, (type_entry, strike_entry, expiry_entry, symbol_entry, token_entry) in enumerate(self.live_options):
            tradingsymbol = symbol_entry.get().strip()
            token = token_entry.get().strip()

            if not tradingsymbol:
                raise ValueError(f"Enter tradingsymbol for option {index + 1}.")
            if not token:
                raise ValueError(f"Enter option token for option {index + 1}.")

            contracts.append({
                "tradingsymbol": tradingsymbol,
                "token": int(token),
                "strike": strike_entry.get().strip(),
                "expiry": expiry_entry.get().strip(),
                "option_type": type_entry.get().strip().upper(),
            })

        return contracts

    def auto_fill_instruments(self):
        try:
            self.set_status("Fetching instruments...")
            self._ensure_zerodha_connected()
            self._resolve_live_instruments()
            self.set_status("NIFTY token and option instruments filled")
            messagebox.showinfo("Instruments", "NIFTY token and option symbols/tokens filled.")
        except Exception as exc:
            self.set_status("Instrument fetch failed")
            messagebox.showerror("INSTRUMENT ERROR", str(exc))

    def _resolve_live_instruments(self):
        zerodha: Any = self.executor.zerodha
        if not zerodha:
            raise ValueError("Connect Zerodha first.")

        if not self.nifty_token.get().strip():
            self.nifty_token.insert(0, str(zerodha.get_nifty50_token()))

        for index, (type_entry, strike_entry, expiry_entry, symbol_entry, token_entry) in enumerate(self.live_options):
            symbol = symbol_entry.get().strip()
            token = token_entry.get().strip()

            if symbol and token:
                continue

            self.fetch_option_row(type_entry, strike_entry, expiry_entry, symbol_entry, token_entry)

    def fetch_nifty_token(self):
        try:
            self._ensure_zerodha_connected()
            zerodha: Any = self.executor.zerodha
            if not zerodha:
                raise ValueError("Connect Zerodha first.")
            token = zerodha.get_nifty50_token()
            self.nifty_token.delete(0, tk.END)
            self.nifty_token.insert(0, str(token))
            self.set_status("NIFTY token fetched")
        except Exception as exc:
            self.set_status("NIFTY token fetch failed")
            messagebox.showerror("NIFTY TOKEN ERROR", str(exc))

    def load_expiry_choices(self, type_entry, strike_entry, expiry_entry):
        if not self.executor.zerodha:
            return

        try:
            expiries = self.executor.zerodha.get_option_expiries(
                option_type=type_entry.get().strip().upper() or None,
                strike=strike_entry.get().strip() or None,
                name="NIFTY"
            )
            expiry_entry["values"] = expiries
        except Exception:
            expiry_entry["values"] = []

    def show_expiry_calendar(self, expiry_entry):
        selected_text = expiry_entry.get().strip()

        try:
            selected = datetime.strptime(selected_text, "%Y-%m-%d")
        except ValueError:
            selected = datetime.now()

        popup = tk.Toplevel(self.root)
        popup.title("Select Expiry")
        popup.resizable(False, False)
        popup.configure(bg="#ffffff")
        popup.transient(self.root)
        popup.grab_set()

        state = {"year": selected.year, "month": selected.month}

        header = tk.Frame(popup, bg="#ffffff", padx=10, pady=8)
        header.pack(fill="x")
        body = tk.Frame(popup, bg="#ffffff", padx=10, pady=6)
        body.pack()

        title = tk.Label(header, bg="#ffffff", fg="#111827", font=("Segoe UI", 10, "bold"), width=18)
        title.pack(side="left", padx=8)

        def choose(day):
            value = f"{state['year']:04d}-{state['month']:02d}-{day:02d}"
            expiry_entry.delete(0, tk.END)
            expiry_entry.insert(0, value)
            popup.destroy()

        def render():
            for widget in body.winfo_children():
                widget.destroy()

            title.config(text=f"{calendar.month_name[state['month']]} {state['year']}")

            for col, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
                tk.Label(body, text=name, width=5, bg="#ffffff", fg="#6b7280").grid(row=0, column=col, pady=(0, 4))

            month_days = calendar.monthcalendar(state["year"], state["month"])

            for row_index, week in enumerate(month_days, start=1):
                for col_index, day in enumerate(week):
                    if day == 0:
                        tk.Label(body, text="", width=5, bg="#ffffff").grid(row=row_index, column=col_index, padx=2, pady=2)
                        continue

                    tk.Button(
                        body,
                        text=str(day),
                        width=5,
                        relief="flat",
                        bg="#eef2ff" if col_index < 5 else "#f3f4f6",
                        command=lambda d=day: choose(d)
                    ).grid(row=row_index, column=col_index, padx=2, pady=2)

        def previous_month():
            state["month"] -= 1
            if state["month"] == 0:
                state["month"] = 12
                state["year"] -= 1
            render()

        def next_month():
            state["month"] += 1
            if state["month"] == 13:
                state["month"] = 1
                state["year"] += 1
            render()

        tk.Button(header, text="<", width=3, command=previous_month).pack(side="left")
        tk.Button(header, text=">", width=3, command=next_month).pack(side="right")

        render()

    def fetch_option_row(self, type_entry, strike_entry, expiry_entry, symbol_entry, token_entry):
        if not self.executor.zerodha:
            raise ValueError("Connect Zerodha first.")

        option_type = type_entry.get().strip().upper()
        strike = strike_entry.get().strip()
        expiry = expiry_entry.get().strip() or None

        if not option_type or not strike or not expiry:
            raise ValueError("Enter Type, Strike, and Expiry before fetching this option.")

        contract = self.executor.zerodha.find_option_contract(
            option_type=option_type,
            strike=strike,
            expiry=expiry,
            name="NIFTY"
        )

        symbol_entry.delete(0, tk.END)
        symbol_entry.insert(0, contract["tradingsymbol"])
        token_entry.delete(0, tk.END)
        token_entry.insert(0, str(contract["instrument_token"]))
        expiry_entry.delete(0, tk.END)
        expiry_entry.insert(0, str(contract["expiry"])[:10])

    def fetch_option_row_safe(self, type_entry, strike_entry, expiry_entry, symbol_entry, token_entry):
        try:
            self._ensure_zerodha_connected()
            self.fetch_option_row(type_entry, strike_entry, expiry_entry, symbol_entry, token_entry)
        except Exception as exc:
            messagebox.showerror("OPTION FETCH ERROR", str(exc))

    def _ensure_zerodha_connected(self):
        mode = "LIVE" if self.live_mode == "LIVE" else "PAPER"
        self._sync_zerodha_client_for_mode(mode)
        if self.executor.zerodha:
            return
        self.root.after(0, self.open_zerodha_auth_wizard)
        label = "real-money Zerodha" if mode == "LIVE" else "paper-data Zerodha"
        raise ValueError(f"Connect {label} first.")

    def _queue_feed_status(self, text):
        self.root.after(0, lambda: (self.feed_label.config(text=text), self.set_status(text)))

    def _queue_alert(self, alert):
        self.root.after(0, lambda alert=alert: self._apply_live_alert(alert))

    def _apply_live_alert(self, alert):
        if not alert:
            return
        self.last_live_alert = alert
        level = str(alert.get("level", "ALERT") or "ALERT").upper()
        code = str(alert.get("code", "") or "")
        message = str(alert.get("message", "") or "")
        text = f"{level}: {message}" if message else level
        if code:
            text = f"{level} {code}: {message}" if message else f"{level} {code}"
        if hasattr(self, "feed_label"):
            self.feed_label.config(text=f"Alert: {code or level}")
        self.set_status(text)

    def _queue_ticks(self, ticks):
        self.root.after(0, lambda: self._buffer_ticks_for_render(ticks))

    def _buffer_ticks_for_render(self, ticks):
        self.feed_label.config(text=f"Feed: live ticks {len(ticks)}")

        for tick in ticks:
            token = tick.get("instrument_token", "")
            ltp = tick.get("last_price", "")
            volume = tick.get("volume_traded", "")
            bucket, name = self._tick_bucket(token)
            line = f"{name} | Token: {token} | LTP: {ltp} | Volume: {volume}"
            self.latest_tick_by_bucket[bucket] = line
            self.pending_tick_lines.setdefault(bucket, deque()).append(line)
            while len(self.pending_tick_lines[bucket]) > 500:
                self.pending_tick_lines[bucket].popleft()

        if not self.tick_render_scheduled:
            self.tick_render_scheduled = True
            self.root.after(self.tick_render_interval_ms, self._flush_tick_render)

    def _flush_tick_render(self):
        self.tick_render_scheduled = False
        dropped = getattr(self.executor, "dropped_tick_batches", 0)
        backlog = self.executor.tick_backlog() if hasattr(self.executor, "tick_backlog") else 0
        if dropped:
            self.set_status(f"Feed active. Queue backlog {backlog}. Dropped visual/input batches {dropped}.")

        for bucket, pending in self.pending_tick_lines.items():
            if not pending:
                continue

            rendered = []
            while pending and len(rendered) < self.max_tick_lines_per_render:
                rendered.append(pending.popleft())

            self.tick_buffer.setdefault(bucket, []).extend(rendered)
            latest = self.latest_tick_by_bucket.get(bucket)
            if latest and (not self.tick_buffer[bucket] or self.tick_buffer[bucket][-1] != latest):
                self.tick_buffer[bucket].append(latest)
            self.tick_buffer[bucket] = self.tick_buffer[bucket][-300:]

            output = self.tick_outputs.get(bucket)
            if output is not None and output.winfo_exists():
                output.insert(tk.END, "\n".join(rendered) + "\n")
                output.see(tk.END)

        if any(self.pending_tick_lines.values()):
            self.tick_render_scheduled = True
            self.root.after(self.tick_render_interval_ms, self._flush_tick_render)

    def _tick_bucket(self, token):
        try:
            token = int(token)
        except (TypeError, ValueError):
            return "NIFTY", "UNKNOWN"

        name = self.current_token_map.get(token, "")
        if name == "NIFTY":
            return "NIFTY", "NIFTY"
        if name == "OPTION_0":
            return "CE", "CE"
        if name == "OPTION_1":
            return "PE", "PE"
        return "NIFTY", name or "UNKNOWN"

    def show_tick_log_window(self):
        popup = tk.Toplevel(self.root)
        popup.title("Live Tick Log")
        popup.geometry("820x460")
        popup.configure(bg="#f4f6f8")

        notebook = ttk.Notebook(popup)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.tick_outputs = {}
        for bucket in ("NIFTY", "CE", "PE"):
            tab = tk.Frame(notebook, bg="#f4f6f8")
            notebook.add(tab, text=bucket)
            output = scrolledtext.ScrolledText(tab, height=18, font=("Consolas", 9))
            output.pack(fill="both", expand=True)
            self.tick_outputs[bucket] = output
            rows = self.tick_buffer.get(bucket, [])
            if rows:
                output.insert(tk.END, "\n".join(rows) + "\n")
                output.see(tk.END)

    def start_live_mode(self):
        self.set_status("Starting live worker...")
        thread = threading.Thread(target=self._run_live_worker, daemon=True)
        thread.start()

    def _run_live_worker(self):
        try:
            self._ensure_zerodha_connected()
            self.root.after(0, lambda: self.feed_label.config(text="Fetching historical candles..."))
            self.root.after(0, lambda: self.set_status("Fetching historical candles..."))
            settings_attr = "paper_settings_values" if self.live_mode == "PAPER" else "real_settings_values"
            if self.live_mode == "LIVE":
                self._refresh_real_margin(show_errors=False)
            settings = self._settings_from_values(self._ensure_settings_values(settings_attr))
            interval = settings.get("chart_interval") or self._normalise_interval(self.history_interval.get())
            nifty, options = self.executor.fetch_live_history(
                self.nifty_token.get().strip(),
                self._option_contracts(),
                days=int(self.history_days.get()),
                interval=interval
            )
            token_map = self._live_token_map()
            self.current_token_map = token_map
            paper_file = timestamped_file("paper_trading", result_category_folder(RESULT_FOLDER, "paper_trading"))
            real_file = timestamped_file("real_money_trading", result_category_folder(RESULT_FOLDER, "real_money_trading"))

            if self.live_mode == "PAPER":
                self.executor.start_live_paper_trading(
                    nifty,
                    options,
                    token_map,
                    settings,
                    paper_file,
                    on_trade=self._queue_trade_update,
                    on_order_update=self._queue_order_update,
                    on_alert=self._queue_alert,
                    on_ticks=self._queue_ticks,
                    on_connect=lambda response: self._queue_feed_status("Feed: live paper connected"),
                    on_close=lambda code, reason: self._queue_feed_status(f"Feed: closed ({code}) {reason}")
                )
                self.root.after(0, lambda: (self.feed_label.config(text="Feed: connecting live paper..."), self.set_status("Live paper feed connecting...")))
                return

            self.executor.start_live_real_trading(
                nifty,
                options,
                token_map,
                settings,
                real_file,
                on_trade=self._queue_trade_update,
                on_order_update=self._queue_order_update,
                on_alert=self._queue_alert,
                on_ticks=self._queue_ticks,
                on_connect=lambda response: self._queue_feed_status("Feed: real trading connected"),
                on_close=lambda code, reason: self._queue_feed_status(f"Feed: closed ({code}) {reason}")
            )
            self.root.after(0, lambda: (self.feed_label.config(text="Feed: connecting real trading..."), self.set_status("Real trading feed connecting...")))
        except Exception as exc:
            self.root.after(0, lambda: self.set_status("Live start failed"))
            self.root.after(0, lambda exc=exc: messagebox.showerror("ERROR", str(exc)))

    def load_live_sample_preset(self):
        settings_attr = "paper_settings_values" if self.live_mode == "PAPER" else "real_settings_values"
        setattr(self, settings_attr, self._default_settings_values())
        if self.live_mode == "LIVE":
            getattr(self, settings_attr).pop("zerodha_margin_fetched", None)
        self._save_settings_profile(settings_attr, getattr(self, settings_attr))
        if hasattr(self, "live_settings_summary"):
            self.live_settings_summary.set(self._settings_summary_text(getattr(self, settings_attr), mode=self.live_mode))
        if self.live_mode == "LIVE" and hasattr(self, "pnl_label"):
            self.pnl_label.config(text="Available Margin: not connected")
        self._set_field_value(self.history_interval, "3 min")
        if not self.history_days.get().strip():
            self.history_days.insert(0, "5")
        self.set_status("Live preset loaded")

    def square_off_open_position(self):
        try:
            trade = self.executor.square_off_open_position()
            if trade is None:
                self.set_status("No open position to square off")
                messagebox.showinfo("Square Off", "No open position to square off.")
            else:
                self.set_status("Open position squared off")
        except Exception as exc:
            self.set_status("Square off failed")
            messagebox.showerror("SQUARE OFF ERROR", str(exc))

    def activate_kill_switch(self):
        confirmed = messagebox.askyesno(
            "Kill Switch",
            (
                "Activate kill switch for this session?\n\n"
                "This blocks all new entries immediately. It does not square off an open position."
            ),
        )
        if not confirmed:
            return
        reason = f"Manual kill switch from {self.live_mode} UI"
        blocked_reason = self.executor.activate_kill_switch(reason)
        if not blocked_reason:
            self.set_status("No active session to disable")
            messagebox.showinfo("Kill Switch", "Start a live/paper session before activating the kill switch.")
            return
        self.set_status(blocked_reason)
        if hasattr(self, "feed_label"):
            self.feed_label.config(text=f"Feed: {blocked_reason}")
        messagebox.showwarning("Kill Switch", blocked_reason)

    def _queue_trade_update(self, trade, balance):
        self.root.after(0, lambda: self._add_trade_row(trade, balance))

    def _queue_order_update(self, payload):
        self.root.after(0, lambda: self._apply_live_log_payload(payload))

    def _apply_live_log_payload(self, payload):
        if not payload:
            return
        health = payload.get("health")
        if health is not None:
            self.live_health_snapshot = health
        active_orders = payload.get("active_orders")
        if active_orders is not None and hasattr(self, "log_trade_table"):
            active_statuses = {"PENDING", "OPEN", "TRIGGER PENDING", "COMPLETE", "REJECTED", "CANCELLED"}
            rows = [row for row in active_orders if row.get("Order Status", "") in active_statuses]
            self._render_table_rows(self.log_trade_table, self.LOG_TRADE_COLUMNS, rows)

        live_trade = payload.get("live_trade")
        if live_trade is not None:
            self.live_trade_snapshot = live_trade
            self._render_live_trade_snapshot(live_trade)

        event = payload.get("order_event")
        if event and hasattr(self, "order_history_table"):
            self.live_order_history_rows.append(event)
            self._render_table_rows(
                self.order_history_table,
                self.ORDER_HISTORY_COLUMNS,
                self.live_order_history_rows,
            )

    def _add_trade_row(self, trade, balance):
        if self.live_mode == "LIVE":
            values = self._ensure_settings_values("real_settings_values")
            if values.get("zerodha_margin_fetched"):
                margin = float(values.get("balance", 0) or 0)
                session = self.executor.live_real_session
                pnl_text = ""
                if session:
                    pnl_text = f" | Session P&L: {session.balance - session.daily_start_balance:.2f}"
                self.pnl_label.config(text=f"Available Margin: {margin:.2f}{pnl_text}")
            else:
                self.pnl_label.config(text=f"Session Balance: {balance:.2f}")
        else:
            self.pnl_label.config(text=f"Total P&L: {balance:.2f}")
        self.set_status(f"Trade update received. Balance {balance:.2f}")

    def _money(self, value):
        if value == "" or value is None:
            return ""
        return f"{float(value):.2f}"
