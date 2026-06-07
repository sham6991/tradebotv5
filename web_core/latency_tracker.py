from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Any


class LatencyTracker:
    """Rolling latency recorder for performance-only telemetry.

    This PR is performance-only. Strategy/decision/output behavior must remain unchanged.
    """

    def __init__(self, max_records: int = 200):
        self.max_records = max(1, int(max_records or 200))
        self._records: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=self.max_records))
        self._lock = threading.Lock()

    def record(self, name: str, duration_ms: float, meta: dict[str, Any] | None = None) -> None:
        try:
            key = str(name or "").strip()
            if not key:
                return
            row = {
                "duration_ms": round(max(0.0, float(duration_ms or 0.0)), 3),
                "meta": dict(meta or {}),
            }
            with self._lock:
                self._records[key].append(row)
        except Exception:
            return

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        try:
            with self._lock:
                items = {key: [float(row.get("duration_ms") or 0.0) for row in rows] for key, rows in self._records.items()}
            return {key: _stats(values) for key, values in sorted(items.items())}
        except Exception:
            return {}


def _stats(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "last_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "last_ms": round(values[-1], 3),
        "p50_ms": round(_percentile(ordered, 50), 3),
        "p95_ms": round(_percentile(ordered, 95), 3),
        "max_ms": round(max(values), 3),
    }


def _percentile(ordered: list[float], percentile: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (float(percentile) / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight
