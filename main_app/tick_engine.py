from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class TickMetrics:
    received: int = 0
    dropped: int = 0
    processed: int = 0
    last_tick_age_seconds: float = 0.0
    tick_update_ms: float = 0.0
    candle_update_ms: float = 0.0
    decision_latency_ms: float = 0.0
    throttle_mode: bool = False
    slow_decision_streak: int = 0


class LatestTickCache:
    def __init__(self, max_queue_size: int = 1000):
        self._lock = RLock()
        self.latest_ticks: dict[int, dict[str, Any]] = {}
        self.latest_tick_time: dict[int, float] = {}
        self.queue: queue.Queue[int] = queue.Queue(maxsize=max(1, int(max_queue_size)))
        self.metrics = TickMetrics()

    def on_tick(self, tick: dict[str, Any]) -> None:
        started = time.perf_counter()
        token = int((tick or {}).get("instrument_token") or (tick or {}).get("token") or 0)
        if token <= 0:
            return
        now = time.time()
        with self._lock:
            replaced = token in self.latest_ticks
            self.latest_ticks[token] = dict(tick or {})
            self.latest_tick_time[token] = now
            self.metrics.received += 1
            if replaced:
                self.metrics.dropped += 1
            try:
                self.queue.put_nowait(token)
            except queue.Full:
                self.metrics.dropped += 1
            self.metrics.tick_update_ms = (time.perf_counter() - started) * 1000

    def snapshot_latest(self) -> dict[int, dict[str, Any]]:
        with self._lock:
            self._drain_queue_locked()
            now = time.time()
            if self.latest_tick_time:
                self.metrics.last_tick_age_seconds = now - max(self.latest_tick_time.values())
            return {token: dict(tick) for token, tick in self.latest_ticks.items()}

    def mark_processed(self, count: int, candle_ms: float = 0.0, decision_ms: float = 0.0) -> None:
        with self._lock:
            self.metrics.processed += max(0, int(count))
            self.metrics.candle_update_ms = float(candle_ms or 0)
            self.metrics.decision_latency_ms = float(decision_ms or 0)
            if self.metrics.decision_latency_ms > 1000:
                self.metrics.slow_decision_streak += 1
            else:
                self.metrics.slow_decision_streak = 0
            self.metrics.throttle_mode = self.metrics.slow_decision_streak >= 2

    def metrics_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.metrics.__dict__)

    def _drain_queue_locked(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break


def decision_due(
    *,
    new_completed_candle: bool = False,
    active_pending_limit_order: bool = False,
    active_position_requires_exit_check: bool = False,
    last_decision_epoch: float = 0.0,
    min_interval_seconds: float = 2.0,
) -> bool:
    if new_completed_candle or active_pending_limit_order or active_position_requires_exit_check:
        return True
    return time.time() - float(last_decision_epoch or 0) >= float(min_interval_seconds or 0)
