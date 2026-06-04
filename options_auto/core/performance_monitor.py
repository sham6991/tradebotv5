from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PerformanceMonitor:
    final_validation_warning_ms: float = 200.0
    action_warning_ms: float = 500.0
    events: list[dict[str, Any]] = field(default_factory=list)

    def now(self) -> float:
        return time.perf_counter()

    def elapsed_ms(self, start: float) -> float:
        return round((time.perf_counter() - float(start)) * 1000.0, 3)

    def record_latency(self, name: str, latency_ms: float, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        warnings = []
        if name == "final_validation" and latency_ms > self.final_validation_warning_ms:
            warnings.append("Final validation latency exceeded warning threshold.")
        if name in {"action", "protection"} and latency_ms > self.action_warning_ms:
            warnings.append("Action latency exceeded warning threshold.")
        event = {
            "name": name,
            "latency_ms": round(float(latency_ms), 3),
            "warnings": warnings,
            "metadata": dict(metadata or {}),
            "recorded_at_epoch": time.time(),
        }
        self.events.append(event)
        self.events = self.events[-500:]
        return event

    def snapshot(self) -> dict[str, Any]:
        return {"events": self.events[-100:]}
