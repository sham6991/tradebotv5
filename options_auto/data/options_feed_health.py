from __future__ import annotations

from datetime import datetime
from typing import Any


WEBSOCKET_TICKS = "WEBSOCKET_TICKS"
QUOTE_SNAPSHOT_POLLING = "QUOTE_SNAPSHOT_POLLING"
DATA_STALE = "DATA_STALE"
RECONNECTING = "RECONNECTING"
BACKFILLING = "BACKFILLING"


class OptionsFeedHealth:
    def __init__(self) -> None:
        self.data_mode = QUOTE_SNAPSHOT_POLLING
        self.last_ticks: dict[str, str] = {}
        self.reconnect_attempts = 0
        self.missing_candles: list[str] = []
        self.backfill_status = ""
        self.last_error = ""

    def mark_mode(self, mode: str) -> None:
        self.data_mode = str(mode or QUOTE_SNAPSHOT_POLLING).upper()

    def mark_tick(self, label: str, timestamp: Any = None) -> None:
        self.last_ticks[str(label or "").upper()] = _iso(timestamp) or datetime.now().isoformat(timespec="seconds")
        if self.data_mode == DATA_STALE:
            self.data_mode = WEBSOCKET_TICKS
        self.last_error = ""

    def mark_reconnecting(self, error: str = "") -> None:
        self.data_mode = RECONNECTING
        self.reconnect_attempts += 1
        self.last_error = error

    def mark_backfilling(self, missing: list[str] | None = None, status: str = "") -> None:
        self.data_mode = BACKFILLING
        self.missing_candles = list(missing or [])
        self.backfill_status = status

    def evaluate(self, settings: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        now = now or datetime.now()
        max_age = float(settings.get("max_tick_age_seconds") or settings.get("max_quote_age_seconds") or 3)
        stale_labels = []
        role_statuses = {}
        for label, timestamp in self.last_ticks.items():
            when = _dt(timestamp)
            age = (now - when).total_seconds() if when else None
            is_stale = bool(age is not None and age > max_age)
            if is_stale:
                stale_labels.append(label)
            role_statuses[label] = {
                "last_tick": timestamp,
                "age_seconds": round(age, 3) if age is not None else None,
                "fresh": bool(age is not None and age <= max_age),
                "stale": is_stale,
            }
        stale = bool(stale_labels)
        mode = DATA_STALE if stale else self.data_mode
        return {
            "data_mode": mode,
            "last_index_tick": self.last_ticks.get("INDEX", ""),
            "last_ce_tick": self.last_ticks.get("CE", ""),
            "last_pe_tick": self.last_ticks.get("PE", ""),
            "role_statuses": role_statuses,
            "feed_stale": stale,
            "stale_labels": stale_labels,
            "reconnect_attempts": self.reconnect_attempts,
            "missing_candles": list(self.missing_candles),
            "backfill_status": self.backfill_status,
            "last_error": self.last_error,
            "new_entries_allowed": not stale or not bool(settings.get("pause_entries_on_feed_stale", True)),
        }


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
    return when.isoformat(timespec="seconds") if when else ""
