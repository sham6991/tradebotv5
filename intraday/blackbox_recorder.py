from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any


TIMESTAMP_FIELDS = (
    "signal_generated_at",
    "final_validation_started_at",
    "final_validation_completed_at",
    "order_submitted_at",
    "broker_ack_at",
    "order_status_first_seen_at",
    "entry_filled_at",
    "target_submitted_at",
    "sl_submitted_at",
    "oco_cancel_submitted_at",
    "reconciled_at",
)


class BlackboxRecorder:
    def __init__(self, max_events: int = 500) -> None:
        self.max_events = max(50, int(max_events or 500))
        self.events: list[dict[str, Any]] = []

    def record(self, **timestamps: Any) -> dict[str, Any]:
        event = {key: _iso(timestamps.get(key)) for key in TIMESTAMP_FIELDS if timestamps.get(key)}
        event.update({key: value for key, value in timestamps.items() if key not in TIMESTAMP_FIELDS})
        event.setdefault("recorded_at", _iso(datetime.now()))
        event.update(calculate_latencies(event))
        self.events.append(event)
        self.events = self.events[-self.max_events :]
        return dict(event)

    def snapshot(self) -> dict[str, Any]:
        return {"events": list(self.events[-100:]), "latency_report": latency_report(self.events)}


def calculate_latencies(event: dict[str, Any]) -> dict[str, float]:
    return {
        "decision_latency_ms": _delta_ms(event.get("signal_generated_at"), event.get("final_validation_started_at")),
        "validation_latency_ms": _delta_ms(event.get("final_validation_started_at"), event.get("final_validation_completed_at")),
        "submit_to_ack_ms": _delta_ms(event.get("order_submitted_at"), event.get("broker_ack_at")),
        "ack_to_fill_ms": _delta_ms(event.get("broker_ack_at"), event.get("entry_filled_at")),
        "protection_delay_ms": _delta_ms(event.get("entry_filled_at"), event.get("sl_submitted_at")),
        "oco_cancel_latency_ms": _delta_ms(event.get("oco_cancel_submitted_at"), event.get("reconciled_at")),
        "data_age_ms": float(event.get("data_age_ms") or 0.0),
    }


def latency_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {"count": len(events)}
    for key in (
        "decision_latency_ms",
        "validation_latency_ms",
        "submit_to_ack_ms",
        "ack_to_fill_ms",
        "protection_delay_ms",
        "oco_cancel_latency_ms",
        "data_age_ms",
    ):
        values = [float(event.get(key) or 0.0) for event in events if event.get(key) not in ("", None)]
        if not values:
            report[key] = {"p50": 0.0, "p95": 0.0}
            continue
        ordered = sorted(values)
        p95_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
        report[key] = {"p50": round(statistics.median(ordered), 2), "p95": round(ordered[p95_index], 2)}
    return report


def _delta_ms(start: Any, end: Any) -> float:
    start_dt = _dt(start)
    end_dt = _dt(end)
    if not start_dt or not end_dt:
        return 0.0
    return round(max(0.0, (end_dt - start_dt).total_seconds() * 1000.0), 2)


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _iso(value: Any) -> str:
    when = _dt(value)
    return when.isoformat(timespec="milliseconds") if when else ""


__all__ = ["BlackboxRecorder", "calculate_latencies", "latency_report"]
