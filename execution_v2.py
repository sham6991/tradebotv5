import json
import os
import queue
import threading
import time
from datetime import datetime, timedelta

import pandas as pd

from candle_builder import CandleBuilder
from config import LOT_SIZE
from config_profile import apply_settings_profile
from engine import TradingEngine, append_datetime_index_key, attach_datetime_index_map, timestamp_key
from event_logger import (
    ENTRY_FILLED,
    KILL_SWITCH_ACTIVATED,
    ORDER_CANCELLED,
    ORDER_COMPLETE,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    ORDER_REJECTED,
    PARTIAL_EXIT_DETECTED,
    PROTECTIVE_ORDER_PLACED,
    RECONCILIATION_ERROR,
    RECONCILIATION_WARNING,
    StructuredEventLogger,
)
from indicators import append_clean_candle, clean_and_add_indicators
from order_manager import ZerodhaOrderManager
from position_reconciler import PositionReconciler
from preflight import validate_live_preflight
from reporting import BufferedExcelWriter, format_datetime_value
from risk_guard import LiveRiskGuard
from session_audit import write_session_audit
from sqlite_store import AsyncTradingStore, TradingStore
from strategy import append_option_formula_row, build_scoring_row, ensure_option_formula_columns
from zerodha_client import ZerodhaClient


