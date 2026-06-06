from __future__ import annotations

from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE


def confirm_option_premium(side: str, candles: list[dict[str, Any]], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    side = str(side or "").upper()
    rows = [dict(row or {}) for row in candles or []]
    completed = [row for row in rows if row.get("complete", True)]
    candle = completed[-1] if completed else (rows[-1] if rows else {})
    if side not in {SIDE_CE, SIDE_PE}:
        return {"allowed": False, "state": "INVALID_SIDE", "blockers": ["Option side is missing."], "candle": {}}
    if not candle:
        return {"allowed": False, "state": "NO_PREMIUM_CANDLE", "blockers": ["Live option premium candle is unavailable."], "candle": {}}
    open_ = _number(candle.get("open"))
    close = _number(candle.get("close"))
    high = _number(candle.get("high"))
    low = _number(candle.get("low"))
    body = close - open_
    candle_range = max(0.0, high - low)
    min_body = float((settings or {}).get("premium_confirmation_min_body_pct") or 0)
    body_pct = (abs(body) / candle_range * 100.0) if candle_range > 0 else 0.0
    allowed = close > open_ and (min_body <= 0 or body_pct >= min_body)
    return {
        "allowed": allowed,
        "state": "PREMIUM_CONFIRMED" if allowed else "PREMIUM_WEAK",
        "blockers": [] if allowed else [f"{side} premium candle is not confirming upward momentum."],
        "candle": candle,
        "body_pct": round(body_pct, 2),
    }


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
