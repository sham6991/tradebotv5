import json
import os
import queue
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Protocol

import pandas as pd

from candle_builder import CandleBuilder
from config import LOT_SIZE
from config_profile import apply_settings_profile
from engine import TradingEngine, append_datetime_index_key, attach_datetime_index_map, timestamp_key
from event_logger import (
    ENTRY_FILLED,
    KILL_SWITCH_ACTIVATED,
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
    RECONCILIATION_ERROR,
    RECONCILIATION_WARNING,
    StructuredEventLogger,
)
from indicators import append_clean_candle, clean_and_add_indicators
from order_manager import ZerodhaOrderManager
from order_state import classify_order_state, normalize_order_status
from position_reconciler import PositionReconciler
from preflight import validate_live_preflight
from reporting import BufferedExcelWriter, format_datetime_value
from runtime_errors import classify_runtime_error
from risk_guard import LiveRiskGuard
from session_audit import write_session_audit
from sqlite_store import AsyncTradingStore, TradingStore
from strategy import OPTION_ENTRY_REPORT_COLUMNS, append_option_formula_row, build_scoring_row, ensure_option_formula_columns
from zerodha_client import ZerodhaClient


from live_session import LivePaperSession


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
        self.tick_queue_put_timeout = 0.05
        self.dropped_tick_batches = 0
        self.processed_tick_count = 0
        self.received_tick_count = 0
        self.last_tick_received_at = None
        self.last_tick_processed_at = None
        self.dispatcher_started_at = None
        self.dispatcher_error_count = 0
        self.last_dispatcher_error = ""
        self.last_dispatcher_error_class = ""
        self.last_dispatcher_error_category = ""
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
        self.last_feed_error_class = ""
        self.last_feed_error_category = ""
        self.last_preflight_report = None

    def active_live_session(self):
        for session in (self.live_real_session, self.live_paper_session):
            if session and not getattr(session, "session_closed", False):
                return session
        return None

    def assert_no_active_live_session(self, requested_mode):
        session = self.active_live_session()
        if session:
            active_mode = str(getattr(session, "mode", "") or "LIVE").upper()
            raise ValueError(f"{active_mode} live session is already running. Stop it before starting {str(requested_mode or '').upper()} live trading.")

    def connect_zerodha(self, api_key, api_secret=None, access_token=None):
        self.zerodha = ZerodhaClient(api_key, api_secret, access_token)
        return self.zerodha

    def fetch_live_history(self, nifty_token, option_contracts, days=5, interval="5minute", settings=None):
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
            df = ensure_option_formula_columns(df, settings)
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
        if self.active_ticker:
            try:
                self.zerodha.stop_ticker()
            except Exception as exc:
                self._remember_feed_error(exc, context="feed_stop")
                self.last_feed_event = f"previous ticker close ignored: {exc}"
            self.active_ticker = None
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
        zerodha = self.zerodha
        if not zerodha:
            raise ValueError("Connect Zerodha first.")
        self.feed_status = "connecting"
        self.active_ticker = zerodha.start_ticker(
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
            try:
                self.zerodha.stop_ticker()
            except Exception as exc:
                self._remember_feed_error(exc, context="feed_stop")
                self.last_feed_event = f"ticker stop ignored: {exc}"
        self._stop_tick_dispatcher()
        self.active_ticker = None

    def _handle_feed_connect(self, response):
        self._cancel_reconnect_timer()
        self.feed_status = "connected"
        self.last_feed_event = "connected"
        self.last_feed_error_class = ""
        self.last_feed_error_category = ""
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
        self._remember_feed_error(reason, context="feed_error")
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
        except Exception as exc:
            self._remember_feed_error(exc, context="feed_stop")
        try:
            self._connect_market_feed()
        except Exception as exc:
            self._remember_feed_error(exc, context="feed_reconnect")
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
        tick_queue = queue.Queue(maxsize=self.tick_queue_size)
        self.tick_queue = tick_queue
        self.tick_stop.clear()
        self.dropped_tick_batches = 0
        self.processed_tick_count = 0
        self.received_tick_count = 0
        self.last_tick_received_at = None
        self.last_tick_processed_at = None
        self.dispatcher_started_at = time.monotonic()
        self.dispatcher_error_count = 0
        self.last_dispatcher_error = ""
        self.last_dispatcher_error_class = ""
        self.last_dispatcher_error_category = ""

        def worker():
            while not self.tick_stop.is_set() or not tick_queue.empty():
                try:
                    ticks = tick_queue.get(timeout=0.2)
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
                        classification = classify_runtime_error(exc, context="tick_dispatch")
                        self.last_dispatcher_error_class = classification["class"]
                        self.last_dispatcher_error_category = classification["category"]
                finally:
                    tick_queue.task_done()

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
        tick_queue = self.tick_queue
        if not ticks or tick_queue is None:
            return
        batch = list(ticks)
        self.received_tick_count += len(batch)
        self.last_tick_received_at = time.time()
        try:
            tick_queue.put_nowait(batch)
            return
        except queue.Full:
            pass

        try:
            tick_queue.put(batch, timeout=self.tick_queue_put_timeout)
            return
        except queue.Full:
            self.dropped_tick_batches += 1

        try:
            tick_queue.get_nowait()
            tick_queue.task_done()
        except queue.Empty:
            pass

        try:
            tick_queue.put_nowait(batch)
        except queue.Full:
            self.dropped_tick_batches += 1

    def start_live_paper_trading(self, nifty, options, token_map, settings, save_path, on_trade=None, on_ticks=None, on_connect=None, on_close=None, on_order_update=None, on_alert=None):
        self.assert_no_active_live_session("PAPER")
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
        self.assert_no_active_live_session("LIVE")
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
            "feed_status": self._effective_feed_status(),
            "last_feed_event": self.last_feed_event,
            "feed_error_class": self.last_feed_error_class,
            "feed_error_category": self.last_feed_error_category,
            "reconnect_attempts": self.feed_reconnect_attempts,
            "dispatcher_errors": self.dispatcher_error_count,
            "last_dispatcher_error": self.last_dispatcher_error,
            "last_dispatcher_error_class": self.last_dispatcher_error_class,
            "last_dispatcher_error_category": self.last_dispatcher_error_category,
        }

    def _remember_feed_error(self, error, context="feed"):
        classification = classify_runtime_error(error, context=context)
        self.last_feed_error_class = classification["class"]
        self.last_feed_error_category = classification["category"]
        return classification

    def _effective_feed_status(self):
        if self.feed_should_run and self.last_tick_received_at:
            age = time.time() - self.last_tick_received_at
            if age <= self.feed_stale_after_seconds:
                return "connected"
        return self.feed_status

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