class LivePaperSession:
    def __init__(
        self,
        nifty,
        option_dfs,
        token_map,
        settings,
        save_path=None,
        on_trade=None,
        on_order_update=None,
        on_alert=None,
        mode="PAPER",
        zerodha=None,
    ):
        self.nifty = nifty
        self.options = option_dfs
        self.token_map = {int(k): v for k, v in token_map.items()}
        self.settings = settings
        self.save_path = save_path
        self.on_trade = on_trade
        self.on_order_update = on_order_update
        self.on_alert = on_alert
        self.mode = mode
        self.zerodha = zerodha
        self.orders = ZerodhaOrderManager(zerodha=zerodha, mode=mode, default_lot_size=LOT_SIZE)
        self.engine = TradingEngine(settings["cooldown"])
        self.balance = float(settings.get("balance", 0))
        if self.mode == "LIVE" and self.zerodha:
            try:
                margin = self.orders.available_margin()
                if margin is not None:
                    self.balance = float(margin)
                    self.settings["balance"] = self.balance
            except Exception as exc:
                self._initial_margin_error = str(exc)
            else:
                self._initial_margin_error = ""
        else:
            self._initial_margin_error = ""
        self.settings_profile = apply_settings_profile(self.settings)
        self.lots = int(settings["lot_size"])
        self.max_trades = int(settings["max_trades"])
        self.trade_count = 0
        self.trades = []
        self.open_position = None
        self.pending_entry = None
        self.state_lock = threading.RLock()
        self.order_transition_in_progress = False
        self.order_status_poll_interval = float(settings.get("order_status_poll_interval", 0.5) or 0.5)
        self.order_monitor_stop = threading.Event()
        self.order_monitor_thread = None
        self.last_order_status_by_id = {}
        self.order_idempotency_records = {}
        self.order_idempotency_in_progress = set()
        self.duplicate_order_suppressed = 0
        self.active_orders = {}
        self.order_history = []
        self.latest_live_trade = {}
        self.latest_ltp_by_option = {}
        self.session_closed = False
        self.ui_update_interval = max(0.0, float(settings.get("ui_update_interval", 0.25) or 0.25))
        self.last_ui_update_at = 0.0
        self.suppressed_ui_updates = 0
        self.emitted_ui_updates = 0
        self.session_id = f"{self.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.settings["session_id"] = self.session_id
        self.last_candle_index = -1
        self.interval_minutes = self._interval_minutes(settings)
        self.candle_builder = CandleBuilder(self.interval_minutes)
        self.live_candle_memory_limit = max(0, int(settings.get("live_candle_memory_limit", 2000) or 0))
        self._prepare_datetime_indexes()
        self.risk_guard = LiveRiskGuard(self.settings, starting_balance=self.balance)
        self.daily_start_balance = self.risk_guard.daily_start_balance
        self.consecutive_losses = self.risk_guard.consecutive_losses
        self.trading_blocked_reason = self.risk_guard.blocked_reason
        self.candle_log_path = (save_path or "").replace(".xlsx", "_candles.xlsx") if save_path else None
        self.audit_report_path = (save_path or "").replace(".xlsx", "_audit.json") if save_path else None
        self.excel_writer = BufferedExcelWriter(
            flush_interval=float(settings.get("excel_flush_interval", 1.0) or 1.0),
            max_batch_rows=int(settings.get("excel_batch_rows", 100) or 100),
        ) if save_path else None
        self.store = self._build_store(save_path)
        self.event_logger = StructuredEventLogger(
            self.store,
            session_id=self.session_id,
            source="LivePaperSession",
        )
        if self.store:
            self.store.start_session(
                self.mode,
                self.session_id,
                self.daily_start_balance,
                notes=f"{self.mode.title()} session export for risk engine",
            )
        self.position_state_path = (
            os.path.join(os.path.dirname(save_path), f"{self.mode.lower()}_open_position.json")
            if save_path else None
        )
        self.pending_state_path = (
            os.path.join(os.path.dirname(save_path), f"{self.mode.lower()}_pending_entry.json")
            if save_path else None
        )
        self.kill_switch_state_path = (
            os.path.join(os.path.dirname(save_path), f"{self.session_id}_kill_switch.json")
            if save_path else None
        )
        self._load_open_position()
        self._load_pending_entry()
        self._load_kill_switch_state()
        self.startup_reconciliation_findings = []
        self._reconcile_startup_state()
        self._start_order_status_monitor()
        if self._initial_margin_error:
            self._log_event("WARN", "Could not fetch starting live margin", {"error": self._initial_margin_error})
        elif self.mode == "LIVE":
            self._log_event("INFO", "Starting balance set from Zerodha margin", {"balance": self.balance})

    def _build_store(self, save_path):
        if not save_path:
            return None
        store = TradingStore(save_path.replace(".xlsx", ".db"), mode=self.mode, settings=self.settings)
        if str(self.settings.get("async_sqlite_writes", "1")).lower() in ("0", "false", "no"):
            return store
        return AsyncTradingStore(
            store,
            max_queue_size=int(self.settings.get("sqlite_queue_size", 10000) or 10000),
        )

    def _sync_risk_state_from_guard(self):
        self.daily_start_balance = self.risk_guard.daily_start_balance
        self.consecutive_losses = self.risk_guard.consecutive_losses
        self.trading_blocked_reason = self.risk_guard.blocked_reason

    def activate_kill_switch(self, reason="Manual kill switch"):
        with self.state_lock:
            blocked_reason = self.risk_guard.activate_kill_switch(reason)
            self._sync_risk_state_from_guard()
            self._save_kill_switch_state()
            self._emit_alert(
                "CRITICAL",
                KILL_SWITCH_ACTIVATED,
                blocked_reason,
                {"reason": reason, "blocked_reason": blocked_reason},
            )
            self._log_lifecycle_event(
                KILL_SWITCH_ACTIVATED,
                "CRITICAL",
                blocked_reason,
                status="BLOCKED",
                payload={"reason": reason},
            )
            self._emit_session_history_event(
                action="KILL SWITCH",
                order_status="BLOCKED",
                exit_reason=blocked_reason,
                error_reason=reason,
            )
            return blocked_reason

    def _emit_session_history_event(self, action, order_status="", exit_reason="", error_reason=""):
        self._emit_live_log_update({
            "Session Trade No": "",
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Instrument / Symbol": "SESSION",
            "Option Type": "",
            "Action": action,
            "Order Type": "",
            "Quantity": "",
            "Order Status": order_status,
            "Entry Price": "",
            "Buy Score": "",
            "Exit Price": "",
            "Exit Reason": exit_reason,
            "Target Price": "",
            "Stop Loss Price": "",
            "LTP at Order Placement": "",
            "Zerodha Order ID": "",
            "Parent Order ID": "",
            "Related Trade ID": self.session_id,
            "Error / Rejection Reason": error_reason,
        })

    def _reconcile_startup_state(self):
        if self.mode != "LIVE" or not self.zerodha:
            return []
        reconciler = PositionReconciler(self.orders)
        findings = reconciler.reconcile(self.open_position, self.pending_entry)
        self.startup_reconciliation_findings = findings
        for finding in findings:
            level = finding.get("level", "WARN")
            event_type = RECONCILIATION_ERROR if level == "ERROR" else RECONCILIATION_WARNING
            self._log_lifecycle_event(
                event_type,
                level,
                f"Startup reconciliation: {finding.get('message', '')}",
                order_id=finding.get("order_id", ""),
                trade_no=finding.get("trade_no", ""),
                status=finding.get("status", ""),
                instrument=(finding.get("context") or {}).get("instrument", ""),
                payload=finding,
            )
        if findings:
            self._emit_live_log_update({
                "Session Trade No": "",
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Instrument / Symbol": "STARTUP RECONCILIATION",
                "Option Type": "",
                "Action": "RECONCILE",
                "Order Type": "",
                "Quantity": "",
                "Order Status": findings[0].get("status", ""),
                "Entry Price": "",
                "Buy Score": "",
                "Exit Price": "",
                "Exit Reason": findings[0].get("message", ""),
                "Target Price": "",
                "Stop Loss Price": "",
                "LTP at Order Placement": "",
                "Zerodha Order ID": findings[0].get("order_id", ""),
                "Parent Order ID": "",
                "Related Trade ID": findings[0].get("trade_no", ""),
                "Error / Rejection Reason": "; ".join(item.get("code", "") for item in findings),
            })
        error_codes = [finding.get("code", "") for finding in findings if finding.get("level") == "ERROR"]
        if error_codes:
            self._emit_alert(
                "ERROR",
                RECONCILIATION_ERROR,
                f"Startup reconciliation error: {', '.join(error_codes)}",
                {"error_codes": error_codes, "findings": findings},
            )
            self.activate_kill_switch(f"Startup reconciliation error: {', '.join(error_codes)}")
        return findings

    def _start_order_status_monitor(self):
        if self.mode != "LIVE" or not self.zerodha:
            return
        if self.order_monitor_thread and self.order_monitor_thread.is_alive():
            return
        self.order_monitor_stop.clear()
        self.order_monitor_thread = threading.Thread(
            target=self._order_status_monitor_loop,
            name=f"tradebot_order_status_monitor_{self.session_id}",
            daemon=True,
        )
        self.order_monitor_thread.start()

    def _order_status_monitor_loop(self):
        while not self.order_monitor_stop.wait(self.order_status_poll_interval):
            try:
                with self.state_lock:
                    self._poll_live_order_statuses()
            except Exception as exc:
                self._log_event("ERROR", "Order status monitor failed", {"error": str(exc)})

    def _poll_live_order_statuses(self):
        if self.mode != "LIVE" or not self.zerodha:
            return
        if self.pending_entry:
            self._check_pending_entry(self.pending_entry.get("placed_index", self.last_candle_index))
        if self.open_position:
            option = self.options[self.open_position["option_index"]]
            i = max(0, min(len(option) - 1, len(self.nifty) - 1))
            self._check_protective_exit_orders(i, force=True)

    def _interval_minutes(self, settings):
        text = str(settings.get("chart_interval", "3minute")).lower()
        if text in ("minute", "1minute", "1 min"):
            return 1
        for value in (2, 3, 5):
            if str(value) in text:
                return value
        return 3

    def _prepare_datetime_indexes(self):
        self.nifty = attach_datetime_index_map(self.nifty)
        self.options = [attach_datetime_index_map(option) for option in self.options]

    def on_ticks(self, ticks):
        if not ticks:
            return
        with self.state_lock:
            completed_any = False
            latest_tick_time = None
            for tick in ticks:
                token = int(tick.get("instrument_token", 0))
                price = tick.get("last_price")
                if token not in self.token_map or price is None:
                    continue
                now = tick.get("exchange_timestamp") or tick.get("last_trade_time") or datetime.now()
                latest_tick_time = now
                name = self.token_map[token]
                if self.open_position and str(name) == f"OPTION_{self.open_position['option_index']}":
                    self.latest_ltp_by_option[self.open_position["option_index"]] = float(price)
                    self._check_live_exit_price(float(price), now)
                    self._refresh_live_trade_snapshot(float(price), now)
                completed = self.candle_builder.add_tick(
                    name,
                    price,
                    timestamp=now,
                    volume=tick.get("volume_traded", 0) or 0
                )
                if completed:
                    completed_any = self._append_completed_candle(name, completed) or completed_any
            if latest_tick_time:
                for name, completed in self.candle_builder.flush_completed(latest_tick_time):
                    completed_any = self._append_completed_candle(name, completed) or completed_any

            if self.pending_entry:
                self._check_pending_entry(self.last_candle_index)
            if self.open_position and self._square_off_time_reached():
                self.square_off_open_position("AUTO SQUARE OFF")
            if completed_any:
                self._process_completed_candles()
                self._trim_live_candles_if_safe()

    def _append_completed_candle(self, name, row):
        if name == "NIFTY":
            if self._is_duplicate_or_old(self.nifty, row["datetime"]):
                return False
            self.nifty = append_clean_candle(self.nifty, row)
            append_datetime_index_key(self.nifty, row["datetime"])
            return True
        if str(name).startswith("OPTION_"):
            idx = int(str(name).split("_")[1])
            if self._is_duplicate_or_old(self.options[idx], row["datetime"]):
                return False
            attrs = dict(self.options[idx].attrs)
            option = append_clean_candle(self.options[idx], row)
            option = append_option_formula_row(option)
            option.attrs.update(attrs)
            append_datetime_index_key(option, row["datetime"])
            self.options[idx] = option
            return True
        return False

    def _is_duplicate_or_old(self, df, timestamp):
        if "datetime" not in df.columns or df.empty:
            return False
        latest_key = df.attrs.get("last_datetime_key")
        current_key = timestamp_key(timestamp)
        if latest_key is not None and current_key is not None:
            return current_key <= latest_key
        latest = pd.to_datetime(df["datetime"], errors="coerce").max()
        current = pd.to_datetime(timestamp, errors="coerce")
        return not pd.isna(latest) and not pd.isna(current) and current <= latest

    def _trim_live_candles_if_safe(self):
        limit = self.live_candle_memory_limit
        if limit <= 0 or self.open_position or self.pending_entry:
            return False
        old_nifty_len = len(self.nifty)
        self.nifty = self._trim_candle_frame(self.nifty, limit)
        self.options = [self._trim_candle_frame(option, limit) for option in self.options]
        dropped = max(0, old_nifty_len - len(self.nifty))
        if dropped:
            self.last_candle_index = max(-1, self.last_candle_index - dropped)
            self.engine.cooldown_until = max(-1, self.engine.cooldown_until - dropped)
        return bool(dropped)

    def _trim_candle_frame(self, df, limit):
        if df is None or len(df) <= limit:
            return df
        attrs = dict(df.attrs)
        trimmed = df.iloc[-limit:].reset_index(drop=True)
        trimmed.attrs.update(attrs)
        return attach_datetime_index_map(trimmed)

    def _process_completed_candles(self):
        i = min(len(self.nifty), *[len(o) for o in self.options]) - 1
        if i <= 6:
            return
        if self.open_position:
            self._check_live_exit(i)
        if (
            self.open_position is None
            and self.pending_entry is None
            and self.trade_count < self.max_trades
            and i > self.last_candle_index
        ):
            self.last_candle_index = i
            if self._trading_blocked():
                self._log_event("WARN", self.trading_blocked_reason)
                return
            self._try_entry(i)
            self._log_candle(i)

    def _resolve_quantity(self, signal):
        tradingsymbol = signal.get("tradingsymbol") or signal.get("instrument")
        contract_lot_size = self.orders.lot_size(tradingsymbol)
        return self.lots * contract_lot_size, contract_lot_size

    def _place_order(self, side, signal, qty, order_type="MARKET", price=None, trigger_price=None):
        tradingsymbol = signal.get("tradingsymbol") or signal.get("instrument")
        product = self._order_product()
        idempotency_key = self._order_idempotency_key(
            side,
            signal,
            qty,
            order_type=order_type,
            price=price,
            trigger_price=trigger_price,
            product=product,
        )
        existing = self.order_idempotency_records.get(idempotency_key)
        if existing:
            self.duplicate_order_suppressed += 1
            self._log_event(
                "WARN",
                "Duplicate order placement suppressed",
                {
                    "idempotency_key": idempotency_key,
                    "order_id": existing["order_id"],
                    "status": existing["status"],
                },
            )
            return existing["status"], existing["order_id"]
        if idempotency_key in self.order_idempotency_in_progress:
            self.duplicate_order_suppressed += 1
            self._log_event("WARN", "Duplicate order placement already in progress", {"idempotency_key": idempotency_key})
            return "FAILED: DUPLICATE ORDER IN PROGRESS", ""

        self.order_idempotency_in_progress.add(idempotency_key)
        try:
            result = self.orders.place_order(
                side,
                tradingsymbol,
                qty,
                product=product,
                order_type=order_type,
                price=price,
                trigger_price=trigger_price,
            )
        finally:
            self.order_idempotency_in_progress.discard(idempotency_key)
        if not str(result.get("status", "")).startswith("FAILED"):
            self.order_idempotency_records[idempotency_key] = {
                "status": result["status"],
                "order_id": result["order_id"],
            }
        if result["log_status"]:
            self._log_order(result["order_id"], side, result["log_status"], result["log_data"])
        if result["error"]:
            if result.get("requires_reconciliation", False):
                self._emit_alert(
                    "ERROR",
                    "ORDER_UNKNOWN_BROKER_STATE",
                    f"{side} order failed with unknown broker state",
                    {
                        "error": result["error"],
                        "order_type": order_type,
                        "error_class": result.get("error_class", ""),
                        "retriable": result.get("retriable", False),
                        "requires_reconciliation": True,
                        "idempotency_key": idempotency_key,
                        "tradingsymbol": tradingsymbol,
                        "quantity": qty,
                    },
                )
            self._log_event(
                "ERROR",
                f"{side} order failed",
                {
                    "error": result["error"],
                    "order_type": order_type,
                    "error_class": result.get("error_class", ""),
                    "retriable": result.get("retriable", False),
                    "requires_reconciliation": result.get("requires_reconciliation", False),
                    "idempotency_key": idempotency_key,
                },
            )
        return result["status"], result["order_id"]

    def _order_idempotency_key(self, side, signal, qty, order_type="MARKET", price=None, trigger_price=None, product=""):
        signal = signal or {}
        tradingsymbol = signal.get("tradingsymbol") or signal.get("instrument")
        side = str(side or "").upper()
        order_type = str(order_type or "MARKET").upper()
        if side == "BUY":
            intent = f"entry:{signal.get('nifty_signal_index', '')}:{signal.get('signal_index', '')}:{signal.get('entry_index', '')}"
        else:
            trade_no = self.open_position.get("trade_no", "") if self.open_position else signal.get("trade_no", "")
            intent = f"exit:{trade_no}"
        return "|".join([
            str(self.session_id),
            intent,
            side,
            str(tradingsymbol or ""),
            str(int(qty or 0)),
            str(product or ""),
            order_type,
            self._idempotency_price(price),
            self._idempotency_price(trigger_price),
        ])

    def _idempotency_price(self, value):
        if value in ("", None):
            return ""
        try:
            return f"{float(value):.4f}"
        except (TypeError, ValueError):
            return str(value)

    def _actual_order_price(self, order_id, fallback):
        return self.orders.average_price(order_id, fallback)

    def _actual_order_quantity(self, order_id, fallback):
        return self.orders.filled_quantity(order_id, fallback)

    def _price_tick(self):
        return float(self.settings.get("price_tick", 0.05) or 0.05)

    def _round_price(self, price):
        tick = self._price_tick()
        return round(round(float(price) / tick) * tick, 2)

    def _order_product(self):
        product = str(self.settings.get("order_product", "NRML") or "NRML").strip().upper()
        if product in ("MIS", "INTRADAY"):
            return "MIS"
        return "NRML"

    def _try_entry(self, i):
        if self.order_transition_in_progress or self.open_position or self.pending_entry:
            return
        signal = self.engine.find_trade(self.nifty, self.options, i, self.settings)
        if signal is None:
            return
        validation_error = self._validate_entry(signal, i)
        if validation_error:
            self._record_rejected_entry(signal, i, validation_error)
            return
        qty, lot_size = self._resolve_quantity(signal)
        margin_error = self._validate_margin(signal, qty)
        if margin_error:
            self._record_rejected_entry(signal, i, margin_error)
            return
        entry_offset = float(signal.get("entry_offset", 0) or 0)
        if entry_offset != 0:
            self._place_pending_limit_entry(signal, i, qty, lot_size)
            return

        self.order_transition_in_progress = True
        try:
            entry_status, entry_order_id = self._place_order("BUY", signal, qty, order_type="MARKET")
            if entry_status.startswith("FAILED"):
                self._record_rejected_entry(signal, i, entry_status)
                return
            entry_price = self._actual_order_price(entry_order_id, signal["entry"])
            filled_qty = self._actual_order_quantity(entry_order_id, qty)
            self._open_position_from_fill(signal, lot_size, entry_order_id, entry_price, filled_qty)
        finally:
            self.order_transition_in_progress = False

    def _place_pending_limit_entry(self, signal, i, qty, lot_size):
        if self.order_transition_in_progress or self.open_position or self.pending_entry:
            return
        if self.mode != "LIVE":
            option = signal["option"]
            entry_index = signal["entry_index"]
            if entry_index >= len(option):
                self._record_rejected_entry(signal, i, "ENTRY CANCELLED: TIME EXHAUSTION CANCELLATION")
                return
            row = option.iloc[entry_index]
            limit_price = float(signal["entry"])
            if float(row.get("low", limit_price + 1)) <= limit_price:
                self._open_position_from_fill(signal, lot_size, "", limit_price, qty)
            else:
                self._record_rejected_entry(signal, i, "TIME EXHAUSTION CANCELLATION")
            return

        self.order_transition_in_progress = True
        try:
            entry_status, entry_order_id = self._place_order(
                "BUY",
                signal,
                qty,
                order_type="LIMIT",
                price=signal["entry"]
            )
            if entry_status.startswith("FAILED"):
                self._record_rejected_entry(signal, i, entry_status)
                return

            self.pending_entry = {
                "signal": signal,
                "option_index": signal["option_index"],
                "order_id": entry_order_id,
                "quantity": qty,
                "contract_lot_size": lot_size,
                "limit_price": signal["entry"],
                "placed_at": datetime.now(),
                "placed_index": i,
            }
            self._save_pending_entry()
            self._record_pending_entry_order(signal, i, entry_status, entry_order_id, qty, lot_size)
            timer = threading.Timer(60, self._expire_pending_entry_order)
            timer.daemon = True
            self.pending_entry["timer"] = timer
            timer.start()
        finally:
            self.order_transition_in_progress = False

    def _expire_pending_entry_order(self):
        pending = self.pending_entry
        if not pending:
            return
        self._check_pending_entry(pending.get("placed_index", 0), force_timeout=True)

    def _check_pending_entry(self, i, force_timeout=False):
        if self.order_transition_in_progress and not force_timeout:
            return
        pending = self.pending_entry
        if not pending:
            return
        status = self.orders.order_status(pending["order_id"], fallback="UNKNOWN")
        details = self.orders.order_details(
            pending["order_id"],
            fallback_quantity=pending.get("quantity", 0),
            fallback_price=pending.get("limit_price", 0),
        )
        self._record_order_status_change(
            pending["order_id"],
            status,
            pending["signal"],
            "ENTRY ORDER STATUS",
            entry_order_id=pending["order_id"],
            quantity=pending.get("quantity", ""),
            lot_size=pending.get("contract_lot_size", ""),
            entry=pending.get("limit_price", ""),
        )
        if self._handle_partial_pending_entry(pending, details, i, status):
            return
        if status in {"COMPLETE", "FILLED"}:
            entry_price = details.get("average_price") or self._actual_order_price(pending["order_id"], pending["limit_price"])
            filled_qty = details.get("filled_quantity") or self._actual_order_quantity(pending["order_id"], pending["quantity"])
            self._open_position_from_fill(
                pending["signal"],
                pending["contract_lot_size"],
                pending["order_id"],
                entry_price,
                filled_qty
            )
            self._cancel_pending_timer(pending)
            self.pending_entry = None
            self._clear_pending_entry()
            return
        if status in {"CANCELLED", "REJECTED"}:
            signal = dict(pending["signal"])
            signal["entry_order_id"] = pending["order_id"]
            self._record_rejected_entry(signal, i, f"ENTRY {status}")
            self._cancel_pending_timer(pending)
            self.pending_entry = None
            self._clear_pending_entry()
            return

        elapsed = (datetime.now() - pending["placed_at"]).total_seconds()
        if elapsed < 60 and not force_timeout:
            return

        cancel_status = "TIME EXHAUSTION CANCELLATION"
        if self.mode == "LIVE" and self.zerodha:
            cancelled = self.orders.cancel_order(pending["order_id"])
            if cancelled["cancelled"]:
                self._log_order(pending["order_id"], "BUY", "CANCELLED", {"reason": cancel_status})
                self._emit_order_event(
                    pending["signal"],
                    self.trade_count + 1,
                    "BUY",
                    "CANCELLED",
                    order_id=pending["order_id"],
                    order_type="LIMIT",
                    quantity=pending.get("quantity", ""),
                    limit_price=pending.get("limit_price", ""),
                    remarks=cancel_status,
                )
            else:
                cancel_status = f"TIME EXHAUSTION CANCELLATION: CANCEL FAILED {cancelled['error']}"
                self._log_event("ERROR", cancel_status, {"order_id": pending["order_id"]})
        signal = dict(pending["signal"])
        signal["entry_order_id"] = pending["order_id"]
        self._record_rejected_entry(signal, i, cancel_status)
        self._cancel_pending_timer(pending)
        self.pending_entry = None
        self._clear_pending_entry()

    def _cancel_pending_timer(self, pending):
        timer = pending.get("timer") if pending else None
        if timer:
            timer.cancel()

    def _handle_partial_pending_entry(self, pending, details, i, status):
        filled_qty = int(details.get("filled_quantity") or 0)
        requested_qty = int(details.get("quantity") or pending.get("quantity") or 0)
        pending_qty = int(details.get("pending_quantity") or 0)
        if filled_qty <= 0 or filled_qty >= requested_qty:
            return False

        order_id = pending["order_id"]
        cancel_reason = (
            f"PARTIAL ENTRY FILL: filled {filled_qty} of {requested_qty}; "
            "remaining quantity cancelled before opening protected position"
        )
        self._emit_alert(
            "WARN",
            ORDER_PARTIAL_FILL,
            cancel_reason,
            {
                "order_id": order_id,
                "status": status,
                "filled_quantity": filled_qty,
                "requested_quantity": requested_qty,
                "pending_quantity": pending_qty,
                "instrument": pending["signal"].get("instrument", ""),
            },
        )
        if status not in {"CANCELLED", "REJECTED"} and pending_qty > 0:
            cancelled = self.orders.cancel_order(order_id)
            if not cancelled["cancelled"]:
                self.activate_kill_switch(
                    f"Partial entry fill detected but remaining order cancel failed: {cancelled['error']}"
                )
                return True
            self._log_order(order_id, "BUY", "PARTIAL ENTRY REMAINDER CANCELLED", {"reason": cancel_reason})
            self._emit_order_event(
                pending["signal"],
                self.trade_count + 1,
                "BUY",
                "CANCELLED",
                order_id=order_id,
                order_type="LIMIT",
                quantity=requested_qty,
                entry_price=details.get("average_price") or pending.get("limit_price", ""),
                limit_price=pending.get("limit_price", ""),
                remarks=cancel_reason,
            )

        self._open_position_from_fill(
            pending["signal"],
            pending["contract_lot_size"],
            order_id,
            details.get("average_price") or pending["limit_price"],
            filled_qty,
        )
        self._log_lifecycle_event(
            ORDER_PARTIAL_FILL,
            "WARN",
            "Partial entry fill converted to protected position",
            order_id=order_id,
            trade_no=self.open_position.get("trade_no", "") if self.open_position else "",
            status=status,
            side="BUY",
            instrument=pending["signal"].get("instrument", ""),
            quantity=requested_qty,
            payload={
                "order_id": order_id,
                "requested_quantity": requested_qty,
                "filled_quantity": filled_qty,
                "pending_quantity": pending_qty,
            },
        )
        self._cancel_pending_timer(pending)
        self.pending_entry = None
        self._clear_pending_entry()
        return True

    def _open_position_from_fill(self, signal, lot_size, entry_order_id, entry_price, filled_qty):
        target = self._round_price(entry_price + float(self.settings["profit_points"]))
        stoploss = self._round_price(max(entry_price - float(self.settings["safety_points"]), self._price_tick()))
        self.active_orders = {}
        self.open_position = {
            "trade_no": self.trade_count + 1,
            "signal": signal,
            "option_index": signal["option_index"],
            "entry_time": self._row_time(signal["option"], signal["entry_index"]),
            "entry_index": signal["entry_index"],
            "entry_price": entry_price,
            "target": target,
            "stoploss": stoploss,
            "quantity": filled_qty,
            "contract_lot_size": lot_size,
            "entry_order_id": entry_order_id,
            "target_order_id": "",
            "stoploss_order_id": "",
            "exit_order_setup_error": "",
            "peak_price": entry_price,
        }
        self._log_lifecycle_event(
            ENTRY_FILLED,
            "INFO",
            "Entry order filled and position opened",
            order_id=entry_order_id,
            trade_no=self.open_position["trade_no"],
            status="COMPLETE",
            side="BUY",
            instrument=signal.get("instrument", ""),
            quantity=filled_qty,
            payload={
                "entry_price": entry_price,
                "target_price": target,
                "stoploss_price": stoploss,
                "contract_lot_size": lot_size,
                "entry_order_type": "LIMIT" if signal.get("entry_offset", 0) else "MARKET",
            },
        )
        self._emit_order_event(
            signal,
            self.open_position["trade_no"],
            "BUY",
            "COMPLETE" if self.mode != "LIVE" else "COMPLETE",
            order_id=entry_order_id,
            order_type="LIMIT" if signal.get("entry_offset", 0) else "MARKET",
            quantity=filled_qty,
            entry_price=entry_price,
            limit_price=signal.get("entry", "") if signal.get("entry_offset", 0) else "",
            target_price=target,
            stoploss_price=stoploss,
            remarks="BUY filled",
        )
        self._refresh_live_trade_snapshot()
        self._place_protective_exit_orders()
        self._save_open_position()

    def _place_protective_exit_orders(self):
        position = self.open_position
        if self.mode != "LIVE" or not self.zerodha or not position:
            return
        if position.get("target_order_id") or position.get("stoploss_order_id"):
            return

        errors = []
        target_status, target_order_id = self._place_order(
            "SELL",
            position["signal"],
            position["quantity"],
            order_type="LIMIT",
            price=position["target"],
        )
        if target_status.startswith("FAILED"):
            errors.append(f"target order failed: {target_status}")
        else:
            position["target_order_id"] = target_order_id
            self._record_exit_order_placed(
                position,
                "TARGET SELL LIMIT PLACED",
                target_order_id,
                position["target"],
                "",
                "SELL LIMIT",
            )

        stop_status, stoploss_order_id = self._place_order(
            "SELL",
            position["signal"],
            position["quantity"],
            order_type="SL-M",
            trigger_price=position["stoploss"],
        )
        if stop_status.startswith("FAILED"):
            errors.append(f"stoploss order failed: {stop_status}")
        else:
            position["stoploss_order_id"] = stoploss_order_id
            self._record_exit_order_placed(
                position,
                "STOPLOSS SELL SL-M PLACED",
                stoploss_order_id,
                "",
                position["stoploss"],
                "SELL SL-M",
            )

        position["exit_order_setup_error"] = "; ".join(errors)
        if errors:
            self._log_event(
                "ERROR",
                "Protective exit order setup incomplete",
                {
                    "entry_order_id": position.get("entry_order_id", ""),
                    "target_order_id": position.get("target_order_id", ""),
                    "stoploss_order_id": position.get("stoploss_order_id", ""),
                    "errors": errors,
                },
            )

    def _validate_entry(self, signal, i):
        if i >= len(self.nifty):
            return "ENTRY REJECTED: NIFTY candle missing"
        option_index = signal.get("option_index")
        if option_index is None or option_index >= len(self.options):
            return "ENTRY REJECTED: option data missing"
        if signal["entry_index"] >= len(signal["option"]):
            return "ENTRY REJECTED: option entry candle missing"
        if self.mode == "LIVE":
            now = datetime.now().time()
            if now < datetime.strptime("09:15", "%H:%M").time() or now > datetime.strptime("15:30", "%H:%M").time():
                return "ENTRY REJECTED: outside market hours"
        return ""

    def _validate_margin(self, signal, qty):
        if self.mode != "LIVE" or not self.zerodha:
            return ""
        if str(self.settings.get("check_margin", "1")).lower() in ("0", "false", "no"):
            return ""
        try:
            available = self.orders.available_margin()
        except Exception as exc:
            self._log_event("WARN", "Margin check failed", {"error": str(exc)})
            return ""
        if available is None:
            return ""
        required = float(signal["entry"]) * int(qty)
        if available < required:
            return f"ENTRY REJECTED: insufficient margin available={available:.2f} required={required:.2f}"
        return ""

    def _check_live_exit(self, i):
        position = self.open_position
        if self._check_protective_exit_orders(i):
            return
        option = self.options[position["option_index"]]
        if i >= len(option):
            return
        current = float(option.iloc[i]["close"])
        position["peak_price"] = max(position["peak_price"], current)
        elapsed = i - position["entry_index"]
        reason = None
        has_target_order = self.mode == "LIVE" and bool(position.get("target_order_id"))
        has_stoploss_order = self.mode == "LIVE" and bool(position.get("stoploss_order_id"))
        if current >= position["target"] and not has_target_order:
            reason = "TARGET"
        elif current <= position["stoploss"] and not has_stoploss_order:
            reason = "STOPLOSS"
        else:
            if elapsed >= int(self.settings.get("time_exit", 10)):
                reason = reason or "TIME EXIT"

        if reason is None:
            return
        self._close_position(i, reason, current)

    def _check_live_exit_price(self, current, timestamp):
        position = self.open_position
        if not position:
            return
        if self._check_protective_exit_orders(len(position["signal"]["option"]) - 1, timestamp):
            return
        position["peak_price"] = max(position.get("peak_price", position["entry_price"]), current)
        has_target_order = self.mode == "LIVE" and bool(position.get("target_order_id"))
        has_stoploss_order = self.mode == "LIVE" and bool(position.get("stoploss_order_id"))
        if current >= position["target"] and not has_target_order:
            self._close_position(len(position["signal"]["option"]) - 1, "TARGET", current, timestamp)
        elif current <= position["stoploss"] and not has_stoploss_order:
            self._close_position(len(position["signal"]["option"]) - 1, "STOPLOSS", current, timestamp)

    def _check_protective_exit_orders(self, i, exit_time_override=None, force=False):
        position = self.open_position
        if self.mode != "LIVE" or not self.zerodha or not position:
            return False

        now = time.monotonic()
        last_checked = float(position.get("last_exit_order_check_at", 0) or 0)
        if not force and now - last_checked < 0.5:
            return False
        position["last_exit_order_check_at"] = now

        target_order_id = position.get("target_order_id")
        stoploss_order_id = position.get("stoploss_order_id")
        target_status = self.orders.order_status(target_order_id, fallback="") if target_order_id else ""
        stoploss_status = self.orders.order_status(stoploss_order_id, fallback="") if stoploss_order_id else ""
        target_details = (
            self.orders.order_details(
                target_order_id,
                fallback_quantity=position.get("quantity", 0),
                fallback_price=position.get("target", 0),
            )
            if target_order_id
            else None
        )
        stoploss_details = (
            self.orders.order_details(
                stoploss_order_id,
                fallback_quantity=position.get("quantity", 0),
                fallback_price=position.get("stoploss", 0),
            )
            if stoploss_order_id
            else None
        )

        self._record_order_status_change(
            target_order_id,
            target_status,
            position["signal"],
            "TARGET ORDER STATUS",
            entry_order_id=position.get("entry_order_id", ""),
            exit_order_id=target_order_id,
            quantity=position.get("quantity", ""),
            lot_size=position.get("contract_lot_size", ""),
            entry=position.get("entry_price", ""),
            exit_price=position.get("target", ""),
        )
        self._record_order_status_change(
            stoploss_order_id,
            stoploss_status,
            position["signal"],
            "STOPLOSS ORDER STATUS",
            entry_order_id=position.get("entry_order_id", ""),
            exit_order_id=stoploss_order_id,
            quantity=position.get("quantity", ""),
            lot_size=position.get("contract_lot_size", ""),
            entry=position.get("entry_price", ""),
            exit_price=position.get("stoploss", ""),
        )

        if self._protect_against_partial_exit(position, "TARGET", target_order_id, target_status, target_details):
            return True
        if self._protect_against_partial_exit(position, "STOPLOSS", stoploss_order_id, stoploss_status, stoploss_details):
            return True

        changed = False
        if target_order_id and target_status in {"CANCELLED", "REJECTED"}:
            position["target_order_id"] = ""
            changed = True
        if stoploss_order_id and stoploss_status in {"CANCELLED", "REJECTED"}:
            position["stoploss_order_id"] = ""
            changed = True
        if changed:
            self._save_open_position()

        if target_status in {"COMPLETE", "FILLED"}:
            self._cancel_exit_order(stoploss_order_id, "STOPLOSS", "TARGET FILLED")
            self._finalize_position_from_exit_order(
                i,
                "TARGET",
                target_order_id,
                target_status,
                position["target"],
                exit_time_override,
            )
            return True

        if stoploss_status in {"COMPLETE", "FILLED"}:
            self._cancel_exit_order(target_order_id, "TARGET", "STOPLOSS FILLED")
            self._finalize_position_from_exit_order(
                i,
                "STOPLOSS",
                stoploss_order_id,
                stoploss_status,
                position["stoploss"],
                exit_time_override,
            )
            return True

        return False

    def _protect_against_partial_exit(self, position, label, order_id, status, details):
        if not order_id or not details:
            return False
        requested_qty = int(position.get("quantity") or details.get("quantity") or 0)
        filled_qty = int(details.get("filled_quantity") or 0)
        if filled_qty <= 0 or requested_qty <= 0 or filled_qty >= requested_qty:
            return False

        guard_key = f"{label}:{order_id}"
        if position.get("partial_exit_guarded_order_id") == guard_key:
            return True
        position["partial_exit_guarded_order_id"] = guard_key
        self._save_open_position()

        reason = (
            f"PARTIAL {label} EXIT FILL DETECTED: filled {filled_qty} of {requested_qty}. "
            "New entries disabled; manual review required."
        )
        self._emit_alert(
            "CRITICAL",
            PARTIAL_EXIT_DETECTED,
            reason,
            {
                "order_id": order_id,
                "status": status,
                "label": label,
                "filled_quantity": filled_qty,
                "requested_quantity": requested_qty,
                "trade_no": position.get("trade_no", ""),
            },
        )
        self._log_lifecycle_event(
            PARTIAL_EXIT_DETECTED,
            "CRITICAL",
            reason,
            order_id=order_id,
            trade_no=position.get("trade_no", ""),
            status=status,
            side="SELL",
            instrument=position.get("signal", {}).get("instrument", ""),
            quantity=requested_qty,
            payload={
                "order_id": order_id,
                "status": status,
                "label": label,
                "filled_quantity": filled_qty,
                "requested_quantity": requested_qty,
                "details": details,
            },
        )
        self._emit_order_event(
            position["signal"],
            position.get("trade_no", self.trade_count + 1),
            "SELL",
            status,
            order_id=order_id,
            order_type="LIMIT" if label == "TARGET" else "SL-M",
            quantity=requested_qty,
            entry_price=position.get("entry_price", ""),
            exit_price=details.get("average_price") or (position.get("target", "") if label == "TARGET" else position.get("stoploss", "")),
            limit_price=position.get("target", "") if label == "TARGET" else "",
            trigger_price=position.get("stoploss", "") if label == "STOPLOSS" else "",
            target_price=position.get("target", ""),
            stoploss_price=position.get("stoploss", ""),
            exit_reason=reason,
            remarks=reason,
            parent_order_id=position.get("entry_order_id", ""),
        )
        self.activate_kill_switch(reason)
        return True

    def _cancel_exit_order(self, order_id, label, reason):
        if self.mode != "LIVE" or not self.zerodha or not order_id:
            return
        status = self.orders.order_status(order_id, fallback="UNKNOWN")
        if status in {"COMPLETE", "FILLED", "CANCELLED", "REJECTED"}:
            return
        cancelled = self.orders.cancel_order(order_id)
        if cancelled["cancelled"]:
            self._log_order(order_id, "SELL", f"{label} CANCELLED", {"reason": reason})
            if self.open_position:
                position = self.open_position
                self._emit_order_event(
                    position["signal"],
                    position.get("trade_no", self.trade_count + 1),
                    "SELL",
                    "CANCELLED",
                    order_id=order_id,
                    order_type="LIMIT" if label == "TARGET" else "SL-M",
                    quantity=position.get("quantity", ""),
                    entry_price=position.get("entry_price", ""),
                    limit_price=position.get("target", "") if label == "TARGET" else "",
                    trigger_price=position.get("stoploss", "") if label == "STOPLOSS" else "",
                    target_price=position.get("target", ""),
                    stoploss_price=position.get("stoploss", ""),
                    exit_reason=reason,
                    remarks=f"{label} order cancelled",
                    parent_order_id=position.get("entry_order_id", ""),
                )
        else:
            self._log_event("ERROR", f"{label} cancel failed", {"order_id": order_id, "error": cancelled["error"]})

    def square_off_open_position(self, reason="MANUAL SQUARE OFF"):
        with self.state_lock:
            if not self.open_position:
                return None
            position = self.open_position
            option = self.options[position["option_index"]]
            i = min(len(option) - 1, len(self.nifty) - 1)
            current = float(option.iloc[i]["close"])
            return self._close_position(i, reason, current)

    def _close_position(self, i, reason, fallback_exit_price, exit_time_override=None):
        if self.order_transition_in_progress:
            return None
        position = self.open_position
        if not position:
            return None
        self.order_transition_in_progress = True
        try:
            if self._check_protective_exit_orders(i, exit_time_override, force=True):
                return None
            self._cancel_exit_order(position.get("target_order_id"), "TARGET", reason)
            self._cancel_exit_order(position.get("stoploss_order_id"), "STOPLOSS", reason)
            if self._check_protective_exit_orders(i, exit_time_override, force=True):
                return None
            exit_status, exit_order_id = self._place_order("SELL", position["signal"], position["quantity"])
            if exit_status.startswith("FAILED"):
                if position.get("last_exit_failure_status") != exit_status:
                    position["last_exit_failure_status"] = exit_status
                    self._save_open_position()
                    self._record_exit_failure(position, i, reason, exit_status)
                return None
            exit_price = self._actual_order_price(exit_order_id, fallback_exit_price)
            exit_quantity = self._actual_order_quantity(exit_order_id, position["quantity"])
        finally:
            self.order_transition_in_progress = False
        pnl = (exit_price - position["entry_price"]) * exit_quantity
        self.balance += pnl
        self.risk_guard.record_trade_result(pnl)
        self._sync_risk_state_from_guard()
        trade = {
            "Trade No": position["trade_no"],
            "Type": position["signal"]["type"],
            "Instrument": position["signal"]["instrument"],
            "Strike": position["signal"].get("strike", ""),
            "Expiry": position["signal"].get("expiry", ""),
            "Entry Time": position["entry_time"],
            "Entry": position["entry_price"],
            "Entry Offset": position["signal"].get("entry_offset", ""),
            "Exit Time": format_datetime_value(exit_time_override) if exit_time_override else self._row_time(position["signal"]["option"], i),
            "Exit": exit_price,
            "PnL": pnl,
            "Live PnL": pnl,
            "Final PnL": pnl,
            "Total PnL": self.balance,
            "Quantity": exit_quantity,
            "Contract Lot Size": position["contract_lot_size"],
            "Reason": reason,
            "Remarks": reason,
            "Order Status": exit_status if self.mode == "LIVE" else "PAPER",
            "Zerodha Order Status": exit_status if self.mode == "LIVE" else "PAPER",
            "Entry Order ID": position["entry_order_id"],
            "Exit Order ID": exit_order_id,
            "Buy Score": position["signal"].get("score_row", {}).get("Buy Score", ""),
            "Buy Entry": position["signal"].get("score_row", {}).get("Buy Entry", ""),
        }
        self.trades.append(trade)
        self.trade_count += 1
        self.engine.mark_trade_complete(i)
        self.open_position = None
        self._clear_open_position()
        self._append_excel(self.save_path, [trade])
        self._log_trade(trade)
        self._emit_order_event(
            position["signal"],
            position["trade_no"],
            "SELL",
            exit_status,
            order_id=exit_order_id,
            order_type="MARKET",
            quantity=exit_quantity,
            entry_price=position["entry_price"],
            exit_price=exit_price,
            target_price=position.get("target", ""),
            stoploss_price=position.get("stoploss", ""),
            exit_reason=reason,
            remarks=reason,
            parent_order_id=position.get("entry_order_id", ""),
        )
        if self.on_trade:
            self.on_trade(trade, self.balance)
        self._refresh_live_trade_snapshot(final_trade=trade)
        return trade

    def _finalize_position_from_exit_order(self, i, reason, exit_order_id, exit_status, fallback_exit_price, exit_time_override=None):
        position = self.open_position
        if not position:
            return None
        exit_price = self._actual_order_price(exit_order_id, fallback_exit_price)
        exit_quantity = self._actual_order_quantity(exit_order_id, position["quantity"])
        return self._finalize_closed_position(
            i,
            reason,
            exit_price,
            exit_quantity,
            exit_status,
            exit_order_id,
            exit_time_override,
        )

    def _finalize_closed_position(self, i, reason, exit_price, exit_quantity, exit_status, exit_order_id, exit_time_override=None):
        position = self.open_position
        if not position:
            return None
        pnl = (exit_price - position["entry_price"]) * exit_quantity
        self.balance += pnl
        self.risk_guard.record_trade_result(pnl)
        self._sync_risk_state_from_guard()
        trade = {
            "Trade No": position["trade_no"],
            "Type": position["signal"]["type"],
            "Instrument": position["signal"]["instrument"],
            "Strike": position["signal"].get("strike", ""),
            "Expiry": position["signal"].get("expiry", ""),
            "Entry Time": position["entry_time"],
            "Entry": position["entry_price"],
            "Entry Offset": position["signal"].get("entry_offset", ""),
            "Exit Time": format_datetime_value(exit_time_override) if exit_time_override else self._row_time(position["signal"]["option"], i),
            "Exit": exit_price,
            "PnL": pnl,
            "Live PnL": pnl,
            "Final PnL": pnl,
            "Total PnL": self.balance,
            "Quantity": exit_quantity,
            "Contract Lot Size": position["contract_lot_size"],
            "Reason": reason,
            "Remarks": reason,
            "Order Status": exit_status if self.mode == "LIVE" else "PAPER",
            "Zerodha Order Status": exit_status if self.mode == "LIVE" else "PAPER",
            "Entry Order ID": position["entry_order_id"],
            "Exit Order ID": exit_order_id,
            "Buy Score": position["signal"].get("score_row", {}).get("Buy Score", ""),
            "Buy Entry": position["signal"].get("score_row", {}).get("Buy Entry", ""),
        }
        self.trades.append(trade)
        self.trade_count += 1
        self.engine.mark_trade_complete(i)
        self.open_position = None
        self._clear_open_position()
        self._append_excel(self.save_path, [trade])
        self._log_trade(trade)
        self._emit_order_event(
            position["signal"],
            position["trade_no"],
            "SELL",
            exit_status,
            order_id=exit_order_id,
            order_type="LIMIT" if reason == "TARGET" else ("SL-M" if reason == "STOPLOSS" else "MARKET"),
            quantity=exit_quantity,
            entry_price=position["entry_price"],
            exit_price=exit_price,
            limit_price=position.get("target", "") if reason == "TARGET" else "",
            trigger_price=position.get("stoploss", "") if reason == "STOPLOSS" else "",
            target_price=position.get("target", ""),
            stoploss_price=position.get("stoploss", ""),
            exit_reason=reason,
            remarks=reason,
            parent_order_id=position.get("entry_order_id", ""),
        )
        if self.on_trade:
            self.on_trade(trade, self.balance)
        self._refresh_live_trade_snapshot(final_trade=trade)
        return trade

    def _record_exit_failure(self, position, i, reason, status):
        trade = {
            "Trade No": position["trade_no"],
            "Type": position["signal"].get("type", ""),
            "Instrument": position["signal"].get("instrument", ""),
            "Strike": position["signal"].get("strike", ""),
            "Expiry": position["signal"].get("expiry", ""),
            "Entry Time": position.get("entry_time", ""),
            "Entry": position.get("entry_price", ""),
            "Entry Offset": position["signal"].get("entry_offset", ""),
            "Exit Time": self._row_time(position["signal"]["option"], i),
            "Exit": "",
            "PnL": 0,
            "Live PnL": 0,
            "Final PnL": 0,
            "Total PnL": self.balance,
            "Quantity": position.get("quantity", ""),
            "Contract Lot Size": position.get("contract_lot_size", ""),
            "Reason": f"EXIT FAILED: {reason}",
            "Remarks": status,
            "Order Status": status,
            "Zerodha Order Status": status,
            "Entry Order ID": position.get("entry_order_id", ""),
            "Exit Order ID": "",
            "Buy Score": position["signal"].get("score_row", {}).get("Buy Score", ""),
            "Buy Entry": position["signal"].get("score_row", {}).get("Buy Entry", ""),
        }
        self._append_excel(self.save_path, [trade])
        self._log_trade(trade)
        self._emit_order_event(
            position["signal"],
            position["trade_no"],
            "SELL",
            status,
            order_id="",
            order_type="MARKET",
            quantity=position.get("quantity", ""),
            entry_price=position.get("entry_price", ""),
            exit_price="",
            exit_reason=f"EXIT FAILED: {reason}",
            remarks=status,
            parent_order_id=position.get("entry_order_id", ""),
        )
        if self.on_trade:
            self.on_trade(trade, self.balance)

    def _record_pending_entry_order(self, signal, i, status, order_id, qty, lot_size):
        zerodha_status = self._zerodha_order_status(order_id, fallback="OPEN")
        self.last_order_status_by_id[str(order_id)] = zerodha_status
        trade = {
            "Trade No": self.trade_count + 1,
            "Type": signal.get("type", ""),
            "Instrument": signal.get("instrument", ""),
            "Strike": signal.get("strike", ""),
            "Expiry": signal.get("expiry", ""),
            "Entry Time": self._row_time(self.nifty, i),
            "Entry": signal.get("entry", ""),
            "Entry Offset": signal.get("entry_offset", ""),
            "Exit Time": "",
            "Exit": "",
            "PnL": 0,
            "Live PnL": 0,
            "Final PnL": 0,
            "Total PnL": self.balance,
            "Quantity": qty,
            "Contract Lot Size": lot_size,
            "Reason": "ENTRY ORDER PLACED",
            "Remarks": f"Pending BUY limit at {signal.get('entry', '')}",
            "Order Status": zerodha_status,
            "Zerodha Order Status": zerodha_status,
            "Entry Order ID": order_id,
            "Exit Order ID": "",
            "Buy Score": signal.get("score_row", {}).get("Buy Score", ""),
            "Buy Entry": signal.get("score_row", {}).get("Buy Entry", ""),
        }
        self._append_excel(self.save_path, [trade])
        self._log_trade(trade)
        self._emit_order_event(
            signal,
            self.trade_count + 1,
            "BUY",
            zerodha_status,
            order_id=order_id,
            order_type="LIMIT",
            quantity=qty,
            entry_price="",
            limit_price=signal.get("entry", ""),
            remarks=f"Pending BUY limit at {signal.get('entry', '')}",
        )
        if self.on_trade:
            self.on_trade(trade, self.balance)

    def _record_exit_order_placed(self, position, reason, order_id, limit_price, trigger_price, order_kind):
        zerodha_status = self._zerodha_order_status(order_id, fallback="OPEN")
        self.last_order_status_by_id[str(order_id)] = zerodha_status
        price_text = []
        if limit_price not in ("", None):
            price_text.append(f"limit {limit_price}")
        if trigger_price not in ("", None):
            price_text.append(f"trigger {trigger_price}")
        trade = {
            "Trade No": position["trade_no"],
            "Type": position["signal"].get("type", ""),
            "Instrument": position["signal"].get("instrument", ""),
            "Strike": position["signal"].get("strike", ""),
            "Expiry": position["signal"].get("expiry", ""),
            "Entry Time": position.get("entry_time", ""),
            "Entry": position.get("entry_price", ""),
            "Entry Offset": position["signal"].get("entry_offset", ""),
            "Exit Time": "",
            "Exit": limit_price or trigger_price or "",
            "PnL": 0,
            "Live PnL": 0,
            "Final PnL": 0,
            "Total PnL": self.balance,
            "Quantity": position.get("quantity", ""),
            "Contract Lot Size": position.get("contract_lot_size", ""),
            "Reason": reason,
            "Remarks": f"{order_kind} {'; '.join(price_text)}",
            "Order Status": zerodha_status,
            "Zerodha Order Status": zerodha_status,
            "Entry Order ID": position.get("entry_order_id", ""),
            "Exit Order ID": order_id,
            "Buy Score": position["signal"].get("score_row", {}).get("Buy Score", ""),
            "Buy Entry": position["signal"].get("score_row", {}).get("Buy Entry", ""),
        }
        self._append_excel(self.save_path, [trade])
        self._log_trade(trade)
        self._log_lifecycle_event(
            PROTECTIVE_ORDER_PLACED,
            "INFO",
            reason,
            order_id=order_id,
            trade_no=position.get("trade_no", ""),
            status=zerodha_status,
            side="SELL",
            instrument=position["signal"].get("instrument", ""),
            quantity=position.get("quantity", ""),
            payload={
                "order_kind": order_kind,
                "entry_order_id": position.get("entry_order_id", ""),
                "exit_order_id": order_id,
                "limit_price": limit_price,
                "trigger_price": trigger_price,
                "target_price": position.get("target", ""),
                "stoploss_price": position.get("stoploss", ""),
            },
        )
        self._emit_order_event(
            position["signal"],
            position["trade_no"],
            "SELL",
            zerodha_status,
            order_id=order_id,
            order_type="LIMIT" if "LIMIT" in order_kind else "SL-M",
            quantity=position.get("quantity", ""),
            entry_price=position.get("entry_price", ""),
            exit_price="",
            limit_price=limit_price,
            trigger_price=trigger_price,
            target_price=position.get("target", ""),
            stoploss_price=position.get("stoploss", ""),
            remarks=f"{order_kind} {'; '.join(price_text)}",
            parent_order_id=position.get("entry_order_id", ""),
        )
        if self.on_trade:
            self.on_trade(trade, self.balance)

    def _zerodha_order_status(self, order_id, fallback="OPEN"):
        if self.mode != "LIVE" or not self.zerodha or not order_id:
            return fallback
        status = self.orders.order_status(order_id, fallback=fallback)
        return status if status and status != "UNKNOWN" else fallback

    def _record_order_status_change(
        self,
        order_id,
        status,
        signal,
        reason,
        entry_order_id="",
        exit_order_id="",
        quantity="",
        lot_size="",
        entry="",
        exit_price="",
    ):
        if not order_id or not status or status == "UNKNOWN":
            return
        key = str(order_id)
        previous_status = self.last_order_status_by_id.get(key)
        if previous_status == status:
            return
        self.last_order_status_by_id[key] = status
        if previous_status is None:
            return

        trade = {
            "Trade No": self.trade_count + 1,
            "Type": signal.get("type", ""),
            "Instrument": signal.get("instrument", ""),
            "Strike": signal.get("strike", ""),
            "Expiry": signal.get("expiry", ""),
            "Entry Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Entry": entry,
            "Entry Offset": signal.get("entry_offset", ""),
            "Exit Time": "",
            "Exit": exit_price,
            "PnL": 0,
            "Live PnL": 0,
            "Final PnL": 0,
            "Total PnL": self.balance,
            "Quantity": quantity,
            "Contract Lot Size": lot_size,
            "Reason": reason,
            "Remarks": f"Order {order_id} status changed from {previous_status} to {status}",
            "Order Status": status,
            "Zerodha Order Status": status,
            "Entry Order ID": entry_order_id,
            "Exit Order ID": exit_order_id,
            "Buy Score": signal.get("score_row", {}).get("Buy Score", ""),
            "Buy Entry": signal.get("score_row", {}).get("Buy Entry", ""),
        }
        self._append_excel(self.save_path, [trade])
        self._log_trade(trade)
        side = "SELL" if exit_order_id else "BUY"
        lifecycle_event_type = self._event_type_for_order_status(status)
        if lifecycle_event_type:
            self._log_lifecycle_event(
                lifecycle_event_type,
                self._event_level_for_order_status(status),
                f"Order {order_id} status changed from {previous_status} to {status}",
                order_id=order_id,
                trade_no=self.trade_count + 1,
                status=status,
                side=side,
                instrument=signal.get("instrument", ""),
                quantity=quantity,
                payload={
                    "previous_status": previous_status,
                    "new_status": status,
                    "normalized_status": self._status_for_active_order(status),
                    "entry_order_id": entry_order_id,
                    "exit_order_id": exit_order_id,
                    "reason": reason,
                    "quantity": quantity,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "lot_size": lot_size,
                },
            )
        self._emit_order_event(
            signal,
            self.trade_count + 1,
            side,
            status,
            order_id=order_id,
            order_type=self._order_type_from_text(reason),
            quantity=quantity,
            entry_price=entry,
            exit_price=exit_price,
            remarks=f"Order {order_id} status changed from {previous_status} to {status}",
            parent_order_id=entry_order_id if exit_order_id else "",
        )
        if self.on_trade:
            self.on_trade(trade, self.balance)

    def _event_type_for_order_status(self, status):
        normalized_status = self._status_for_active_order(status)
        if normalized_status in {"OPEN", "TRIGGER PENDING"}:
            return ORDER_OPEN
        if normalized_status == "COMPLETE":
            return ORDER_COMPLETE
        if normalized_status == "REJECTED":
            return ORDER_REJECTED
        if normalized_status == "CANCELLED":
            return ORDER_CANCELLED
        return ""

    def _event_level_for_order_status(self, status):
        normalized_status = self._status_for_active_order(status)
        if normalized_status == "REJECTED":
            return "ERROR"
        if normalized_status == "CANCELLED":
            return "WARN"
        return "INFO"

    def _record_rejected_entry(self, signal, i, status):
        trade = {
            "Trade No": self.trade_count + 1,
            "Type": signal.get("type", ""),
            "Instrument": signal.get("instrument", ""),
            "Strike": signal.get("strike", ""),
            "Expiry": signal.get("expiry", ""),
            "Entry Time": self._row_time(self.nifty, i),
            "Entry": signal.get("entry", ""),
            "Entry Offset": signal.get("entry_offset", ""),
            "Exit Time": "",
            "Exit": "",
            "PnL": 0,
            "Live PnL": 0,
            "Final PnL": 0,
            "Total PnL": self.balance,
            "Quantity": 0,
            "Contract Lot Size": "",
            "Reason": "ENTRY REJECTED",
            "Remarks": status,
            "Order Status": status,
            "Zerodha Order Status": status,
            "Entry Order ID": signal.get("entry_order_id", ""),
            "Exit Order ID": "",
            "Buy Score": signal.get("score_row", {}).get("Buy Score", ""),
            "Buy Entry": signal.get("score_row", {}).get("Buy Entry", ""),
        }
        self.trades.append(trade)
        self.trade_count += 1
        self._append_excel(self.save_path, [trade])
        self._log_trade(trade)
        if self.on_trade:
            self.on_trade(trade, self.balance)

    def _save_open_position(self):
        if not self.position_state_path or not self.open_position:
            return
        position = dict(self.open_position)
        signal = dict(position.get("signal", {}))
        signal.pop("option", None)
        position["signal"] = signal
        with open(self.position_state_path, "w", encoding="utf-8") as handle:
            json.dump(position, handle, default=str, indent=2)
        if self.store:
            self.store.save_state(f"{self.mode.lower()}_open_position", position)

    def _load_open_position(self):
        if not self.position_state_path or not os.path.exists(self.position_state_path):
            position = self.store.load_state(f"{self.mode.lower()}_open_position") if self.store else None
            if not position:
                return
            try:
                option_index = int(position["option_index"])
                position["signal"]["option"] = self.options[option_index]
                self.open_position = position
            except Exception:
                self.open_position = None
            return
        try:
            with open(self.position_state_path, "r", encoding="utf-8") as handle:
                position = json.load(handle)
            option_index = int(position["option_index"])
            position["signal"]["option"] = self.options[option_index]
            self.open_position = position
        except Exception:
            self.open_position = None

    def _clear_open_position(self):
        if self.position_state_path and os.path.exists(self.position_state_path):
            os.remove(self.position_state_path)
        if self.store:
            self.store.clear_state(f"{self.mode.lower()}_open_position")

    def _save_pending_entry(self):
        if not self.pending_entry:
            return
        pending = dict(self.pending_entry)
        signal = dict(pending.get("signal", {}))
        signal.pop("option", None)
        pending["signal"] = signal
        pending.pop("timer", None)
        if self.pending_state_path:
            with open(self.pending_state_path, "w", encoding="utf-8") as handle:
                json.dump(pending, handle, default=str, indent=2)
        if self.store:
            self.store.save_state(f"{self.mode.lower()}_pending_entry", pending)

    def _load_pending_entry(self):
        data = None
        if self.pending_state_path and os.path.exists(self.pending_state_path):
            try:
                with open(self.pending_state_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                data = None
        if data is None and self.store:
            data = self.store.load_state(f"{self.mode.lower()}_pending_entry")
        if not data:
            return
        try:
            option_index = int(data["option_index"])
            data["signal"]["option"] = self.options[option_index]
            data["placed_at"] = pd.to_datetime(data["placed_at"], errors="coerce").to_pydatetime()
            self.pending_entry = data
            elapsed = (datetime.now() - data["placed_at"]).total_seconds()
            remaining = max(0, 60 - elapsed)
            timer = threading.Timer(remaining, self._expire_pending_entry_order)
            timer.daemon = True
            self.pending_entry["timer"] = timer
            timer.start()
        except Exception as exc:
            self.pending_entry = None
            self._log_event("ERROR", "Could not restore pending entry", {"error": str(exc)})

    def _clear_pending_entry(self):
        if self.pending_state_path and os.path.exists(self.pending_state_path):
            os.remove(self.pending_state_path)
        if self.store:
            self.store.clear_state(f"{self.mode.lower()}_pending_entry")

    def _save_kill_switch_state(self):
        state = {
            "active": self.risk_guard.kill_switch_active,
            "reason": self.risk_guard.kill_switch_reason,
            "blocked_reason": self.risk_guard.blocked_reason,
            "session_id": self.session_id,
        }
        if self.kill_switch_state_path:
            with open(self.kill_switch_state_path, "w", encoding="utf-8") as handle:
                json.dump(state, handle, default=str, indent=2)
        if self.store:
            self.store.save_state(f"{self.session_id}_kill_switch", state)

    def _load_kill_switch_state(self):
        data = None
        if self.kill_switch_state_path and os.path.exists(self.kill_switch_state_path):
            try:
                with open(self.kill_switch_state_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                data = None
        if data is None and self.store:
            data = self.store.load_state(f"{self.session_id}_kill_switch")
        if not data:
            return
        self.risk_guard.restore_kill_switch(
            active=data.get("active", False),
            reason=data.get("reason", ""),
        )
        self._sync_risk_state_from_guard()

    def _trading_blocked(self):
        blocked, _reason = self.risk_guard.is_blocked(self.balance)
        self._sync_risk_state_from_guard()
        return blocked

    def _square_off_time_reached(self):
        return self.risk_guard.square_off_time_reached()

    def _log_event(self, level, message, payload=None):
        if self.store:
            self.store.log_event(level, message, payload)

    def _emit_alert(self, level, code, message, payload=None):
        if not self.on_alert:
            return
        alert = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": str(level or "").upper(),
            "code": str(code or ""),
            "message": str(message or ""),
            "session_id": self.session_id,
            "mode": self.mode,
            "payload": payload or {},
        }
        try:
            self.on_alert(alert)
        except Exception as exc:
            self._log_event("ERROR", "Alert callback failed", {"error": str(exc), "alert": alert})

    def _log_lifecycle_event(
        self,
        event_type,
        level,
        message,
        order_id="",
        trade_no="",
        status="",
        side="",
        instrument="",
        quantity=None,
        payload=None,
    ):
        return self.event_logger.log(
            event_type,
            level,
            message,
            order_id=order_id,
            trade_no=trade_no,
            status=status,
            side=side,
            instrument=instrument,
            quantity=quantity,
            payload=payload,
        )

    def _log_trade(self, trade):
        if self.store:
            self.store.log_trade(trade)

    def _log_order(self, order_id, side, status, data=None):
        if self.store:
            self.store.log_order(order_id, side, status, data)

    def _status_for_active_order(self, status):
        text = str(status or "").upper()
        if text.startswith("PAPER"):
            return "COMPLETE"
        if "PLACED" in text and "SL-M" in text:
            return "TRIGGER PENDING"
        if "PLACED" in text or text in {"OPEN", "PENDING"}:
            return "OPEN"
        if "COMPLETE" in text or "FILLED" in text:
            return "COMPLETE"
        if "REJECT" in text:
            return "REJECTED"
        if "CANCEL" in text:
            return "CANCELLED"
        if text.startswith("FAILED"):
            return "REJECTED"
        return text or "PENDING"

    def _order_type_from_text(self, text):
        text = str(text or "").upper()
        if "SL-M" in text:
            return "SL-M"
        if "LIMIT" in text:
            return "LIMIT"
        if "SL" in text:
            return "SL"
        return "MARKET"

    def _action_from_reason(self, side, reason):
        reason = str(reason or "").upper()
        side = str(side or "").upper()
        if "TARGET" in reason:
            return "TARGET SELL"
        if "STOPLOSS" in reason or "STOP LOSS" in reason:
            return "STOPLOSS SELL"
        if "CANCEL" in reason:
            return "CANCEL ORDER"
        if "MANUAL" in reason or "SQUARE OFF" in reason:
            return "MANUAL SELL"
        return "BUY" if side == "BUY" else "SELL"

    def _current_ltp(self, option_index):
        if option_index in self.latest_ltp_by_option:
            return self.latest_ltp_by_option[option_index]
        try:
            option = self.options[int(option_index)]
            if len(option):
                return float(option.iloc[-1].get("close", 0) or 0)
        except Exception:
            return ""
        return ""

    def _live_pnl_values(self, entry_price, quantity, ltp):
        try:
            entry = float(entry_price)
            qty = int(quantity)
            current = float(ltp)
        except (TypeError, ValueError):
            return "", ""
        points = current - entry
        pnl = points * qty
        pnl_percent = (points / entry) * 100 if entry else 0
        return pnl, pnl_percent

    def _order_quantity_visibility(self, order_id, quantity, price_fallback, normalized_status):
        if order_id:
            details = self.orders.order_details(
                order_id,
                fallback_quantity=quantity or 0,
                fallback_price=price_fallback or 0,
            )
            return {
                "ordered": details.get("quantity", ""),
                "filled": details.get("filled_quantity", ""),
                "pending": details.get("pending_quantity", ""),
                "cancelled": details.get("cancelled_quantity", ""),
                "partial": "YES" if details.get("is_partial") else "",
            }

        if quantity in ("", None):
            return {"ordered": "", "filled": "", "pending": "", "cancelled": "", "partial": ""}

        if normalized_status == "COMPLETE":
            return {"ordered": quantity, "filled": quantity, "pending": 0, "cancelled": 0, "partial": ""}
        if normalized_status == "CANCELLED":
            return {"ordered": quantity, "filled": 0, "pending": 0, "cancelled": quantity, "partial": ""}
        if normalized_status == "REJECTED":
            return {"ordered": quantity, "filled": 0, "pending": 0, "cancelled": 0, "partial": ""}
        return {"ordered": quantity, "filled": 0, "pending": quantity, "cancelled": 0, "partial": ""}

    def _emit_order_event(
        self,
        signal,
        trade_no,
        side,
        status,
        order_id="",
        order_type="MARKET",
        quantity="",
        entry_price="",
        exit_price="",
        limit_price="",
        trigger_price="",
        target_price="",
        stoploss_price="",
        exit_reason="",
        remarks="",
        parent_order_id="",
        related_trade_id="",
        timestamp=None,
        keep_active=True,
    ):
        signal = signal or {}
        timestamp = format_datetime_value(timestamp or datetime.now())
        option_index = signal.get("option_index", "")
        ltp = self._current_ltp(option_index)
        live_pnl, live_pnl_percent = "", ""
        if entry_price not in ("", None) and quantity not in ("", None):
            live_pnl, live_pnl_percent = self._live_pnl_values(entry_price, quantity, ltp)
        normalized_status = self._status_for_active_order(status)
        price_fallback = entry_price or exit_price or limit_price or trigger_price or 0
        quantity_details = self._order_quantity_visibility(order_id, quantity, price_fallback, normalized_status)
        action = self._action_from_reason(side, exit_reason or remarks or status)
        buy_score = signal.get("score_row", {}).get("Buy Score", "")
        sell_score = signal.get("score_row", {}).get("Sell Score", "")
        trade_id = related_trade_id or f"{self.session_id}_{trade_no}"

        active_row = {
            "Trade ID": trade_id,
            "Time": timestamp,
            "Symbol / Instrument": signal.get("instrument", ""),
            "Option Type": signal.get("type", ""),
            "Order Side": side,
            "Order Type": order_type,
            "Product Type": self._order_product(),
            "Quantity": quantity,
            "Ordered Quantity": quantity_details["ordered"],
            "Filled Quantity": quantity_details["filled"],
            "Pending Quantity": quantity_details["pending"],
            "Cancelled Quantity": quantity_details["cancelled"],
            "Is Partial Fill": quantity_details["partial"],
            "Order Status": normalized_status,
            "Entry Price": entry_price,
            "Exit Price": exit_price,
            "Limit Price": limit_price,
            "Trigger Price": trigger_price,
            "Buy Score": buy_score,
            "Sell Score": sell_score,
            "Stop Loss Price": stoploss_price,
            "Target Price": target_price,
            "Current LTP": ltp,
            "Live PnL": live_pnl,
            "Exit Reason": exit_reason,
            "Zerodha Order ID": order_id,
            "Remarks / Error Message": remarks,
        }
        history_row = {
            "Session Trade No": trade_no,
            "Timestamp": timestamp,
            "Instrument / Symbol": signal.get("instrument", ""),
            "Option Type": signal.get("type", ""),
            "Action": action,
            "Order Type": order_type,
            "Quantity": quantity,
            "Ordered Quantity": quantity_details["ordered"],
            "Filled Quantity": quantity_details["filled"],
            "Pending Quantity": quantity_details["pending"],
            "Cancelled Quantity": quantity_details["cancelled"],
            "Is Partial Fill": quantity_details["partial"],
            "Order Status": normalized_status,
            "Entry Price": entry_price if side == "BUY" else "",
            "Buy Score": buy_score if side == "BUY" else "",
            "Exit Price": exit_price if side == "SELL" else "",
            "Exit Reason": exit_reason if side == "SELL" else "",
            "Target Price": target_price,
            "Stop Loss Price": stoploss_price,
            "LTP at Order Placement": ltp,
            "Zerodha Order ID": order_id,
            "Parent Order ID": parent_order_id,
            "Related Trade ID": trade_id,
            "Error / Rejection Reason": remarks if normalized_status in {"REJECTED", "CANCELLED"} else "",
        }

        key = str(order_id or trade_id or f"{trade_no}_{side}_{timestamp}")
        if keep_active and normalized_status not in {"CANCELLED", "REJECTED"}:
            self.active_orders[key] = active_row
        elif key in self.active_orders:
            self.active_orders[key] = active_row
        if normalized_status in {"CANCELLED", "REJECTED"}:
            self.active_orders[key] = active_row

        self.order_history.append(history_row)
        if self.store:
            self.store.log_order_history(history_row)
        self._emit_live_log_update(history_row)

    def _refresh_live_trade_snapshot(self, ltp=None, timestamp=None, final_trade=None):
        position = self.open_position
        if final_trade:
            entry = final_trade.get("Entry", "")
            exit_price = final_trade.get("Exit", "")
            quantity = final_trade.get("Quantity", "")
            pnl = final_trade.get("Final PnL", final_trade.get("PnL", ""))
            pnl_percent = ""
            try:
                pnl_percent = ((float(exit_price) - float(entry)) / float(entry)) * 100 if float(entry) else 0
            except (TypeError, ValueError):
                pass
            self.latest_live_trade = {
                "Trade ID": f"{self.session_id}_{final_trade.get('Trade No', '')}",
                "Instrument / Symbol": final_trade.get("Instrument", ""),
                "Option Type": final_trade.get("Type", ""),
                "Current Trade Side": "WAITING",
                "Entry Time": final_trade.get("Entry Time", ""),
                "Entry Price": entry,
                "Buy Score at Entry": final_trade.get("Buy Score", ""),
                "Quantity": quantity,
                "Target Price": "",
                "Stop Loss Price": "",
                "Current LTP": exit_price,
                "Live PnL": pnl,
                "Live PnL %": pnl_percent,
                "Status": "COMPLETE",
            }
            self._emit_live_log_update()
            return
        if not position:
            return
        signal = position["signal"]
        current_ltp = ltp if ltp not in ("", None) else self._current_ltp(position["option_index"])
        pnl, pnl_percent = self._live_pnl_values(position["entry_price"], position["quantity"], current_ltp)
        self.latest_live_trade = {
            "Trade ID": f"{self.session_id}_{position.get('trade_no', '')}",
            "Instrument / Symbol": signal.get("instrument", ""),
            "Option Type": signal.get("type", ""),
            "Current Trade Side": "BUY",
            "Entry Time": position.get("entry_time", ""),
            "Entry Price": position.get("entry_price", ""),
            "Buy Score at Entry": signal.get("score_row", {}).get("Buy Score", ""),
            "Quantity": position.get("quantity", ""),
            "Target Price": position.get("target", ""),
            "Stop Loss Price": position.get("stoploss", ""),
            "Current LTP": current_ltp,
            "Live PnL": pnl,
            "Live PnL %": pnl_percent,
            "Status": "ACTIVE",
        }
        for row in self.active_orders.values():
            if row.get("Trade ID") == self.latest_live_trade["Trade ID"]:
                row["Current LTP"] = current_ltp
                row["Live PnL"] = pnl
        self._emit_live_log_update()

    def _emit_live_log_update(self, order_event=None, force=False):
        if not self.on_order_update:
            return
        now = time.monotonic()
        if order_event is None and not force and self.ui_update_interval > 0:
            if now - self.last_ui_update_at < self.ui_update_interval:
                self.suppressed_ui_updates += 1
                return
        payload = {
            "active_orders": list(self.active_orders.values()),
            "live_trade": dict(self.latest_live_trade),
            "order_event": order_event,
            "health": self.health_snapshot(),
            "ui_update_stats": {
                "emitted": self.emitted_ui_updates + 1,
                "suppressed": self.suppressed_ui_updates,
                "interval_seconds": self.ui_update_interval,
            },
        }
        self.last_ui_update_at = now
        self.emitted_ui_updates += 1
        self.on_order_update(payload)

    def health_snapshot(self):
        excel_health = {
            "enabled": bool(self.excel_writer),
            "queue_size": self.excel_writer.queue.qsize() if self.excel_writer else 0,
            "enqueued_rows": self.excel_writer.enqueued_rows if self.excel_writer else 0,
            "flushed_rows": self.excel_writer.flushed_rows if self.excel_writer else 0,
            "errors": list(self.excel_writer.errors[-5:]) if self.excel_writer else [],
        }
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": self.mode,
            "session_id": self.session_id,
            "session_closed": self.session_closed,
            "open_position": bool(self.open_position),
            "pending_entry": bool(self.pending_entry),
            "active_orders": len(self.active_orders),
            "duplicate_order_suppressed": self.duplicate_order_suppressed,
            "order_history_rows": len(self.order_history),
            "order_monitor_alive": bool(self.order_monitor_thread and self.order_monitor_thread.is_alive()),
            "kill_switch_active": bool(self.risk_guard.kill_switch_active),
            "trading_blocked_reason": self.trading_blocked_reason,
            "candle_builder": {
                **self.candle_builder.stats,
                "active_keys": len(self.candle_builder.current),
            },
            "excel_writer": excel_health,
            "store": {
                "enabled": bool(self.store),
                "path": self.store.path if self.store else "",
                **(self.store.health() if self.store and hasattr(self.store, "health") else {"async": False}),
            },
            "ui": {
                "emitted_updates": self.emitted_ui_updates,
                "suppressed_updates": self.suppressed_ui_updates,
                "interval_seconds": self.ui_update_interval,
            },
        }

    def _log_candle(self, i):
        if not self.candle_log_path:
            return
        row = self.nifty.iloc[i]
        bullish_threshold = float(self.settings.get("bullish_threshold", 16))
        bearish_threshold = float(self.settings.get("bearish_threshold", -15))
        rsi_bull = float(self.settings.get("rsi_bull", 55))
        rsi_bear = float(self.settings.get("rsi_bear", 45))
        scored = build_scoring_row(self.nifty, i, bullish_threshold, bearish_threshold, rsi_bull, rsi_bear)
        export = {
            "Date": row.get("date", ""),
            "Open": row.get("open", ""),
            "High": row.get("high", ""),
            "Low": row.get("low", ""),
            "Close": row.get("close", ""),
            **scored,
        }
        self._append_excel(self.candle_log_path, [export])

    def _append_excel(self, path, rows):
        if not path or not rows:
            return
        if self.excel_writer:
            self.excel_writer.append(path, rows)

    def _row_time(self, df, index):
        if index >= len(df):
            return ""
        row = df.iloc[index]
        value = row.get("datetime", "")
        return format_datetime_value(value) if value != "" else index

    def close_session(self):
        if self.session_closed:
            return
        self.session_closed = True
        self.order_monitor_stop.set()
        if self.order_monitor_thread and self.order_monitor_thread.is_alive():
            self.order_monitor_thread.join(timeout=2)
        self.order_monitor_thread = None
        if self.store:
            self.store.update_session(
                self.mode,
                self.session_id,
                final_balance=self.balance,
                total_trades=self.trade_count,
                ended=True,
            )
            if hasattr(self.store, "close"):
                self.store.close(timeout=10)
            self._write_session_audit_report()
        if self.excel_writer:
            self.excel_writer.close(timeout=10)

    def _write_session_audit_report(self):
        if not self.audit_report_path or not self.store:
            return None
        try:
            return write_session_audit(
                self.store.path,
                self.audit_report_path,
                session_id=self.session_id,
            )
        except Exception as exc:
            self._log_event("ERROR", "Session audit report failed", {"error": str(exc)})
            return None


