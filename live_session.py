import os
import queue
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Protocol

import pandas as pd

from broker_reconciliation import BrokerReconciliationMixin
from candle_builder import CandleBuilder
from config import LOT_SIZE
from config_profile import apply_settings_profile
from engine import TradingEngine, append_datetime_index_key, attach_datetime_index_map, timestamp_key
from event_logger import (
    ENTRY_FILLED,
    LIVE_LATENCY_MEASURED,
    ORDER_CANCELLED,
    ORDER_COMPLETE,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    ORDER_REJECTED,
    PARTIAL_EXIT_DETECTED,
    PROTECTIVE_ORDER_VERIFICATION_FAILED,
    PROTECTIVE_ORDER_VERIFICATION_PASSED,
    PROTECTIVE_ORDER_PLACED,
    StructuredEventLogger,
)
from indicators import append_clean_candle, clean_and_add_indicators
from order_manager import ZerodhaOrderManager
from order_state import classify_order_state, normalize_order_status
from preflight import validate_live_preflight
from reporting import BufferedExcelWriter, format_datetime_value
from risk_runtime import RiskRuntimeMixin
from runtime_errors import classify_runtime_error
from risk_guard import LiveRiskGuard
from session_persistence import SessionPersistenceMixin
from session_audit import write_session_audit
from sqlite_store import AsyncTradingStore, TradingStore
from strategy import OPTION_ENTRY_REPORT_COLUMNS, append_option_formula_row, build_scoring_row, ensure_option_formula_columns
from trailing_safeguard import (
    build_safeguard_event,
    initial_trailing_safeguard_state,
    safeguard_prices,
    should_apply_trailing_safeguard,
    trailing_start_reached,
)
from trailing_stop import calculate_trailing_stop, trailing_settings
from zerodha_client import ZerodhaClient


class SessionEngine(Protocol):
    cooldown_until: int
    last_skip_reason: str

    def find_trade(self, nifty, options, i, settings) -> Any: ...

    def mark_trade_complete(self, exit_index) -> None: ...


class LivePaperSession(BrokerReconciliationMixin, RiskRuntimeMixin, SessionPersistenceMixin):
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
        self.engine: SessionEngine = TradingEngine(settings["cooldown"])
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
        self.session_start_balance = self.balance
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
        self.pending_entry_timeout_seconds = float(
            settings.get("buy_limit_validity_seconds", settings.get("pending_entry_timeout_seconds", 30)) or 30
        )
        self.order_monitor_stop = threading.Event()
        self.order_monitor_thread = None
        self.last_order_status_by_id = {}
        self.order_idempotency_records = {}
        self.order_idempotency_in_progress = set()
        self.duplicate_order_suppressed = 0
        self.active_orders = {}
        self.order_history = []
        self.latency_events = []
        self.protective_verification_events = []
        self.last_tick_batch_latency = {}
        self.last_candle_processing_latency = {}
        self.latest_live_trade = {}
        self.latest_ltp_by_option = {}
        self.latest_spread_by_option = {}
        self.entry_attempt_candle_keys = set()
        self.missed_limit_cooldown_until_by_option = {}
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
        self.stoploss_trades = self.risk_guard.stoploss_trades
        self.trading_blocked_reason = self.risk_guard.blocked_reason
        self.candle_log_path = (save_path or "").replace(".xlsx", "_candles.xlsx") if save_path else None
        self.audit_report_path = (save_path or "").replace(".xlsx", "_audit.json") if save_path else None
        self.excel_writer = BufferedExcelWriter(
            flush_interval=float(settings.get("excel_flush_interval", 1.0) or 1.0),
            max_batch_rows=int(settings.get("excel_batch_rows", 100) or 100),
        ) if save_path else None
        self.store = self._build_store(save_path)
        self.candle_persistence_queue = None
        self.candle_persistence_thread = None
        self.candle_persistence_lock = threading.Lock()
        self.candle_persistence_closed = False
        self.candle_persistence_stats = {
            "enabled": bool(self.store),
            "queue_size": 0,
            "enqueued": 0,
            "completed": 0,
            "dropped": 0,
            "errors": [],
        }
        self.candle_persistence_queue_size = max(
            1,
            int(settings.get("candle_persistence_queue_size", settings.get("sqlite_queue_size", 10000)) or 10000),
        )
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
            "Early Score": "",
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
                classification = classify_runtime_error(exc, context="order_status")
                self._log_event(
                    "ERROR",
                    "Order status monitor failed",
                    {
                        "error": str(exc),
                        "error_class": classification["class"],
                        "error_category": classification["category"],
                    },
                )

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
        batch_started = time.perf_counter()
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
                if str(name).startswith("OPTION_"):
                    try:
                        option_index = int(str(name).split("_")[1])
                        self.latest_ltp_by_option[option_index] = float(price)
                        spread = self._extract_bid_ask_spread(tick)
                        if spread is not None:
                            self.latest_spread_by_option[option_index] = spread
                    except (TypeError, ValueError):
                        pass
                if self.open_position and str(name) == f"OPTION_{self.open_position['option_index']}":
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
                candle_processing_started = time.perf_counter()
                self._process_completed_candles()
                self.last_candle_processing_latency = {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "duration_seconds": self._elapsed_seconds(candle_processing_started),
                    "last_candle_index": self.last_candle_index,
                }
                self._trim_live_candles_if_safe()
            elif self.settings.get("aggressive_live_entry_enabled"):
                self._process_live_forming_candle()
            self.last_tick_batch_latency = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ticks": len(ticks),
                "duration_seconds": self._elapsed_seconds(batch_started),
                "completed_candles": bool(completed_any),
            }

    def _append_completed_candle(self, name, row):
        if name == "NIFTY":
            if self._is_duplicate_or_old(self.nifty, row["datetime"]):
                return False
            self.nifty = append_clean_candle(self.nifty, row)
            append_datetime_index_key(self.nifty, row["datetime"])
            self._log_completed_candle(name, self.nifty.iloc[-1], self.nifty.attrs)
            return True
        if str(name).startswith("OPTION_"):
            idx = int(str(name).split("_")[1])
            if self._is_duplicate_or_old(self.options[idx], row["datetime"]):
                return False
            attrs = dict(self.options[idx].attrs)
            attrs.pop("_option_scoring_settings", None)
            option = append_clean_candle(self.options[idx], row)
            option = append_option_formula_row(option, self.settings)
            option.attrs.update(attrs)
            append_datetime_index_key(option, row["datetime"])
            self.options[idx] = option
            self._log_completed_candle(name, self.options[idx].iloc[-1], self.options[idx].attrs)
            return True
        return False

    def _log_completed_candle(self, name, row, attrs=None):
        if not self.store:
            return False
        metadata = {
            "instrument": (attrs or {}).get("instrument", "NIFTY" if name == "NIFTY" else ""),
            "tradingsymbol": (attrs or {}).get("tradingsymbol", (attrs or {}).get("instrument", "")),
            "option_type": (attrs or {}).get("option_type", ""),
        }
        return self._enqueue_candle_persistence(name, row, metadata)

    def _enqueue_candle_persistence(self, name, row, metadata):
        with self.candle_persistence_lock:
            if self.candle_persistence_closed:
                return False
            if self.candle_persistence_queue is None:
                self.candle_persistence_queue = queue.Queue(maxsize=self.candle_persistence_queue_size)
            if self.candle_persistence_thread is None or not self.candle_persistence_thread.is_alive():
                self.candle_persistence_thread = threading.Thread(
                    target=self._run_candle_persistence,
                    name="tradebot_candle_persistence",
                    daemon=True,
                )
                self.candle_persistence_thread.start()
        try:
            self.candle_persistence_queue.put_nowait(("candle", name, row, metadata, None))
            self.candle_persistence_stats["enqueued"] += 1
            self.candle_persistence_stats["queue_size"] = self.candle_persistence_queue.qsize()
            return True
        except queue.Full:
            self.candle_persistence_stats["dropped"] += 1
            self._remember_candle_persistence_error(name, "candle persistence queue full")
            return False

    def _run_candle_persistence(self):
        while True:
            kind, name, row, metadata, done = self.candle_persistence_queue.get()
            if kind == "close":
                if done:
                    done.set()
                break
            try:
                self.store.log_candle(name, row, metadata)
                self.candle_persistence_stats["completed"] += 1
            except Exception as exc:
                self._remember_candle_persistence_error(name, str(exc))
            finally:
                self.candle_persistence_stats["queue_size"] = self.candle_persistence_queue.qsize()

    def _remember_candle_persistence_error(self, stream_name, error):
        errors = list(self.candle_persistence_stats.get("errors") or [])
        errors.append({
            "stream_name": stream_name,
            "error": error,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.candle_persistence_stats["errors"] = errors[-5:]

    def _close_candle_persistence(self, timeout=10):
        with self.candle_persistence_lock:
            self.candle_persistence_closed = True
            thread = self.candle_persistence_thread
            candle_queue = self.candle_persistence_queue
        if thread is None or candle_queue is None or not thread.is_alive():
            return True
        done = threading.Event()
        try:
            candle_queue.put(("close", "", None, None, done), timeout=timeout)
        except queue.Full:
            self._remember_candle_persistence_error("", "candle persistence close queue full")
            return False
        completed = done.wait(timeout)
        thread.join(timeout=1)
        self.candle_persistence_stats["queue_size"] = candle_queue.qsize()
        return completed

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
            if self._trading_blocked():
                self._log_event("WARN", self.trading_blocked_reason)
                self.last_candle_index = i
                return
            entry_result = self._try_entry(i)
            if entry_result != "busy":
                self.last_candle_index = i
            self._log_candle(i)

    def _resolve_quantity(self, signal):
        tradingsymbol = signal.get("tradingsymbol") or signal.get("instrument")
        contract_lot_size = self.orders.lot_size(tradingsymbol)
        return self.lots * contract_lot_size, contract_lot_size

    def _place_order(self, side, signal, qty, order_type="MARKET", price=None, trigger_price=None):
        order_started = time.perf_counter()
        tradingsymbol = signal.get("tradingsymbol") or signal.get("instrument")
        product = self._order_product()
        order_type, price = self._maybe_live_option_market_entry_limit(
            side,
            signal,
            order_type,
            price,
            tradingsymbol,
        )
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
            max_attempts = self._order_placement_max_attempts()
            result = {}
            for attempt in range(1, max_attempts + 1):
                result = self.orders.place_order(
                    side,
                    tradingsymbol,
                    qty,
                    product=product,
                    order_type=order_type,
                    price=price,
                    trigger_price=trigger_price,
                )
                result["order_attempts"] = attempt
                if not str(result.get("status", "")).startswith("FAILED"):
                    break
                if result.get("requires_reconciliation", False):
                    break
                if not result.get("retriable", False):
                    break
                if attempt < max_attempts:
                    self._log_event(
                        "WARN",
                        f"{side} order placement failed; retrying",
                        {
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "error": result.get("error", ""),
                            "order_type": order_type,
                            "error_class": result.get("error_class", ""),
                            "idempotency_key": idempotency_key,
                        },
                    )
                    retry_delay = self._order_placement_retry_delay_seconds()
                    if retry_delay > 0:
                        time.sleep(retry_delay)
        finally:
            self.order_idempotency_in_progress.discard(idempotency_key)
        if not str(result.get("status", "")).startswith("FAILED"):
            self.order_idempotency_records[idempotency_key] = {
                "status": result["status"],
                "order_id": result["order_id"],
            }
        duration_seconds = self._elapsed_seconds(order_started)
        self._record_latency_event(
            "order_request",
            duration_seconds,
            {
                "side": side,
                "order_type": order_type,
                "product": product,
                "tradingsymbol": tradingsymbol,
                "quantity": qty,
                "status": result.get("status", ""),
                "order_id": result.get("order_id", ""),
                "error_class": result.get("error_class", ""),
                "error_category": result.get("error_category", ""),
                "attempts": result.get("order_attempts", 1),
            },
            log_event=True,
        )
        if result["log_status"]:
            self._log_order(result["order_id"], side, result["log_status"], result["log_data"])
        if result["error"]:
            if result.get("requires_reconciliation", False):
                kill_reason = (
                    f"UNKNOWN_BROKER_STATE during {side} {order_type} order placement; "
                    "manual reconciliation required before further trading"
                )
                self._emit_alert(
                    "ERROR",
                    "ORDER_UNKNOWN_BROKER_STATE",
                    f"{side} order failed with unknown broker state",
                    {
                        "error": result["error"],
                        "order_type": order_type,
                        "error_class": result.get("error_class", ""),
                        "error_category": result.get("error_category", ""),
                        "retriable": result.get("retriable", False),
                        "requires_reconciliation": True,
                        "idempotency_key": idempotency_key,
                        "tradingsymbol": tradingsymbol,
                        "quantity": qty,
                    },
                )
                self.activate_kill_switch(kill_reason)
            self._log_event(
                "ERROR",
                f"{side} order failed",
                {
                    "error": result["error"],
                    "order_type": order_type,
                    "error_class": result.get("error_class", ""),
                    "error_category": result.get("error_category", ""),
                    "retriable": result.get("retriable", False),
                    "requires_reconciliation": result.get("requires_reconciliation", False),
                    "idempotency_key": idempotency_key,
                    "attempts": result.get("order_attempts", 1),
                },
            )
        return result["status"], result["order_id"]

    def _order_placement_max_attempts(self):
        try:
            return max(1, int(self.settings.get("order_placement_max_attempts", 3) or 3))
        except (TypeError, ValueError):
            return 3

    def _order_placement_retry_delay_seconds(self):
        try:
            return max(0.0, float(self.settings.get("order_placement_retry_delay_seconds", 0.2) or 0))
        except (TypeError, ValueError):
            return 0.2

    def _maybe_live_option_market_entry_limit(self, side, signal, order_type, price, tradingsymbol):
        order_type = str(order_type or "MARKET").upper()
        side = str(side or "").upper()
        if (
            self.mode != "LIVE"
            or side != "BUY"
            or order_type != "MARKET"
            or not self._enabled("live_option_market_entry_as_limit_enabled")
            or not self._looks_like_option_symbol(tradingsymbol)
        ):
            return order_type, price

        ltp = self._latest_option_ltp(signal)
        if ltp is None:
            ltp = signal.get("entry")
        try:
            ltp = float(ltp)
        except (TypeError, ValueError):
            return order_type, price
        buffer_points = float(self.settings.get("live_option_market_entry_limit_buffer_points", 2) or 2)
        limit_price = self._round_price(ltp + max(buffer_points, self._price_tick()))
        signal["_live_entry_order_type_actual"] = "LIMIT"
        signal["_live_entry_limit_price"] = limit_price
        self._log_event(
            "INFO",
            "Live option market entry converted to aggressive LIMIT",
            {
                "instrument": tradingsymbol,
                "ltp": ltp,
                "buffer_points": buffer_points,
                "limit_price": limit_price,
            },
        )
        return "LIMIT", limit_price

    def _latest_option_ltp(self, signal):
        try:
            option_index = int(signal.get("option_index"))
        except (TypeError, ValueError):
            return None
        return self.latest_ltp_by_option.get(option_index)

    def _enabled(self, key):
        value = self.settings.get(key)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in ("1", "true", "yes", "on", "enabled")

    def _looks_like_option_symbol(self, tradingsymbol):
        symbol = str(tradingsymbol or "").strip().upper()
        return symbol.endswith(("CE", "PE")) and any(character.isdigit() for character in symbol)

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

    def _stoploss_trigger_price(self, entry_price):
        tick = self._price_tick()
        minimum_trigger = tick * 2 if self.mode == "LIVE" else tick
        raw_stoploss = float(entry_price) - float(self.settings["safety_points"])
        return self._round_price(max(raw_stoploss, minimum_trigger))

    def _stoploss_limit_price(self, trigger_price):
        trigger = self._round_price(trigger_price)
        tick = self._price_tick()
        buffer_points = max(float(self.settings.get("stoploss_limit_buffer_points", 2) or 2), tick)
        limit_price = self._round_price(trigger - buffer_points)
        if limit_price <= 0 or limit_price >= trigger:
            limit_price = self._round_price(max(trigger - tick, tick))
        return limit_price

    def _order_product(self):
        product = str(self.settings.get("order_product", "NRML") or "NRML").strip().upper()
        if product in ("MIS", "INTRADAY"):
            return "MIS"
        return "NRML"

    def _try_entry(self, i, current_candle_closed=True):
        if self.order_transition_in_progress or self.open_position or self.pending_entry:
            return "busy"
        decision_started = time.perf_counter()
        decision_settings = dict(self.settings)
        decision_settings["_fast_current_candle_closed"] = current_candle_closed
        decision_settings["_fast_ltp_by_option"] = dict(self.latest_ltp_by_option)
        decision_settings["_fast_spread_by_option"] = dict(self.latest_spread_by_option)
        signal_nifty, signal_options = self._frames_with_active_candles()
        signal = self.engine.find_trade(signal_nifty, signal_options, i, decision_settings)
        decision_seconds = self._elapsed_seconds(decision_started)
        if signal is None:
            return "no_signal"
        self._record_latency_event(
            "signal_generated",
            decision_seconds,
            {
                "nifty_index": i,
                "option_type": signal.get("type", ""),
                "instrument": signal.get("instrument", ""),
                "entry_index": signal.get("entry_index", ""),
                "signal_index": signal.get("signal_index", ""),
            },
            log_event=True,
        )
        attempt_error = self._entry_attempt_block_reason(signal)
        if attempt_error:
            self.engine.last_skip_reason = attempt_error
            self._log_event("WARN", attempt_error, {
                "instrument": signal.get("instrument", ""),
                "option_index": signal.get("option_index", ""),
                "signal_index": signal.get("signal_index", ""),
            })
            return "entry_attempt_blocked"
        validation_error = self._validate_entry(signal, i)
        if validation_error:
            self._record_rejected_entry(signal, i, validation_error)
            return "entry_rejected"
        qty, lot_size = self._resolve_quantity(signal)
        margin_error = self._validate_margin(signal, qty)
        if margin_error:
            self._record_rejected_entry(signal, i, margin_error)
            return "entry_rejected"
        entry_order_type = str(signal.get("entry_order_type", "MARKET") or "MARKET").upper()
        if entry_order_type == "LIMIT":
            return self._place_pending_limit_entry(signal, i, qty, lot_size)

        self.order_transition_in_progress = True
        try:
            entry_status, entry_order_id = self._place_order("BUY", signal, qty, order_type="MARKET")
            if entry_status.startswith("FAILED"):
                self._record_rejected_entry(signal, i, entry_status)
                return "order_failed"
            self._mark_entry_attempt(signal)
            if self.mode == "LIVE" and entry_order_id:
                actual_entry_order_type = signal.get("_live_entry_order_type_actual") or entry_order_type
                self._emit_order_event(
                    signal,
                    self.trade_count + 1,
                    "BUY",
                    entry_status,
                    order_id=entry_order_id,
                    order_type=actual_entry_order_type,
                    quantity=qty,
                    limit_price=signal.get("_live_entry_limit_price", "") if actual_entry_order_type == "LIMIT" else "",
                    remarks="Entry order placed",
                )
                details = self._wait_for_entry_execution(entry_order_id, signal, qty, signal["entry"])
                classification = details.get("classified_state") or classify_order_state(details, role="ENTRY")
                entry_state = classification["state"]
                if entry_state == "ENTRY_PARTIAL":
                    if not self._cancel_partial_market_entry_remainder(entry_order_id, signal, details, qty):
                        return "reconciliation_required"
                    entry_state = "ENTRY_CANCELLED_PARTIAL"
                if entry_state not in {"ENTRY_FILLED", "ENTRY_CANCELLED_PARTIAL"}:
                    if classification.get("requires_reconciliation"):
                        self.activate_kill_switch(
                            f"UNKNOWN_BROKER_STATE after entry order placement: {entry_order_id}"
                        )
                    self._record_rejected_entry(signal, i, f"ENTRY {entry_state}")
                    return "entry_rejected"
                entry_price = details.get("average_price") or signal["entry"]
                filled_qty = details.get("filled_quantity") or 0
            else:
                entry_price = self._actual_order_price(entry_order_id, signal["entry"])
                filled_qty = self._actual_order_quantity(entry_order_id, qty)
            self._open_position_from_fill(signal, lot_size, entry_order_id, entry_price, filled_qty)
            return "position_opened"
        finally:
            self.order_transition_in_progress = False

    def _wait_for_entry_execution(self, order_id, signal, quantity, fallback_price):
        timeout = float(self.settings.get("entry_order_fill_timeout_seconds", 5) or 5)
        poll_interval = max(0.2, min(self.order_status_poll_interval, 1.0))
        deadline = time.monotonic() + max(0.1, timeout)
        latest = self.orders.order_details(order_id, fallback_quantity=quantity, fallback_price=fallback_price)
        while True:
            classification = latest.get("classified_state") or classify_order_state(latest, role="ENTRY")
            latest["classified_state"] = classification
            self._record_order_status_change(
                order_id,
                latest.get("status", "UNKNOWN"),
                signal,
                "ENTRY ORDER STATUS",
                entry_order_id=order_id,
                quantity=quantity,
                lot_size=self.orders.lot_size(signal.get("instrument", "")),
                entry=fallback_price,
            )
            if classification["state"] in {
                "ENTRY_FILLED",
                "ENTRY_CANCELLED_PARTIAL",
                "ENTRY_REJECTED",
                "ENTRY_CANCELLED_EMPTY",
            }:
                return latest
            if time.monotonic() >= deadline:
                if classification["state"] == "ENTRY_PARTIAL":
                    return latest
                latest["classified_state"] = {
                    **classification,
                    "state": "UNKNOWN",
                    "requires_reconciliation": True,
                }
                return latest
            time.sleep(poll_interval)
            latest = self.orders.order_details(order_id, fallback_quantity=quantity, fallback_price=fallback_price)

    def _signal_candle_key(self, signal):
        option = signal.get("option")
        signal_index = signal.get("signal_index", signal.get("entry_index", ""))
        candle_key = ""
        if option is not None and signal_index not in ("", None) and int(signal_index) < len(option):
            candle_key = timestamp_key(option.iloc[int(signal_index)].get("datetime", ""))
        return (signal.get("option_index", ""), candle_key or signal_index)

    def _entry_attempt_block_reason(self, signal):
        option_index = signal.get("option_index")
        signal_index = int(signal.get("signal_index", signal.get("entry_index", 0)) or 0)
        cooldown_until = self.missed_limit_cooldown_until_by_option.get(option_index, -1)
        if cooldown_until >= signal_index:
            return "missed_limit_cooldown_active"
        if self.settings.get("one_entry_attempt_per_candle", True):
            key = self._signal_candle_key(signal)
            if key in self.entry_attempt_candle_keys:
                return "entry_attempt_already_used_for_candle"
        return ""

    def _mark_entry_attempt(self, signal):
        if self.settings.get("one_entry_attempt_per_candle", True):
            self.entry_attempt_candle_keys.add(self._signal_candle_key(signal))

    def _mark_missed_limit_cooldown(self, pending):
        if not pending:
            return
        signal = pending.get("signal", {})
        option_index = signal.get("option_index", pending.get("option_index", ""))
        signal_index = int(signal.get("signal_index", signal.get("entry_index", pending.get("placed_index", 0))) or 0)
        cooldown = int(self.settings.get("missed_limit_cooldown_candles", 0) or 0)
        if cooldown > 0:
            self.missed_limit_cooldown_until_by_option[option_index] = signal_index + cooldown

    def _cancel_partial_market_entry_remainder(self, order_id, signal, details, requested_qty):
        filled_qty = int(details.get("filled_quantity") or 0)
        pending_qty = int(details.get("pending_quantity") or max(int(requested_qty or 0) - filled_qty, 0))
        status = normalize_order_status(details.get("status", "UNKNOWN"))
        if filled_qty <= 0 or pending_qty <= 0 or status in {"CANCELLED", "REJECTED", "COMPLETE"}:
            return True

        cancel_reason = (
            f"PARTIAL MARKET ENTRY FILL: filled {filled_qty} of {requested_qty}; "
            "remaining quantity cancelled before opening protected position"
        )
        cancelled = self.orders.cancel_order(order_id)
        if not cancelled["cancelled"]:
            self.activate_kill_switch(
                f"Partial market entry fill detected but remaining order cancel failed: {cancelled['error']}"
            )
            return False
        self._log_order(order_id, "BUY", "PARTIAL MARKET ENTRY REMAINDER CANCELLED", {"reason": cancel_reason})
        self._emit_order_event(
            signal,
            self.trade_count + 1,
            "BUY",
            "CANCELLED",
            order_id=order_id,
            order_type="MARKET",
            quantity=requested_qty,
            entry_price=details.get("average_price") or signal.get("entry", ""),
            remarks=cancel_reason,
        )
        return True

    def _extract_bid_ask_spread(self, tick):
        depth = tick.get("depth") if isinstance(tick, dict) else None
        bid = ask = None
        if isinstance(depth, dict):
            buys = depth.get("buy") or []
            sells = depth.get("sell") or []
            if buys:
                bid = buys[0].get("price")
            if sells:
                ask = sells[0].get("price")
        bid = tick.get("bid_price", bid)
        ask = tick.get("ask_price", tick.get("offer_price", ask))
        try:
            if bid in ("", None) or ask in ("", None):
                return None
            spread = float(ask) - float(bid)
        except (TypeError, ValueError):
            return None
        return spread if spread >= 0 else None

    def _frames_with_active_candles(self):
        nifty = self._append_active_candle(self.nifty, "NIFTY")
        options = [
            self._append_active_candle(option, f"OPTION_{index}")
            for index, option in enumerate(self.options)
        ]
        return nifty, options

    def _append_active_candle(self, frame, key):
        active = self.candle_builder.snapshot(key)
        if not active or frame is None or frame.empty:
            return frame
        if "datetime" in frame.columns:
            latest = pd.to_datetime(frame["datetime"], errors="coerce").max()
            active_time = pd.to_datetime(active.get("datetime"), errors="coerce")
            if not pd.isna(latest) and not pd.isna(active_time) and active_time <= latest:
                return frame
        attrs = dict(frame.attrs)
        active_row = {column: active.get(column, "") for column in frame.columns}
        for column in ("datetime", "open", "high", "low", "close", "volume"):
            active_row[column] = active.get(column, active_row.get(column, ""))
        combined = pd.concat([frame, pd.DataFrame([active_row])], ignore_index=True)
        combined.attrs.update(attrs)
        if key == "NIFTY":
            combined = clean_and_add_indicators(combined)
            combined.attrs.update(attrs)
        return attach_datetime_index_map(combined)

    def _process_live_forming_candle(self):
        if (
            self.open_position is not None
            or self.pending_entry is not None
            or self.trade_count >= self.max_trades
            or self._trading_blocked()
        ):
            return
        active_index = len(self.nifty)
        if active_index <= 6:
            return
        self._try_entry(active_index, current_candle_closed=False)

    def _place_pending_limit_entry(self, signal, i, qty, lot_size):
        if self.order_transition_in_progress or self.open_position or self.pending_entry:
            return "busy"

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
                return "order_failed"

            self._mark_entry_attempt(signal)
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
            timer = threading.Timer(self.pending_entry_timeout_seconds, self._expire_pending_entry_order)
            timer.daemon = True
            self.pending_entry["timer"] = timer
            timer.start()
            return "pending_entry_placed"
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
        if self.mode != "LIVE":
            if self._paper_pending_entry_filled(pending):
                self._open_position_from_fill(
                    pending["signal"],
                    pending["contract_lot_size"],
                    pending["order_id"],
                    pending["limit_price"],
                    pending["quantity"],
                )
                self._cancel_pending_timer(pending)
                self.pending_entry = None
                self._clear_pending_entry()
                return
            elapsed = (datetime.now() - pending["placed_at"]).total_seconds()
            if elapsed < self.pending_entry_timeout_seconds and not force_timeout:
                return
            signal = dict(pending["signal"])
            signal["entry_order_id"] = pending["order_id"]
            self._record_rejected_entry(signal, i, "TIME EXHAUSTION CANCELLATION")
            self._mark_missed_limit_cooldown(pending)
            self._cancel_pending_timer(pending)
            self.pending_entry = None
            self._clear_pending_entry()
            self._emit_pending_entry_cancelled(pending, "TIME EXHAUSTION CANCELLATION")
            return
        status = self.orders.order_status(pending["order_id"], fallback="UNKNOWN")
        details = self.orders.order_details(
            pending["order_id"],
            fallback_quantity=pending.get("quantity", 0),
            fallback_price=pending.get("limit_price", 0),
        )
        classification = details.get("classified_state") or classify_order_state(details, role="ENTRY")
        entry_state = classification["state"]
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
        if entry_state == "ENTRY_FILLED":
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
        if entry_state in {"ENTRY_CANCELLED_EMPTY", "ENTRY_REJECTED"}:
            signal = dict(pending["signal"])
            signal["entry_order_id"] = pending["order_id"]
            self._record_rejected_entry(signal, i, f"ENTRY {entry_state}")
            self._mark_missed_limit_cooldown(pending)
            self._cancel_pending_timer(pending)
            self.pending_entry = None
            self._clear_pending_entry()
            return

        elapsed = (datetime.now() - pending["placed_at"]).total_seconds()
        if elapsed < self.pending_entry_timeout_seconds and not force_timeout:
            return

        cancel_status = "TIME EXHAUSTION CANCELLATION"
        cancel_confirmed = False
        if self.mode == "LIVE" and self.zerodha:
            cancelled = self.orders.cancel_order(pending["order_id"])
            if cancelled["cancelled"]:
                self._log_order(pending["order_id"], "BUY", "CANCELLED", {"reason": cancel_status})
                cancel_confirmed = True
            else:
                self._handle_pending_entry_cancel_not_confirmed(pending, i, cancel_status, cancelled)
                return
        signal = dict(pending["signal"])
        signal["entry_order_id"] = pending["order_id"]
        self._record_rejected_entry(signal, i, cancel_status)
        self._mark_missed_limit_cooldown(pending)
        self._cancel_pending_timer(pending)
        self.pending_entry = None
        self._clear_pending_entry()
        if cancel_confirmed:
            self._emit_pending_entry_cancelled(pending, cancel_status)

    def _emit_pending_entry_cancelled(self, pending, remarks, status="CANCELLED"):
        self._emit_order_event(
            pending.get("signal", {}),
            self.trade_count + 1,
            "BUY",
            status,
            order_id=pending.get("order_id", ""),
            order_type="LIMIT",
            quantity=pending.get("quantity", ""),
            limit_price=pending.get("limit_price", ""),
            remarks=remarks,
            keep_active=False,
        )

    def _handle_pending_entry_cancel_not_confirmed(self, pending, i, cancel_status, cancel_result):
        order_id = pending.get("order_id", "")
        status = cancel_result.get("status", "")
        details = self.orders.order_details(
            order_id,
            fallback_quantity=pending.get("quantity", 0),
            fallback_price=pending.get("limit_price", 0),
        )
        if status and normalize_order_status(details.get("status")) == "UNKNOWN":
            details["status"] = status
            details["classified_state"] = classify_order_state(details, role="ENTRY")
        classification = details.get("classified_state") or classify_order_state(details, role="ENTRY")
        entry_state = classification["state"]

        if self._handle_partial_pending_entry(pending, details, i, details.get("status") or status):
            return
        if entry_state == "ENTRY_FILLED":
            entry_price = details.get("average_price") or self._actual_order_price(order_id, pending["limit_price"])
            filled_qty = details.get("filled_quantity") or self._actual_order_quantity(order_id, pending["quantity"])
            self._open_position_from_fill(
                pending["signal"],
                pending["contract_lot_size"],
                order_id,
                entry_price,
                filled_qty,
            )
            self._cancel_pending_timer(pending)
            self.pending_entry = None
            self._clear_pending_entry()
            return
        if entry_state in {"ENTRY_CANCELLED_EMPTY", "ENTRY_REJECTED"}:
            signal = dict(pending["signal"])
            signal["entry_order_id"] = order_id
            terminal_status = details.get("status") or status or "CANCELLED"
            self._record_rejected_entry(signal, i, f"ENTRY {entry_state}")
            self._mark_missed_limit_cooldown(pending)
            self._cancel_pending_timer(pending)
            self.pending_entry = None
            self._clear_pending_entry()
            self._emit_pending_entry_cancelled(pending, cancel_status, status=terminal_status)
            return

        pending["cancel_requested_at"] = datetime.now()
        pending["cancel_status"] = status or entry_state
        self._save_pending_entry()
        message = (
            f"{cancel_status}: cancel not terminal; keeping pending entry under monitoring"
        )
        payload = {
            "order_id": order_id,
            "status": status,
            "entry_state": entry_state,
            "accepted": cancel_result.get("accepted", False),
            "resolved": cancel_result.get("resolved", False),
            "error": cancel_result.get("error", ""),
            "attempts": cancel_result.get("attempts", ""),
        }
        self._log_event("WARN" if cancel_result.get("accepted") else "ERROR", message, payload)
        if not cancel_result.get("accepted"):
            self.activate_kill_switch(
                f"UNKNOWN_CANCEL_STATE after pending entry timeout for {order_id}; manual reconciliation required"
            )
        self._emit_live_log_update(force=True)

    def _paper_pending_entry_filled(self, pending):
        current_ltp = self._current_ltp(pending.get("option_index"))
        if current_ltp in ("", None):
            return False
        try:
            return float(current_ltp) <= float(pending.get("limit_price", 0))
        except (TypeError, ValueError):
            return False

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
        protection_started = time.perf_counter()
        target = self._round_price(entry_price + float(self.settings["profit_points"]))
        stoploss = self._stoploss_trigger_price(entry_price)
        trailing_config = trailing_settings(self.settings)
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
            "initial_target_price": target,
            "initial_stoploss_price": stoploss,
            "current_sl_price": stoploss,
            "trailing_sl_enabled": trailing_config["enabled"],
            "trailing_start_points": trailing_config["start_points"],
            "trailing_step_points": trailing_config["step_points"],
            "trailing_lock_points": trailing_config["lock_points"],
            "last_trailing_level": 0,
            "trailing_modification_count": 0,
            "trailing_modifications": [],
            **initial_trailing_safeguard_state(signal, entry_price, self.settings),
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
                "trailing_sl_enabled": trailing_config["enabled"],
                "contract_lot_size": lot_size,
                "entry_order_type": signal.get("entry_order_type", "MARKET"),
            },
        )
        self._emit_order_event(
            signal,
            self.open_position["trade_no"],
            "BUY",
            "COMPLETE" if self.mode != "LIVE" else "COMPLETE",
            order_id=entry_order_id,
            order_type=signal.get("_live_entry_order_type_actual") or signal.get("entry_order_type", "MARKET"),
            quantity=filled_qty,
            entry_price=entry_price,
            limit_price=signal.get("_live_entry_limit_price", "") if signal.get("_live_entry_order_type_actual") == "LIMIT" else signal.get("entry", "") if signal.get("entry_order_type") == "LIMIT" else "",
            target_price=target,
            stoploss_price=stoploss,
            remarks=signal.get("entry_remark") or "BUY filled",
        )
        self._refresh_live_trade_snapshot()
        self._place_protective_exit_orders()
        self._record_latency_event(
            "entry_fill_to_protection_complete",
            self._elapsed_seconds(protection_started),
            {
                "trade_no": self.open_position.get("trade_no", ""),
                "instrument": signal.get("instrument", ""),
                "entry_order_id": entry_order_id,
                "target_order_id": self.open_position.get("target_order_id", ""),
                "stoploss_order_id": self.open_position.get("stoploss_order_id", ""),
                "quantity": filled_qty,
            },
            log_event=True,
        )
        self._save_open_position()

    def _place_protective_exit_orders(self):
        protection_started = time.perf_counter()
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
            order_type="SL",
            price=self._stoploss_limit_price(position["stoploss"]),
            trigger_price=position["stoploss"],
        )
        if stop_status.startswith("FAILED"):
            errors.append(f"stoploss order failed: {stop_status}")
        else:
            position["stoploss_order_id"] = stoploss_order_id
            self._record_exit_order_placed(
                position,
                "STOPLOSS SELL SL PLACED",
                stoploss_order_id,
                self._stoploss_limit_price(position["stoploss"]),
                position["stoploss"],
                "SELL SL",
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
        self._verify_protective_orders(position, errors)
        self._record_latency_event(
            "protective_order_pair",
            self._elapsed_seconds(protection_started),
            {
                "trade_no": position.get("trade_no", ""),
                "instrument": position.get("signal", {}).get("instrument", ""),
                "entry_order_id": position.get("entry_order_id", ""),
                "target_order_id": position.get("target_order_id", ""),
                "stoploss_order_id": position.get("stoploss_order_id", ""),
                "errors": list(errors),
            },
            log_event=True,
        )

    def _verify_protective_orders(self, position, placement_errors=None):
        if self.mode != "LIVE" or not self.zerodha or not position:
            return True
        placement_errors = list(placement_errors or [])
        target_order_id = str(position.get("target_order_id") or "")
        stoploss_order_id = str(position.get("stoploss_order_id") or "")
        target_status = self.last_order_status_by_id.get(target_order_id, "") if target_order_id else ""
        stoploss_status = self.last_order_status_by_id.get(stoploss_order_id, "") if stoploss_order_id else ""

        findings = []
        if placement_errors:
            findings.extend(placement_errors)
        if not target_order_id:
            findings.append("target order id missing")
        if not stoploss_order_id:
            findings.append("stoploss order id missing")
        if target_order_id and stoploss_order_id and target_order_id == stoploss_order_id:
            findings.append("target and stoploss share the same order id")
        if target_status and self._status_for_active_order(target_status) in {"REJECTED", "CANCELLED"}:
            findings.append(f"target order terminal status {target_status}")
        if stoploss_status and self._status_for_active_order(stoploss_status) in {"REJECTED", "CANCELLED"}:
            findings.append(f"stoploss order terminal status {stoploss_status}")

        payload = {
            "trade_no": position.get("trade_no", ""),
            "instrument": position.get("signal", {}).get("instrument", ""),
            "quantity": position.get("quantity", ""),
            "entry_order_id": position.get("entry_order_id", ""),
            "target_order_id": target_order_id,
            "target_status": target_status,
            "stoploss_order_id": stoploss_order_id,
            "stoploss_status": stoploss_status,
            "target_price": position.get("target", ""),
            "stoploss_price": position.get("stoploss", ""),
            "findings": findings,
        }
        if findings:
            message = "Protective order verification failed: " + "; ".join(str(item) for item in findings)
            self.protective_verification_events.append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "passed": False,
                **payload,
            })
            self.protective_verification_events = self.protective_verification_events[-50:]
            self._emit_alert("CRITICAL", PROTECTIVE_ORDER_VERIFICATION_FAILED, message, payload)
            self._log_lifecycle_event(
                PROTECTIVE_ORDER_VERIFICATION_FAILED,
                "CRITICAL",
                message,
                trade_no=position.get("trade_no", ""),
                status="FAILED",
                side="SELL",
                instrument=position.get("signal", {}).get("instrument", ""),
                quantity=position.get("quantity", ""),
                payload=payload,
            )
            return False

        message = "Protective order verification passed"
        self.protective_verification_events.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "passed": True,
            **payload,
        })
        self.protective_verification_events = self.protective_verification_events[-50:]
        self._log_lifecycle_event(
            PROTECTIVE_ORDER_VERIFICATION_PASSED,
            "INFO",
            message,
            trade_no=position.get("trade_no", ""),
            status="PASSED",
            side="SELL",
            instrument=position.get("signal", {}).get("instrument", ""),
            quantity=position.get("quantity", ""),
            payload=payload,
        )
        return True

    def _validate_entry(self, signal, i):
        if i >= len(self.nifty):
            return "ENTRY REJECTED: NIFTY candle missing"
        option_index = signal.get("option_index")
        if option_index is None or option_index >= len(self.options):
            return "ENTRY REJECTED: option data missing"
        if signal["entry_index"] >= len(signal["option"]):
            return "ENTRY REJECTED: option entry candle missing"
        if self.mode == "LIVE" and str(self.settings.get("enforce_market_hours", "1")).lower() not in ("0", "false", "no"):
            now = datetime.now().time()
            if now < datetime.strptime("09:15", "%H:%M").time() or now > datetime.strptime("15:30", "%H:%M").time():
                return "ENTRY REJECTED: outside market hours"
        return ""

    def _validate_margin(self, signal, qty):
        if self.mode == "PAPER":
            return self._validate_paper_balance(signal, qty)
        if self.mode == "LIVE":
            return self._validate_live_margin(signal, qty)
        return ""

    def _validate_paper_balance(self, signal, qty):
        required = float(signal["entry"]) * int(qty)
        if self.balance < required:
            return f"ENTRY REJECTED: insufficient paper balance available={self.balance:.2f} required={required:.2f}"
        return ""

    def _validate_live_margin(self, signal, qty):
        if not self.zerodha:
            return ""
        if str(self.settings.get("check_margin", "1")).lower() in ("0", "false", "no"):
            return ""
        try:
            available = self.orders.available_margin()
        except Exception as exc:
            error = str(exc)
            classification = classify_runtime_error(exc, context="margin")
            self._log_event(
                "ERROR",
                "Margin check failed",
                {
                    "error": error,
                    "error_class": classification["class"],
                    "error_category": classification["category"],
                },
            )
            return f"ENTRY REJECTED: live margin check failed error={error}"
        if available is None:
            return "ENTRY REJECTED: live margin unavailable"
        try:
            available = float(available)
        except (TypeError, ValueError):
            return f"ENTRY REJECTED: invalid live margin value={available}"
        required = float(signal["entry"]) * int(qty)
        if available < required:
            return f"ENTRY REJECTED: insufficient margin available={available:.2f} required={required:.2f}"
        return ""

    def _apply_trailing_stop(self, current, timestamp=None):
        position = self.open_position
        if not position or not position.get("trailing_sl_enabled"):
            return False
        if trailing_start_reached(position.get("entry_price", 0), current, self.settings):
            position["trailing_start_reached"] = True
        current_sl = float(position.get("current_sl_price", position.get("stoploss", 0)) or 0)
        update = calculate_trailing_stop(position.get("entry_price", 0), current_sl, current, self.settings)
        if not update:
            return False
        trailing_level = float(update["trailing_level"])
        if trailing_level <= float(position.get("last_trailing_level", 0) or 0):
            return False

        old_sl = current_sl
        new_sl = self._round_price(update["new_sl_price"])
        if new_sl <= old_sl:
            return False

        status = "PAPER"
        modify_status = "PAPER"
        if self.mode == "LIVE":
            stoploss_order_id = position.get("stoploss_order_id")
            if not stoploss_order_id:
                return False
            status = self.orders.order_status(stoploss_order_id, fallback="UNKNOWN")
            if status not in {
                "OPEN",
                "TRIGGER PENDING",
                "PENDING",
                "OPEN PENDING",
                "MODIFY PENDING",
                "MODIFY VALIDATION PENDING",
                "VALIDATION PENDING",
                "PUT ORDER REQ RECEIVED",
            }:
                return False
            sl_limit_price = self._stoploss_limit_price(new_sl)
            result = self.orders.modify_stoploss_trigger(
                stoploss_order_id,
                new_sl,
                quantity=position.get("quantity", ""),
                price=sl_limit_price,
                order_type="SL",
            )
            modify_status = result.get("status") or ("MODIFIED" if result.get("modified") else "FAILED")
            if not result.get("modified"):
                self._record_trailing_modification(position, old_sl, new_sl, current, update, modify_status, timestamp)
                self._log_event(
                    "ERROR",
                    "Trailing stoploss modification failed",
                    {
                        "order_id": stoploss_order_id,
                        "old_sl_price": old_sl,
                        "new_sl_price": new_sl,
                        "error": result.get("error", ""),
                        "error_class": result.get("error_class", ""),
                        "error_category": result.get("error_category", ""),
                    },
                )
                return False

        position["current_sl_price"] = new_sl
        position["stoploss"] = new_sl
        position["trailing_start_reached"] = True
        position["last_trailing_level"] = trailing_level
        position["trailing_modification_count"] = int(position.get("trailing_modification_count", 0) or 0) + 1
        self._record_trailing_modification(position, old_sl, new_sl, current, update, modify_status, timestamp)
        self._save_open_position()
        self._emit_order_event(
            position["signal"],
            position.get("trade_no", self.trade_count + 1),
            "SELL",
            "MODIFIED" if self.mode == "LIVE" else "PAPER",
            order_id=position.get("stoploss_order_id", ""),
            order_type="SL",
            quantity=position.get("quantity", ""),
            entry_price=position.get("entry_price", ""),
            limit_price=self._stoploss_limit_price(new_sl),
            trigger_price=new_sl,
            target_price=position.get("target", ""),
            stoploss_price=new_sl,
            exit_reason="TRAILING STOPLOSS MODIFIED",
            remarks=f"Trailing SL moved from {old_sl} to {new_sl}",
            parent_order_id=position.get("entry_order_id", ""),
            timestamp=timestamp,
        )
        return True

    def _record_trailing_modification(self, position, old_sl, new_sl, current, update, modify_status, timestamp=None):
        event = {
            "timestamp": format_datetime_value(timestamp or datetime.now()),
            "old_sl_price": old_sl,
            "new_sl_price": new_sl,
            "ltp_at_modification": current,
            "unrealized_profit_points": update.get("profit", ""),
            "trailing_level": update.get("trailing_level", ""),
            "modify_status": modify_status,
        }
        modifications = list(position.get("trailing_modifications") or [])
        modifications.append(event)
        position["trailing_modifications"] = modifications
        self._log_event(
            "INFO" if modify_status in {"PAPER", "MODIFIED"} else "ERROR",
            "Trailing stoploss update",
            {
                "trade_no": position.get("trade_no", ""),
                "instrument": position.get("signal", {}).get("instrument", ""),
                **event,
            },
        )

    def _modifiable_exit_order_statuses(self):
        return {
            "OPEN",
            "TRIGGER PENDING",
            "PENDING",
            "OPEN PENDING",
            "MODIFY PENDING",
            "MODIFY VALIDATION PENDING",
            "VALIDATION PENDING",
            "PUT ORDER REQ RECEIVED",
        }

    def _apply_trailing_time_safeguard(self, current, current_index, timestamp=None):
        position = self.open_position
        if not position or not position.get("trailing_time_safeguard_enabled"):
            return False
        if trailing_start_reached(position.get("entry_price", 0), current, self.settings):
            position["trailing_start_reached"] = True
            return False
        if not should_apply_trailing_safeguard(position, current_index):
            return False

        old_target = position.get("target", "")
        old_sl = position.get("stoploss", position.get("current_sl_price", ""))
        minimum_trigger = self._price_tick() if self.mode != "LIVE" else self._price_tick() * 2
        new_target, new_sl = safeguard_prices(
            position.get("entry_price", 0),
            self.settings,
            round_price=self._round_price,
            minimum_price=minimum_trigger,
        )
        sl_limit_price = self._stoploss_limit_price(new_sl)
        modify_status = "MODIFIED"

        if self.mode == "LIVE":
            target_order_id = position.get("target_order_id")
            stoploss_order_id = position.get("stoploss_order_id")
            if not target_order_id or not stoploss_order_id:
                return False
            modifiable = self._modifiable_exit_order_statuses()
            target_status = self.orders.order_status(target_order_id, fallback="UNKNOWN")
            stoploss_status = self.orders.order_status(stoploss_order_id, fallback="UNKNOWN")
            if target_status not in modifiable or stoploss_status not in modifiable:
                return False

            target_result = self.orders.modify_limit_price(
                target_order_id,
                new_target,
                quantity=position.get("quantity", ""),
            )
            stoploss_result = self.orders.modify_stoploss_trigger(
                stoploss_order_id,
                new_sl,
                quantity=position.get("quantity", ""),
                price=sl_limit_price,
                order_type="SL",
            )
            if not target_result.get("modified") or not stoploss_result.get("modified"):
                self._log_event(
                    "ERROR",
                    "Trailing time safeguard modification failed",
                    {
                        "target_order_id": target_order_id,
                        "stoploss_order_id": stoploss_order_id,
                        "target_error": target_result.get("error", ""),
                        "stoploss_error": stoploss_result.get("error", ""),
                    },
                )
                return False

        position["target"] = new_target
        position["stoploss"] = new_sl
        position["current_sl_price"] = new_sl
        position["trailing_time_safeguard_applied"] = True
        event = build_safeguard_event(
            format_datetime_value(timestamp or datetime.now()),
            old_target,
            new_target,
            old_sl,
            new_sl,
            current,
            current_index,
            modify_status,
        )
        modifications = list(position.get("trailing_time_safeguard_modifications") or [])
        modifications.append(event)
        position["trailing_time_safeguard_modifications"] = modifications
        self._log_event(
            "INFO",
            "Trailing time safeguard update",
            {
                "trade_no": position.get("trade_no", ""),
                "instrument": position.get("signal", {}).get("instrument", ""),
                **event,
            },
        )
        self._save_open_position()
        self._emit_order_event(
            position["signal"],
            position.get("trade_no", self.trade_count + 1),
            "SELL",
            modify_status,
            order_id=position.get("target_order_id", ""),
            order_type="LIMIT",
            quantity=position.get("quantity", ""),
            entry_price=position.get("entry_price", ""),
            limit_price=new_target,
            target_price=new_target,
            stoploss_price=new_sl,
            exit_reason="TRAILING TIME SAFEGUARD MODIFIED",
            remarks=f"Target tightened from {old_target} to {new_target}",
            parent_order_id=position.get("entry_order_id", ""),
            timestamp=timestamp,
        )
        self._emit_order_event(
            position["signal"],
            position.get("trade_no", self.trade_count + 1),
            "SELL",
            modify_status,
            order_id=position.get("stoploss_order_id", ""),
            order_type="SL",
            quantity=position.get("quantity", ""),
            entry_price=position.get("entry_price", ""),
            limit_price=sl_limit_price,
            trigger_price=new_sl,
            target_price=new_target,
            stoploss_price=new_sl,
            exit_reason="TRAILING TIME SAFEGUARD MODIFIED",
            remarks=f"Stoploss tightened from {old_sl} to {new_sl}",
            parent_order_id=position.get("entry_order_id", ""),
            timestamp=timestamp,
        )
        return True

    def _check_live_exit(self, i):
        position = self.open_position
        if not position:
            return
        if self._check_protective_exit_orders(i):
            return
        position = self.open_position
        if not position:
            return
        option = self.options[position["option_index"]]
        if i >= len(option):
            return
        current = float(option.iloc[i]["close"])
        position["peak_price"] = max(position["peak_price"], current)
        self._apply_trailing_stop(current, self._row_time(option, i))
        self._apply_trailing_time_safeguard(current, i, self._row_time(option, i))
        elapsed = i - position["entry_index"]
        reason = None
        has_target_order = self.mode == "LIVE" and bool(position.get("target_order_id"))
        has_stoploss_order = self.mode == "LIVE" and bool(position.get("stoploss_order_id"))
        if current >= position["target"] and not has_target_order:
            reason = "TARGET"
        elif current <= position["stoploss"] and not has_stoploss_order:
            reason = "TRAILING_STOPLOSS" if float(position.get("stoploss", 0) or 0) > float(position.get("initial_stoploss_price", 0) or 0) else "STOPLOSS"
        else:
            if elapsed >= int(self.settings.get("time_exit", 10)):
                reason = reason or "TIME_EXIT"

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
        self._apply_trailing_stop(current, timestamp)
        self._apply_trailing_time_safeguard(current, self._current_position_index(position), timestamp)
        has_target_order = self.mode == "LIVE" and bool(position.get("target_order_id"))
        has_stoploss_order = self.mode == "LIVE" and bool(position.get("stoploss_order_id"))
        if current >= position["target"] and not has_target_order:
            self._close_position(len(position["signal"]["option"]) - 1, "TARGET", current, timestamp)
        elif current <= position["stoploss"] and not has_stoploss_order:
            reason = "TRAILING_STOPLOSS" if float(position.get("stoploss", 0) or 0) > float(position.get("initial_stoploss_price", 0) or 0) else "STOPLOSS"
            self._close_position(len(position["signal"]["option"]) - 1, reason, current, timestamp)

    def _current_position_index(self, position):
        try:
            option = self.options[int(position.get("option_index", 0))]
            return len(option) - 1
        except (TypeError, ValueError, IndexError):
            return len(position.get("signal", {}).get("option", [])) - 1

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
        target_classification = (
            classify_order_state(target_details, role="EXIT") if target_details else {"state": "UNKNOWN"}
        )
        stoploss_classification = (
            classify_order_state(stoploss_details, role="EXIT") if stoploss_details else {"state": "UNKNOWN"}
        )
        target_state = target_classification["state"]
        stoploss_state = stoploss_classification["state"]

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

        if target_state == "EXIT_FILLED" and stoploss_state == "EXIT_FILLED":
            reason = "CRITICAL: target and stoploss both completed; broker position reconciliation required"
            self.activate_kill_switch(reason)
            self._log_event("CRITICAL", reason, {
                "target_order_id": target_order_id,
                "stoploss_order_id": stoploss_order_id,
                "target_status": target_status,
                "stoploss_status": stoploss_status,
            })
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

        if target_state == "EXIT_FILLED":
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

        if stoploss_state == "EXIT_FILLED":
            stoploss_reason = (
                "TRAILING_STOPLOSS"
                if float(position.get("stoploss", 0) or 0) > float(position.get("initial_stoploss_price", 0) or 0)
                else "STOPLOSS"
            )
            self._cancel_exit_order(target_order_id, "TARGET", "STOPLOSS FILLED")
            self._finalize_position_from_exit_order(
                i,
                stoploss_reason,
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
            order_type="LIMIT" if label == "TARGET" else "SL",
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
                    order_type="LIMIT" if label == "TARGET" else "SL",
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
            if cancelled.get("accepted") and not cancelled.get("resolved"):
                self._log_event(
                    "WARN",
                    f"{label} cancel accepted but final status is not terminal yet",
                    {
                        "order_id": order_id,
                        "status": cancelled.get("status", ""),
                        "attempts": cancelled.get("attempts", ""),
                        "reason": reason,
                    },
                )
            elif cancelled.get("resolved"):
                self._log_event(
                    "INFO",
                    f"{label} cancel resolved by broker status refresh",
                    {
                        "order_id": order_id,
                        "status": cancelled.get("status", ""),
                        "reason": reason,
                    },
                )
            else:
                self._log_event(
                    "ERROR",
                    f"{label} cancel failed",
                    {
                        "order_id": order_id,
                        "error": cancelled.get("error", ""),
                        "error_class": cancelled.get("error_class", ""),
                        "error_category": cancelled.get("error_category", ""),
                        "status": cancelled.get("status", ""),
                        "attempts": cancelled.get("attempts", ""),
                    },
                )

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
            exit_order_type, simulated_limit_price, simulated_trigger_price = self._simulated_close_order_fields(
                position,
                reason,
                fallback_exit_price,
            )
            order_price = simulated_limit_price if simulated_limit_price not in ("", None) else None
            order_trigger_price = simulated_trigger_price if simulated_trigger_price not in ("", None) else None
            if reason not in {"TIME_EXIT"}:
                order_price = None
                order_trigger_price = None
            exit_status, exit_order_id = self._place_order(
                "SELL",
                position["signal"],
                position["quantity"],
                order_type=exit_order_type if reason == "TIME_EXIT" else "MARKET",
                price=order_price,
                trigger_price=order_trigger_price,
            )
            if exit_status.startswith("FAILED"):
                if position.get("last_exit_failure_status") != exit_status:
                    position["last_exit_failure_status"] = exit_status
                    self._save_open_position()
                    self._record_exit_failure(
                        position,
                        i,
                        reason,
                        exit_status,
                        order_type=exit_order_type,
                        limit_price=simulated_limit_price,
                        trigger_price=simulated_trigger_price,
                    )
                return None
            exit_price = self._actual_order_price(exit_order_id, fallback_exit_price)
            exit_quantity = self._actual_order_quantity(exit_order_id, position["quantity"])
        finally:
            self.order_transition_in_progress = False
        pnl = (exit_price - position["entry_price"]) * exit_quantity
        self.balance += pnl
        self.risk_guard.record_trade_result(pnl, reason)
        self._sync_risk_state_from_guard()
        trade = {
            "Trade No": position["trade_no"],
            "trade_id": f"{self.session_id}_{position['trade_no']}",
            "mode": "LIVE_ZERODHA" if self.mode == "LIVE" else self.mode,
            "Type": position["signal"]["type"],
            "Instrument": position["signal"]["instrument"],
            "symbol": position["signal"]["instrument"],
            "tradingsymbol": position["signal"].get("tradingsymbol", position["signal"]["instrument"]),
            "option_type": position["signal"]["type"],
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
            "buy_order_id": position["entry_order_id"],
            "target_order_id": position.get("target_order_id", ""),
            "stoploss_order_id": position.get("stoploss_order_id", ""),
            "stoploss_order_type": "SL" if self.mode == "LIVE" or (self.mode != "LIVE" and reason in {"STOPLOSS", "TRAILING_STOPLOSS"}) else "",
            "slm_order_id": position.get("stoploss_order_id", ""),
            "final_pnl": pnl,
            "order_cancel_status": "not_required" if self.mode != "LIVE" else "",
            "Early Score": position["signal"].get("score_row", {}).get("Early Score", ""),
            "Buy Entry": position["signal"].get("score_row", {}).get("Buy Entry", ""),
            "Entry Remark": position["signal"].get("entry_remark", ""),
        }
        trade.update(self._trailing_trade_fields(position))
        for column in OPTION_ENTRY_REPORT_COLUMNS:
            trade.setdefault(column, position["signal"].get("score_row", {}).get(column, ""))
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
            order_type=exit_order_type,
            quantity=exit_quantity,
            entry_price=position["entry_price"],
            exit_price=exit_price,
            limit_price=simulated_limit_price,
            trigger_price=simulated_trigger_price,
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

    def _simulated_close_order_fields(self, position, reason, fallback_exit_price=None):
        if reason == "TIME_EXIT":
            return self._time_exit_close_order_fields(fallback_exit_price)
        if self.mode == "LIVE" or reason not in {"STOPLOSS", "TRAILING_STOPLOSS"}:
            return "MARKET", "", ""
        trigger_price = position.get("stoploss", "")
        limit_price = self._stoploss_limit_price(trigger_price) if trigger_price not in ("", None) else ""
        return "SL", limit_price, trigger_price

    def _time_exit_close_order_fields(self, fallback_exit_price):
        trigger_price = self._round_price(fallback_exit_price)
        limit_price = self._stoploss_limit_price(trigger_price)
        return "SL", limit_price, trigger_price

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
        self.risk_guard.record_trade_result(pnl, reason)
        self._sync_risk_state_from_guard()
        trade = {
            "Trade No": position["trade_no"],
            "trade_id": f"{self.session_id}_{position['trade_no']}",
            "mode": "LIVE_ZERODHA" if self.mode == "LIVE" else self.mode,
            "Type": position["signal"]["type"],
            "Instrument": position["signal"]["instrument"],
            "symbol": position["signal"]["instrument"],
            "tradingsymbol": position["signal"].get("tradingsymbol", position["signal"]["instrument"]),
            "option_type": position["signal"]["type"],
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
            "buy_order_id": position["entry_order_id"],
            "target_order_id": position.get("target_order_id", ""),
            "stoploss_order_id": position.get("stoploss_order_id", ""),
            "stoploss_order_type": "SL" if self.mode == "LIVE" else "",
            "slm_order_id": position.get("stoploss_order_id", ""),
            "final_pnl": pnl,
            "order_cancel_status": "not_required" if self.mode != "LIVE" else "",
            "Early Score": position["signal"].get("score_row", {}).get("Early Score", ""),
            "Buy Entry": position["signal"].get("score_row", {}).get("Buy Entry", ""),
            "Entry Remark": position["signal"].get("entry_remark", ""),
        }
        trade.update(self._trailing_trade_fields(position))
        for column in OPTION_ENTRY_REPORT_COLUMNS:
            trade.setdefault(column, position["signal"].get("score_row", {}).get(column, ""))
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
            order_type="LIMIT" if reason == "TARGET" else ("SL" if reason in {"STOPLOSS", "TRAILING_STOPLOSS"} else "MARKET"),
            quantity=exit_quantity,
            entry_price=position["entry_price"],
            exit_price=exit_price,
            limit_price=position.get("target", "") if reason == "TARGET" else "",
            trigger_price=position.get("stoploss", "") if reason in {"STOPLOSS", "TRAILING_STOPLOSS"} else "",
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

    def _trailing_trade_fields(self, position):
        return {
            "initial_target_price": position.get("initial_target_price", position.get("target", "")),
            "initial_stoploss_price": position.get("initial_stoploss_price", position.get("stoploss", "")),
            "current_sl_price": position.get("current_sl_price", position.get("stoploss", "")),
            "trailing_sl_enabled": position.get("trailing_sl_enabled", False),
            "trailing_start_points": position.get("trailing_start_points", ""),
            "trailing_step_points": position.get("trailing_step_points", ""),
            "trailing_lock_points": position.get("trailing_lock_points", ""),
            "trailing_modifications": list(position.get("trailing_modifications") or []),
            "trailing_start_reached": position.get("trailing_start_reached", False),
            "trailing_time_safeguard_enabled": position.get("trailing_time_safeguard_enabled", False),
            "trailing_time_safeguard_applied": position.get("trailing_time_safeguard_applied", False),
            "trailing_time_safeguard_candles": position.get("trailing_time_safeguard_candles", ""),
            "trailing_time_safeguard_target_points": position.get("trailing_time_safeguard_target_points", ""),
            "trailing_time_safeguard_stoploss_points": position.get("trailing_time_safeguard_stoploss_points", ""),
            "trailing_time_safeguard_modifications": list(position.get("trailing_time_safeguard_modifications") or []),
        }

    def _record_exit_failure(self, position, i, reason, status, order_type="MARKET", limit_price="", trigger_price=""):
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
            "Early Score": position["signal"].get("score_row", {}).get("Early Score", ""),
            "Buy Entry": position["signal"].get("score_row", {}).get("Buy Entry", ""),
            "Entry Remark": position["signal"].get("entry_remark", ""),
        }
        for column in OPTION_ENTRY_REPORT_COLUMNS:
            trade.setdefault(column, position["signal"].get("score_row", {}).get(column, ""))
        self._append_excel(self.save_path, [trade])
        self._log_trade(trade)
        self._emit_order_event(
            position["signal"],
            position["trade_no"],
            "SELL",
            status,
            order_id="",
            order_type=order_type,
            quantity=position.get("quantity", ""),
            entry_price=position.get("entry_price", ""),
            exit_price="",
            limit_price=limit_price,
            trigger_price=trigger_price,
            exit_reason=f"EXIT FAILED: {reason}",
            remarks=status,
            parent_order_id=position.get("entry_order_id", ""),
        )
        if self.on_trade:
            self.on_trade(trade, self.balance)

    def _record_pending_entry_order(self, signal, i, status, order_id, qty, lot_size):
        zerodha_status = self._zerodha_order_status(order_id, fallback="OPEN")
        self.last_order_status_by_id[str(order_id)] = zerodha_status
        entry_remark = signal.get("entry_remark")
        pending_remark = f"Pending BUY limit at {signal.get('entry', '')}"
        if entry_remark:
            pending_remark = f"{entry_remark}; {pending_remark}"
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
            "Remarks": pending_remark,
            "Order Status": zerodha_status,
            "Zerodha Order Status": zerodha_status,
            "Entry Order ID": order_id,
            "Exit Order ID": "",
            "Early Score": signal.get("score_row", {}).get("Early Score", ""),
            "Buy Entry": signal.get("score_row", {}).get("Buy Entry", ""),
            "Entry Remark": signal.get("entry_remark", ""),
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
            remarks=pending_remark,
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
            "Early Score": position["signal"].get("score_row", {}).get("Early Score", ""),
            "Buy Entry": position["signal"].get("score_row", {}).get("Buy Entry", ""),
            "Entry Remark": position["signal"].get("entry_remark", ""),
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
            order_type="LIMIT" if "LIMIT" in order_kind else ("SL" if "SL" in order_kind else "MARKET"),
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
        order_id: Any,
        status,
        signal,
        reason,
        entry_order_id="",
        exit_order_id: Any = "",
        quantity: Any = "",
        lot_size: Any = "",
        entry: Any = "",
        exit_price: Any = "",
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
            "Early Score": signal.get("score_row", {}).get("Early Score", ""),
            "Buy Entry": signal.get("score_row", {}).get("Buy Entry", ""),
            "Entry Remark": signal.get("entry_remark", ""),
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
            "Early Score": signal.get("score_row", {}).get("Early Score", ""),
            "Buy Entry": signal.get("score_row", {}).get("Buy Entry", ""),
            "Entry Remark": signal.get("entry_remark", ""),
        }
        for column in OPTION_ENTRY_REPORT_COLUMNS:
            trade.setdefault(column, signal.get("score_row", {}).get(column, ""))
        self.trades.append(trade)
        self._append_excel(self.save_path, [trade])
        self._log_trade(trade)
        if self.on_trade:
            self.on_trade(trade, self.balance)

    def _elapsed_seconds(self, started_at):
        return max(time.perf_counter() - started_at, 0.0)

    def _record_latency_event(self, stage, duration_seconds, payload=None, log_event=False):
        event = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stage": str(stage or ""),
            "duration_seconds": float(duration_seconds or 0.0),
            "payload": payload or {},
        }
        self.latency_events.append(event)
        self.latency_events = self.latency_events[-100:]
        if log_event:
            self._log_lifecycle_event(
                LIVE_LATENCY_MEASURED,
                "INFO",
                f"Live latency measured: {event['stage']} {event['duration_seconds']:.6f}s",
                order_id=(payload or {}).get("order_id", ""),
                trade_no=(payload or {}).get("trade_no", ""),
                status=(payload or {}).get("status", ""),
                side=(payload or {}).get("side", ""),
                instrument=(payload or {}).get("instrument", "") or (payload or {}).get("tradingsymbol", ""),
                quantity=(payload or {}).get("quantity"),
                payload=event,
            )
        return event

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
        trade_no: Any = "",
        status="",
        side="",
        instrument="",
        quantity: Any = None,
        payload: Any = None,
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
        text = normalize_order_status(status)
        if text in {
            "OPEN",
            "PENDING",
            "OPEN PENDING",
            "MODIFY PENDING",
            "MODIFY VALIDATION PENDING",
            "CANCEL PENDING",
            "VALIDATION PENDING",
            "PUT ORDER REQ RECEIVED",
        }:
            return "OPEN"
        if text == "TRIGGER PENDING":
            return "TRIGGER PENDING"
        if text == "COMPLETE":
            return "COMPLETE"
        if text == "REJECTED":
            return "REJECTED"
        if text == "CANCELLED":
            return "CANCELLED"
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
        trade_no: Any,
        side,
        status,
        order_id="",
        order_type="MARKET",
        quantity: Any = "",
        entry_price: Any = "",
        exit_price: Any = "",
        limit_price: Any = "",
        trigger_price: Any = "",
        target_price: Any = "",
        stoploss_price: Any = "",
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
        score_row = signal.get("score_row", {})
        early_score = score_row.get("Early Score", "")
        sell_score = score_row.get("Sell Score", "")
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
            "Early Score": early_score,
            "Entry Type": score_row.get("Entry Type", signal.get("entry_type", "")),
            "Final Decision": score_row.get("Final Decision", ""),
            "Decision Reason": score_row.get("Decision Reason", ""),
            "Rejection Reason": score_row.get("Rejection Reason", ""),
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
            "Early Score": early_score if side == "BUY" else "",
            "Entry Type": score_row.get("Entry Type", signal.get("entry_type", "")) if side == "BUY" else "",
            "Final Decision": score_row.get("Final Decision", "") if side == "BUY" else "",
            "Decision Reason": score_row.get("Decision Reason", "") if side == "BUY" else "",
            "Rejection Reason": score_row.get("Rejection Reason", "") if side == "BUY" else "",
            "Exit Price": exit_price if side == "SELL" else "",
            "Limit Price": limit_price,
            "Trigger Price": trigger_price,
            "Exit Reason": exit_reason if side == "SELL" else "",
            "Target Price": target_price,
            "Stop Loss Price": stoploss_price,
            "LTP at Order Placement": ltp,
            "Zerodha Order ID": order_id,
            "Parent Order ID": parent_order_id,
            "Related Trade ID": trade_id,
            "Error / Rejection Reason": remarks if normalized_status in {"REJECTED", "CANCELLED"} else "",
        }
        for column in OPTION_ENTRY_REPORT_COLUMNS:
            active_row.setdefault(column, score_row.get(column, ""))
            history_row.setdefault(column, score_row.get(column, "") if side == "BUY" else "")

        key = str(order_id or trade_id or f"{trade_no}_{side}_{timestamp}")
        is_closing_completion = str(side or "").upper() == "SELL" and normalized_status == "COMPLETE"
        if is_closing_completion:
            for active_key, row in list(self.active_orders.items()):
                if row.get("Trade ID") == trade_id:
                    del self.active_orders[active_key]
        terminal_inactive = normalized_status in {"CANCELLED", "REJECTED"}
        if terminal_inactive:
            self.active_orders.pop(key, None)
        elif keep_active:
            if not is_closing_completion:
                self.active_orders[key] = active_row
        elif key in self.active_orders:
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
                "Early Score at Entry": final_trade.get("Early Score", ""),
                "Quantity": quantity,
                "Target Price": "",
                "Stop Loss Price": "",
                "Current LTP": exit_price,
                "Live PnL": pnl,
                "Live PnL %": pnl_percent,
                "Status": "COMPLETE",
            }
            self._emit_live_log_update(force=True)
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
            "Early Score at Entry": signal.get("score_row", {}).get("Early Score", ""),
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
            "account_balance": self.balance,
            "session_start_balance": self.session_start_balance,
            "session_pnl": self.balance - self.session_start_balance,
            "session_trade_count": self.trade_count,
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
            "latency": {
                "last_tick_batch": dict(self.last_tick_batch_latency),
                "last_candle_processing": dict(self.last_candle_processing_latency),
                "recent_events": list(self.latency_events[-10:]),
                "protective_verification": list(self.protective_verification_events[-10:]),
            },
            "excel_writer": excel_health,
            "store": self._store_health(),
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
        rsi_reversal_bullish = float(self.settings.get("rsi_reversal_bullish", 70))
        rsi_reversal_bearish = float(self.settings.get("rsi_reversal_bearish", 20))
        bullish_reversal_condition = float(self.settings.get("bullish_reversal_condition", -20))
        bearish_reversal_condition = float(self.settings.get("bearish_reversal_condition", 10))
        scored = build_scoring_row(
            self.nifty,
            i,
            bullish_threshold,
            bearish_threshold,
            rsi_bull,
            rsi_bear,
            rsi_reversal_bullish,
            rsi_reversal_bearish,
            bullish_reversal_condition,
            bearish_reversal_condition,
        )
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
        self._close_candle_persistence(timeout=10)
        if self.store:
            self.store.update_session(
                self.mode,
                self.session_id,
                final_balance=self.balance,
                total_trades=self.trade_count,
                ended=True,
            )
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

    def _store_health(self):
        store = self.store
        if not store:
            return {"enabled": False, "path": "", "async": False}
        candle_persistence = dict(self.candle_persistence_stats)
        if self.candle_persistence_queue is not None:
            candle_persistence["queue_size"] = self.candle_persistence_queue.qsize()
        return {
            "enabled": True,
            "path": store.path,
            "candle_persistence": candle_persistence,
            **store.health(),
        }


