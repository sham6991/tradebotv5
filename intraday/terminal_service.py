from __future__ import annotations

import threading
import time
from datetime import datetime

from .constants import SESSION_STATUS_RUNNING
from .session_manager import IntradaySessionManager


class IntradayTerminalService:
    def __init__(self, base_result_folder: str, zerodha_client_provider=None):
        self.manager = IntradaySessionManager(base_result_folder, zerodha_client_provider=zerodha_client_provider)
        self._lock = threading.RLock()
        self._engine_stop = threading.Event()
        self._engine_thread: threading.Thread | None = None
        self._engine_interval_seconds = 5.0
        self._engine_last_cycle = ""
        self._engine_last_error = ""

    def defaults(self) -> dict:
        with self._lock:
            return {"settings": self.manager.default_settings()}

    def status(self) -> dict:
        with self._lock:
            return self._with_engine_state(self.manager.status_payload())

    def paper_account(self) -> dict:
        with self._lock:
            return self.manager.paper_account_status()

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
            result = self.manager.evaluate(self._engine_payload(payload))
            self._start_engine_locked(payload)
            self._engine_last_cycle = datetime.now().isoformat(timespec="seconds")
            return self._with_engine_state(result)

    def evaluate(self, payload: dict) -> dict:
        with self._lock:
            return self._with_engine_state(self.manager.evaluate(payload))

    def process_orders(self, payload: dict) -> dict:
        with self._lock:
            return self._with_engine_state(self.manager.process_orders(payload))

    def paper_backtest(self, payload: dict) -> dict:
        with self._lock:
            return self.manager.run_paper_backtest(payload)

    def approve(self, payload: dict) -> dict:
        with self._lock:
            return self._with_engine_state(self.manager.approve_entry(payload))

    def reject(self, payload: dict) -> dict:
        with self._lock:
            return self._with_engine_state(self.manager.reject_entry(payload))

    def kill_switch(self) -> dict:
        with self._lock:
            self._stop_engine_locked()
            return self._with_engine_state(self.manager.kill_switch())

    def stop(self) -> dict:
        with self._lock:
            self._stop_engine_locked()
            return self._with_engine_state(self.manager.stop_session())

    def _start_engine_locked(self, payload: dict) -> None:
        self._stop_engine_locked()
        self._engine_stop.clear()
        self._engine_last_cycle = ""
        self._engine_last_error = ""
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
            with self._lock:
                if self.manager.status != SESSION_STATUS_RUNNING:
                    self._engine_stop.set()
                    break
                try:
                    self.manager.evaluate(dict(engine_payload))
                    self._engine_last_cycle = datetime.now().isoformat(timespec="seconds")
                    self._engine_last_error = ""
                except Exception as exc:
                    self._engine_last_error = str(exc)
            self._engine_stop.wait(self._engine_interval_seconds)

    def _engine_interval(self, payload: dict) -> float:
        try:
            value = float(payload.get("engine_interval_seconds") or 5)
        except (TypeError, ValueError):
            value = 5.0
        return max(1.0, min(60.0, value))

    def _with_engine_state(self, payload: dict) -> dict:
        payload = dict(payload)
        payload["engine"] = {
            "running": bool(self._engine_thread and self._engine_thread.is_alive() and not self._engine_stop.is_set()),
            "interval_seconds": self._engine_interval_seconds,
            "last_cycle": self._engine_last_cycle,
            "last_error": self._engine_last_error,
        }
        return payload
