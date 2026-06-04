from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from runtime_errors import classify_runtime_error


PRIORITIES = {
    "PROTECTION": 1,
    "EMERGENCY_EXIT": 2,
    "OCO_CANCEL": 3,
    "RECONCILIATION": 4,
    "ENTRY": 5,
    "QUOTE": 6,
}


@dataclass
class RateLimiter:
    max_calls: int = 3
    per_seconds: float = 1.0
    calls: deque[float] = field(default_factory=deque)

    def acquire(self, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        while self.calls and now - self.calls[0] > self.per_seconds:
            self.calls.popleft()
        if len(self.calls) >= self.max_calls:
            return False
        self.calls.append(now)
        return True


class KiteApiManager:
    def __init__(self, client: Any | None = None, limiter: RateLimiter | None = None):
        self.client = client
        self.limiter = limiter or RateLimiter()
        self.history: list[dict[str, Any]] = []

    def call(self, name: str, callback: Callable[[], Any], priority: str = "QUOTE") -> dict[str, Any]:
        priority = str(priority or "QUOTE").upper()
        if not self.limiter.acquire():
            result = {
                "ok": False,
                "name": name,
                "priority": priority,
                "error": "Rate limit exceeded",
                "error_category": "rate_limit",
            }
            self.history.append(result)
            return result
        start = time.perf_counter()
        try:
            value = callback()
            result = {
                "ok": True,
                "name": name,
                "priority": priority,
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "value": value,
            }
        except Exception as exc:
            classification = classify_runtime_error(exc, context=name)
            result = {
                "ok": False,
                "name": name,
                "priority": priority,
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "error": str(exc),
                "error_class": classification["class"],
                "error_category": classification["category"],
            }
        self.history.append(result)
        return result

    def health(self) -> dict[str, Any]:
        failures = [item for item in self.history[-50:] if not item.get("ok")]
        return {
            "calls": len(self.history),
            "recent_failures": len(failures),
            "healthy": not failures,
            "history": self.history[-50:],
        }

