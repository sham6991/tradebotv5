from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any


@dataclass
class MarketPhaseSnapshot:
    phase: str
    gap_type: str = "UNKNOWN"
    opening_micro_range_high: float = 0.0
    opening_micro_range_low: float = 0.0
    opening_micro_midpoint: float = 0.0
    opening_micro_range_size: float = 0.0
    opening_15m_high: float = 0.0
    opening_15m_low: float = 0.0
    opening_15m_midpoint: float = 0.0
    opening_15m_range_size: float = 0.0
    blockers: list[str] = field(default_factory=list)


class MarketPhaseEngine:
    def phase_at(self, value: datetime | None = None, square_off_time: str = "15:20") -> str:
        dt = value or datetime.now()
        current = dt.time()
        square_off = _parse_time(square_off_time, time(15, 20))
        if current >= square_off:
            return "FORCED_EXIT_ZONE"
        if current < time(9, 15):
            return "PRE_MARKET"
        if current < time(9, 20):
            return "OPENING_MICRO_RANGE"
        if current < time(9, 30):
            return "OPENING_CONFIRMATION"
        if current < time(11, 30):
            return "MORNING_TREND"
        if current < time(13, 45):
            return "MIDDAY_COMPRESSION"
        if current < time(14, 45):
            return "AFTERNOON_DECISION"
        return "CLOSING_RISK"

    def snapshot(
        self,
        candles: list[dict[str, Any]],
        *,
        previous_close: float = 0.0,
        today_open: float = 0.0,
        now: datetime | None = None,
        square_off_time: str = "15:20",
    ) -> MarketPhaseSnapshot:
        phase = self.phase_at(now, square_off_time)
        micro = _range_for(candles, time(9, 15), time(9, 20))
        confirm = _range_for(candles, time(9, 15), time(9, 30))
        blockers = []
        if phase in {"PRE_MARKET", "OPENING_MICRO_RANGE", "FORCED_EXIT_ZONE"}:
            blockers.append(f"{phase} does not allow new entries.")
        gap_type = classify_gap(previous_close, today_open)
        return MarketPhaseSnapshot(
            phase=phase,
            gap_type=gap_type,
            opening_micro_range_high=micro["high"],
            opening_micro_range_low=micro["low"],
            opening_micro_midpoint=micro["midpoint"],
            opening_micro_range_size=micro["size"],
            opening_15m_high=confirm["high"],
            opening_15m_low=confirm["low"],
            opening_15m_midpoint=confirm["midpoint"],
            opening_15m_range_size=confirm["size"],
            blockers=blockers,
        )


def classify_gap(previous_close: float, today_open: float) -> str:
    previous_close = float(previous_close or 0)
    today_open = float(today_open or 0)
    if previous_close <= 0 or today_open <= 0:
        return "UNKNOWN"
    gap_percent = ((today_open - previous_close) / previous_close) * 100
    if -0.15 <= gap_percent <= 0.15:
        return "FLAT_OPEN"
    if 0.15 < gap_percent <= 0.35:
        return "SMALL_GAP_UP"
    if 0.35 < gap_percent <= 0.75:
        return "MEDIUM_GAP_UP"
    if gap_percent > 0.75:
        return "LARGE_GAP_UP"
    if -0.35 <= gap_percent < -0.15:
        return "SMALL_GAP_DOWN"
    if -0.75 <= gap_percent < -0.35:
        return "MEDIUM_GAP_DOWN"
    return "LARGE_GAP_DOWN"


def _range_for(candles: list[dict[str, Any]], start: time, end: time) -> dict[str, float]:
    rows = [row for row in candles if start <= _row_time(row) < end]
    highs = [float(row.get("high") or 0) for row in rows if float(row.get("high") or 0) > 0]
    lows = [float(row.get("low") or 0) for row in rows if float(row.get("low") or 0) > 0]
    high = max(highs) if highs else 0.0
    low = min(lows) if lows else 0.0
    size = max(0.0, high - low)
    return {"high": high, "low": low, "midpoint": (high + low) / 2 if high and low else 0.0, "size": size}


def _row_time(row: dict[str, Any]) -> time:
    value = row.get("timestamp") or row.get("datetime") or row.get("date")
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, date):
        return time(0, 0)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).time()
    except ValueError:
        return time(0, 0)


def _parse_time(value: str, fallback: time) -> time:
    try:
        hour, minute = str(value or "").split(":", 1)
        return time(int(hour), int(minute[:2]))
    except Exception:
        return fallback
