from __future__ import annotations

import threading
import time
from datetime import datetime

from .constants import SESSION_STATUS_RUNNING
from .session_manager import IntradaySessionManager
from web_core.latency_tracker import LatencyTracker


class IntradayTerminalService:
    def __init__(self, base_result_folder: str, zerodha_client_provider=None):
        self.manager = IntradaySessionManager(base_result_folder, zerodha_client_provider=zerodha_client_provider)
        self._lock = threading.RLock()
        self._engine_stop = threading.Event()
        self._engine_thread: threading.Thread | None = None
        self._engine_interval_seconds = 5.0
        self._engine_last_cycle = ""
        self._engine_last_error = ""
        self._engine_last_cycle_duration_ms = 0.0
        self._engine_last_wait_seconds = 0.0
        self._engine_adaptive_reason = "IDLE"
        self._engine_active_interval_seconds = 1.0
        self._engine_idle_interval_seconds = 3.0
        self._engine_hidden_ui_interval_seconds = 5.0
        self._engine_min_interval_seconds = 0.5
        self._engine_max_interval_seconds = 60.0
        self.latency = LatencyTracker()

    def defaults(self) -> dict:
        with self._lock:
            return {"settings": self.manager.default_settings()}

    def status(self) -> dict:
        start = time.perf_counter()
        with self._lock:
            try:
                return self._with_engine_state(self.manager.status_payload())
            finally:
                self._record_latency_started("intraday.status_full", start)

    def paper_account(self) -> dict:
        start = time.perf_counter()
        with self._lock:
            try:
                return self.manager.paper_account_status()
            finally:
                self._record_latency_started("intraday.account_status", start)

    def update_paper_account(self, payload: dict) -> dict:
        with self._lock:
            return self.manager.update_paper_account(payload)

    def upload_fii_dii(self, payload: dict) -> dict:
        with self._lock:
            return self.manager.upload_fii_dii_csv(payload)

    def start(self, payload: dict) -> dict:
        with self._lock:
            if self.manager.status == SESSION_STATUS_RUNNING:
                raise ValueError("Stop the current intraday session before starting another one.")
            result = self.manager.start_session(payload)
            eval_start = time.perf_counter()
            result = self.manager.evaluate(self._engine_payload(payload))
            self._record_latency_started("intraday.evaluate_total", eval_start, {"source": "start"})
            self._start_engine_locked(payload)
            self._engine_last_cycle = datetime.now().isoformat(timespec="seconds")
            return self._with_engine_state(result)

    def evaluate(self, payload: dict) -> dict:
        start = time.perf_counter()
        with self._lock:
            try:
                return self._with_engine_state(self.manager.evaluate(payload))
            finally:
                self._record_latency_started("intraday.evaluate_total", start, {"source": "manual"})

    def process_orders(self, payload: dict) -> dict:
        start = time.perf_counter()
        with self._lock:
            try:
                return self._with_engine_state(self.manager.process_orders(payload))
            finally:
                self._record_latency_started("intraday.process_orders", start)

    def paper_backtest(self, payload: dict) -> dict:
        with self._lock:
            return self.manager.run_paper_backtest(payload)

    def approve(self, payload: dict) -> dict:
        start = time.perf_counter()
        with self._lock:
            try:
                return self._with_engine_state(self.manager.approve_entry(payload))
            finally:
                self._record_latency_started("intraday.approve_command", start)

    def reject(self, payload: dict) -> dict:
        with self._lock:
            return self._with_engine_state(self.manager.reject_entry(payload))

    def kill_switch(self) -> dict:
        self.manager.kill_requested = True
        self._engine_stop.set()
        with self._lock:
            self._stop_engine_locked()
            return self._with_engine_state(self.manager.kill_switch())

    def stop(self) -> dict:
        with self._lock:
            self._stop_engine_locked()
            return self._with_engine_state(self.manager.stop_session())

    def latency_snapshot(self) -> dict:
        return self.latency.snapshot()

    def _record_latency_started(self, name: str, start: float, meta: dict | None = None) -> None:
        try:
            self.latency.record(name, (time.perf_counter() - float(start)) * 1000.0, meta)
        except Exception:
            return

    def ui_summary_snapshot(self) -> dict:
        start = time.perf_counter()
        with self._lock:
            settings = self.manager.settings.locked_dict() if self.manager.settings else {}
            active = self.manager.lifecycle.active_trade if self.manager.lifecycle else None
            pnl_total = (
                (self.manager.lifecycle.session_realized_pnl + self.manager.lifecycle.session_unrealized_pnl)
                if self.manager.lifecycle else 0.0
            )
            stocks = []
            for snapshot in list(self.manager.snapshots or [])[:50]:
                row = snapshot.to_dict()
                stocks.append({
                    "symbol": row.get("symbol"),
                    "ltp": row.get("ltp"),
                    "trend": row.get("trend") or row.get("selected_side") or "WAIT",
                    "signal": row.get("signal") or row.get("decision") or row.get("entry_gate") or "NO TRADE",
                    "score": max(float(row.get("final_long_score") or 0), float(row.get("final_short_score") or 0)),
                    "risk": row.get("risk") or row.get("margin_validation_status") or "OK",
                    "position": "Active" if (active or {}).get("symbol") == row.get("symbol") else "Flat",
                    "blocker": ((row.get("reason") or {}).get("blockers") or [None])[0],
                })
            payload = {
                "mode": str(settings.get("mode") or "PAPER").upper(),
                "session": self.manager.status,
                "paper_balance": (self.manager.paper_account.snapshot() or {}).get("available"),
                "session_pnl": pnl_total,
                "available_margin": (self.manager.cached_funds or {}).get("available") if self.manager.cached_funds else None,
                "used_margin": (self.manager.cached_funds or {}).get("used_margin") if self.manager.cached_funds else 0,
                "active_trade": (active or {}).get("symbol") or "",
                "lock_state": "LOCKED" if self.manager.settings and self.manager.status == SESSION_STATUS_RUNNING else "OPEN",
                "kill_switch": bool((self.manager.last_kill_switch_report or {}).get("active") or self.manager.kill_requested),
                "last_scan": self._engine_last_cycle,
                "stocks": stocks,
                "engine": self._engine_state_locked(),
                "latency": self.latency_snapshot(),
            }
            self._record_latency_started("intraday.ui_summary", start)
            payload["latency"] = self.latency_snapshot()
            return payload

    def _start_engine_locked(self, payload: dict) -> None:
        self._stop_engine_locked()
        self._engine_stop.clear()
        self._engine_last_cycle = ""
        self._engine_last_error = ""
        self._refresh_engine_interval_config_locked(payload)
        self._engine_interval_seconds = self._engine_interval(payload)
        engine_payload = {
            **self._engine_payload(payload),
        }
        self._engine_thread = threading.Thread(target=self._engine_loop, args=(engine_payload,), daemon=True)
        self._engine_thread.start()

    def _engine_payload(self, payload: dict) -> dict:
        return {
            "market_trend": payload.get("market_trend") or payload.get("nifty_trend") or "Neutral",
            "market_phase": payload.get("market_phase") or payload.get("cue_phase") or "",
        }

    def _stop_engine_locked(self) -> None:
        self._engine_stop.set()

    def _engine_loop(self, engine_payload: dict) -> None:
        while not self._engine_stop.is_set():
            wait_seconds = self._engine_interval_seconds
            with self._lock:
                if self.manager.status != SESSION_STATUS_RUNNING:
                    self._engine_stop.set()
                    break
                try:
                    cycle_start = time.perf_counter()
                    result = self.manager.evaluate(dict(engine_payload))
                    self._engine_last_cycle_duration_ms = round((time.perf_counter() - cycle_start) * 1000.0, 3)
                    self._record_latency_started("intraday.engine_cycle_total", cycle_start)
                    self._record_latency_started("intraday.evaluate_total", cycle_start, {"source": "engine"})
                    wait_seconds = self._next_engine_interval_locked(result)
                    self._engine_interval_seconds = wait_seconds
                    self._engine_last_wait_seconds = wait_seconds
                    self._engine_last_cycle = datetime.now().isoformat(timespec="seconds")
                    self._engine_last_error = ""
                except Exception as exc:
                    self._engine_last_error = str(exc)
                    wait_seconds = self._clamp_engine_interval(self._engine_hidden_ui_interval_seconds)
                    self._engine_interval_seconds = wait_seconds
                    self._engine_last_wait_seconds = wait_seconds
                    self._engine_adaptive_reason = "ERROR_BACKOFF"
            self._engine_stop.wait(wait_seconds)

    def _engine_interval(self, payload: dict) -> float:
        if "engine_interval_seconds" not in (payload or {}) or payload.get("engine_interval_seconds") in ("", None):
            return self._clamp_engine_interval(self._engine_active_interval_seconds)
        try:
            value = float(payload.get("engine_interval_seconds"))
        except (TypeError, ValueError):
            value = self._engine_active_interval_seconds
        return self._clamp_engine_interval(value)

    def _refresh_engine_interval_config_locked(self, payload: dict) -> None:
        payload = dict(payload or {})
        self._engine_active_interval_seconds = self._payload_float(payload, "intraday_engine_active_interval_seconds", 1.0)
        self._engine_idle_interval_seconds = self._payload_float(payload, "intraday_engine_idle_interval_seconds", 3.0)
        self._engine_hidden_ui_interval_seconds = self._payload_float(payload, "intraday_engine_hidden_ui_interval_seconds", 5.0)
        self._engine_min_interval_seconds = self._payload_float(payload, "intraday_engine_min_interval_seconds", 0.5)
        self._engine_max_interval_seconds = self._payload_float(payload, "intraday_engine_max_interval_seconds", 60.0)
        if self._engine_max_interval_seconds < self._engine_min_interval_seconds:
            self._engine_max_interval_seconds = self._engine_min_interval_seconds

    def _payload_float(self, payload: dict, key: str, default: float) -> float:
        try:
            return float(payload.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _clamp_engine_interval(self, value: float) -> float:
        return max(float(self._engine_min_interval_seconds), min(float(self._engine_max_interval_seconds), float(value)))

    def _next_engine_interval_locked(self, last_result: dict | None) -> float:
        if self.manager.status != SESSION_STATUS_RUNNING:
            self._engine_adaptive_reason = "STOPPED"
            return self._clamp_engine_interval(self._engine_idle_interval_seconds)
        lifecycle = self.manager.lifecycle
        active_trade = bool((lifecycle and lifecycle.active_trade) or (lifecycle and lifecycle.active_trades))
        if self.manager.pending_signal:
            self._engine_adaptive_reason = "PENDING_APPROVAL"
            return self._clamp_engine_interval(min(1.0, self._engine_active_interval_seconds))
        if active_trade:
            self._engine_adaptive_reason = "ACTIVE_TRADE"
            return self._clamp_engine_interval(min(1.0, self._engine_active_interval_seconds))
        if self.manager.last_signal or (last_result or {}).get("pending_signal"):
            self._engine_adaptive_reason = "RECENT_CANDIDATE"
            return self._clamp_engine_interval(min(1.0, self._engine_active_interval_seconds))
        self._engine_adaptive_reason = "IDLE"
        return self._clamp_engine_interval(self._engine_idle_interval_seconds)

    def _with_engine_state(self, payload: dict) -> dict:
        payload = dict(payload)
        payload["engine"] = self._engine_state_locked()
        payload["latency"] = self.latency_snapshot()
        return payload

    def _engine_state_locked(self) -> dict:
        return {
            "running": bool(self._engine_thread and self._engine_thread.is_alive() and not self._engine_stop.is_set()),
            "interval_seconds": self._engine_interval_seconds,
            "last_cycle": self._engine_last_cycle,
            "last_error": self._engine_last_error,
            "last_cycle_duration_ms": self._engine_last_cycle_duration_ms,
            "last_wait_seconds": self._engine_last_wait_seconds,
            "adaptive_reason": self._engine_adaptive_reason,
        }