class Executor:
    def __init__(self, zerodha=None):
        self.running = False
        self.zerodha = zerodha
        self.live_paper_session = None
        self.live_real_session = None
        self.active_ticker = None
        self.tick_queue = None
        self.tick_worker = None
        self.tick_stop = threading.Event()
        self.tick_queue_size = 10000
        self.dropped_tick_batches = 0
        self.processed_tick_count = 0
        self.received_tick_count = 0
        self.last_tick_received_at = None
        self.last_tick_processed_at = None
        self.dispatcher_started_at = None
        self.dispatcher_error_count = 0
        self.last_dispatcher_error = ""
        self.feed_tokens = []
        self.feed_on_ticks = None
        self.feed_on_connect = None
        self.feed_on_close = None
        self.feed_on_error = None
        self.feed_should_run = False
        self.feed_reconnect_attempts = 0
        self.feed_reconnect_timer = None
        self.feed_watchdog_timer = None
        self.feed_backoff_seconds = (1, 2, 5, 10, 20, 30)
        self.feed_stale_after_seconds = 15
        self.feed_status = "stopped"
        self.last_feed_event = ""
        self.last_preflight_report = None

    def connect_zerodha(self, api_key, api_secret=None, access_token=None):
        self.zerodha = ZerodhaClient(api_key, api_secret, access_token)
        return self.zerodha

    def fetch_live_history(self, nifty_token, option_contracts, days=5, interval="5minute"):
        if not self.zerodha:
            raise ValueError("Connect Zerodha first.")
        to_date = datetime.now()
        from_date = to_date - timedelta(days=int(days))
        nifty = self.zerodha.historical_candles(nifty_token, from_date, to_date, interval=interval)
        nifty = clean_and_add_indicators(nifty)
        options = []
        for contract in option_contracts:
            df = self.zerodha.historical_candles(contract["token"], from_date, to_date, interval=interval)
            df = clean_and_add_indicators(df)
            df = ensure_option_formula_columns(df)
            df.attrs["instrument"] = contract["tradingsymbol"]
            df.attrs["tradingsymbol"] = contract["tradingsymbol"]
            if contract.get("strike"):
                df.attrs["strike"] = contract.get("strike")
            if contract.get("expiry"):
                df.attrs["expiry"] = str(contract.get("expiry"))[:10]
            options.append(df)
        return nifty, options

    def start_market_feed(self, tokens, on_ticks, on_connect=None, on_close=None):
        if not self.zerodha:
            raise ValueError("Connect Zerodha first.")
        self.feed_tokens = [int(token) for token in tokens]
        self.feed_on_ticks = on_ticks
        self.feed_on_connect = on_connect
        self.feed_on_close = on_close
        self.feed_on_error = None
        self.feed_should_run = True
        self.feed_reconnect_attempts = 0
        self._cancel_reconnect_timer()
        self._cancel_watchdog_timer()
        self.feed_status = "connecting"
        self._start_tick_dispatcher(on_ticks)
        self._connect_market_feed()
        self._schedule_watchdog()
        return self.active_ticker

    def _connect_market_feed(self):
        if not self.feed_should_run:
            return None
        self.feed_status = "connecting"
        self.active_ticker = self.zerodha.start_ticker(
            self.feed_tokens,
            on_ticks=self._enqueue_ticks,
            on_connect=self._handle_feed_connect,
            on_close=self._handle_feed_close,
            on_error=self._handle_feed_error,
            on_reconnect=self._handle_feed_reconnect,
            on_noreconnect=self._handle_feed_noreconnect,
        )
        return self.active_ticker

    def stop_market_feed(self):
        self.feed_should_run = False
        self.feed_status = "stopped"
        self._cancel_reconnect_timer()
        self._cancel_watchdog_timer()
        for session in (self.live_paper_session, self.live_real_session):
            if session:
                session.close_session()
        if self.zerodha:
            self.zerodha.stop_ticker()
        self._stop_tick_dispatcher()
        self.active_ticker = None

    def _handle_feed_connect(self, response):
        self.feed_status = "connected"
        self.last_feed_event = "connected"
        self.feed_reconnect_attempts = 0
        if self.feed_on_connect:
            self.feed_on_connect(response)

    def _handle_feed_close(self, code, reason):
        self.feed_status = "closed"
        self.last_feed_event = f"closed ({code}) {reason}"
        if self.feed_on_close:
            self.feed_on_close(code, reason)
        self._schedule_reconnect(f"closed ({code}) {reason}")

    def _handle_feed_error(self, code, reason):
        self.feed_status = "error"
        self.last_feed_event = f"error ({code}) {reason}"
        self._schedule_reconnect(f"error ({code}) {reason}")

    def _handle_feed_reconnect(self, attempts_count):
        self.feed_status = "kite_reconnecting"
        self.last_feed_event = f"kiteticker reconnect attempt {attempts_count}"

    def _handle_feed_noreconnect(self):
        self.feed_status = "kite_no_reconnect"
        self.last_feed_event = "kiteticker no reconnect"
        self._schedule_reconnect("kiteticker no reconnect")

    def _schedule_reconnect(self, reason):
        if not self.feed_should_run:
            return
        self._cancel_reconnect_timer()
        delay = self.feed_backoff_seconds[min(self.feed_reconnect_attempts, len(self.feed_backoff_seconds) - 1)]
        self.feed_reconnect_attempts += 1
        self.feed_status = f"reconnecting_in_{delay}s"
        self.last_feed_event = f"{reason}; reconnect in {delay}s"
        self.feed_reconnect_timer = threading.Timer(delay, self._reconnect_market_feed)
        self.feed_reconnect_timer.daemon = True
        self.feed_reconnect_timer.start()

    def _reconnect_market_feed(self):
        if not self.feed_should_run:
            return
        try:
            if self.zerodha:
                self.zerodha.stop_ticker()
        except Exception:
            pass
        try:
            self._connect_market_feed()
        except Exception as exc:
            self._schedule_reconnect(f"reconnect failed: {exc}")

    def _schedule_watchdog(self):
        if not self.feed_should_run:
            return
        self._cancel_watchdog_timer()
        self.feed_watchdog_timer = threading.Timer(5, self._watchdog_check)
        self.feed_watchdog_timer.daemon = True
        self.feed_watchdog_timer.start()

    def _watchdog_check(self):
        if not self.feed_should_run:
            return
        now = time.time()
        last_tick = self.last_tick_received_at or self.last_tick_processed_at
        if self.feed_status == "connected" and last_tick and now - last_tick > self.feed_stale_after_seconds:
            self.feed_status = "stale"
            self._schedule_reconnect(f"stale feed: no ticks for {int(now - last_tick)}s")
        self._schedule_watchdog()

    def _cancel_reconnect_timer(self):
        timer = self.feed_reconnect_timer
        if timer:
            timer.cancel()
        self.feed_reconnect_timer = None

    def _cancel_watchdog_timer(self):
        timer = self.feed_watchdog_timer
        if timer:
            timer.cancel()
        self.feed_watchdog_timer = None

    def _start_tick_dispatcher(self, on_ticks):
        self._stop_tick_dispatcher()
        self.tick_queue = queue.Queue(maxsize=self.tick_queue_size)
        self.tick_stop.clear()
        self.dropped_tick_batches = 0
        self.processed_tick_count = 0
        self.received_tick_count = 0
        self.last_tick_received_at = None
        self.last_tick_processed_at = None
        self.dispatcher_started_at = time.monotonic()
        self.dispatcher_error_count = 0
        self.last_dispatcher_error = ""

        def worker():
            while not self.tick_stop.is_set() or not self.tick_queue.empty():
                try:
                    ticks = self.tick_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    try:
                        on_ticks(ticks)
                        self.processed_tick_count += len(ticks)
                        self.last_tick_processed_at = time.time()
                    except Exception as exc:
                        self.dispatcher_error_count += 1
                        self.last_dispatcher_error = str(exc)
                finally:
                    self.tick_queue.task_done()

        self.tick_worker = threading.Thread(
            target=worker,
            name="tradebot_tick_dispatcher",
            daemon=True,
        )
        self.tick_worker.start()

    def _stop_tick_dispatcher(self):
        self.tick_stop.set()
        worker = self.tick_worker
        if worker and worker.is_alive():
            worker.join(timeout=2)
        self.tick_worker = None
        self.tick_queue = None

    def _enqueue_ticks(self, ticks):
        if not ticks or self.tick_queue is None:
            return
        batch = list(ticks)
        self.received_tick_count += len(batch)
        self.last_tick_received_at = time.time()
        try:
            self.tick_queue.put_nowait(batch)
            return
        except queue.Full:
            self.dropped_tick_batches += 1

        try:
            self.tick_queue.get_nowait()
            self.tick_queue.task_done()
        except queue.Empty:
            pass

        try:
            self.tick_queue.put_nowait(batch)
        except queue.Full:
            self.dropped_tick_batches += 1

    def start_live_paper_trading(self, nifty, options, token_map, settings, save_path, on_trade=None, on_ticks=None, on_connect=None, on_close=None, on_order_update=None, on_alert=None):
        self.last_preflight_report = validate_live_preflight(
            nifty,
            options,
            token_map,
            settings,
            mode="PAPER",
            zerodha=self.zerodha,
        )
        self.last_preflight_report.raise_for_errors()
        self.live_paper_session = LivePaperSession(
            nifty,
            options,
            token_map,
            settings,
            save_path=save_path,
            on_trade=on_trade,
            on_order_update=on_order_update,
            on_alert=on_alert,
            mode="PAPER",
            zerodha=self.zerodha,
        )
        self.start_market_feed(list(token_map.keys()), on_ticks=self._combine_tick_handlers(self.live_paper_session, on_ticks), on_connect=on_connect, on_close=on_close)
        return self.live_paper_session

    def start_live_real_trading(self, nifty, options, token_map, settings, save_path, on_trade=None, on_ticks=None, on_connect=None, on_close=None, on_order_update=None, on_alert=None):
        self.last_preflight_report = validate_live_preflight(
            nifty,
            options,
            token_map,
            settings,
            mode="LIVE",
            zerodha=self.zerodha,
        )
        self.last_preflight_report.raise_for_errors()
        self.live_real_session = LivePaperSession(
            nifty,
            options,
            token_map,
            settings,
            save_path=save_path,
            on_trade=on_trade,
            on_order_update=on_order_update,
            on_alert=on_alert,
            mode="LIVE",
            zerodha=self.zerodha,
        )
        if on_trade:
            on_trade({
                "Trade No": "",
                "Type": "INFO",
                "Instrument": "ZERODHA",
                "Entry Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Entry": "",
                "Exit Time": "",
                "Exit": "",
                "PnL": 0,
                "Live PnL": 0,
                "Final PnL": 0,
                "Total PnL": self.live_real_session.balance,
                "Quantity": "",
                "Contract Lot Size": "",
                "Reason": "STARTING MARGIN",
                "Remarks": "Balance fetched from Zerodha available margin",
                "Order Status": "INFO",
                "Zerodha Order Status": "INFO",
                "Entry Order ID": "",
                "Exit Order ID": "",
            }, self.live_real_session.balance)
        self.start_market_feed(list(token_map.keys()), on_ticks=self._combine_tick_handlers(self.live_real_session, on_ticks), on_connect=on_connect, on_close=on_close)
        return self.live_real_session

    def _combine_tick_handlers(self, session, user_handler):
        def handler(ticks):
            session.on_ticks(ticks)
            if user_handler:
                user_handler(ticks)
        return handler

    def tick_backlog(self):
        if self.tick_queue is None:
            return 0
        return self.tick_queue.qsize()

    def feed_metrics(self):
        elapsed = 0
        if self.dispatcher_started_at:
            elapsed = max(time.monotonic() - self.dispatcher_started_at, 0.001)
        return {
            "received_ticks": self.received_tick_count,
            "processed_ticks": self.processed_tick_count,
            "ticks_per_second": self.processed_tick_count / elapsed if elapsed else 0,
            "backlog": self.tick_backlog(),
            "dropped_batches": self.dropped_tick_batches,
            "last_tick_received_at": self.last_tick_received_at,
            "last_tick_processed_at": self.last_tick_processed_at,
            "feed_status": self.feed_status,
            "last_feed_event": self.last_feed_event,
            "reconnect_attempts": self.feed_reconnect_attempts,
            "dispatcher_errors": self.dispatcher_error_count,
            "last_dispatcher_error": self.last_dispatcher_error,
        }

    def stop(self):
        for session in (self.live_paper_session, self.live_real_session):
            if session:
                session.close_session()
        self.stop_market_feed()
        self.running = False

    def square_off_open_position(self):
        session = self.live_real_session or self.live_paper_session
        if not session:
            return None
        return session.square_off_open_position()

    def activate_kill_switch(self, reason="Manual kill switch"):
        session = self.live_real_session or self.live_paper_session
        if not session:
            return None
        return session.activate_kill_switch(reason)

