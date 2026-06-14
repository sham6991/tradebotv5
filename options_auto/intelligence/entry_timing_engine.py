from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


class EntryTimingEngine:
    def evaluate(self, candle: dict[str, Any], option_quote: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        candle = dict(candle or {})
        option_quote = dict(option_quote or {})
        settings = dict(settings or {})
        blockers: list[str] = []
        warnings: list[str] = []

        cutoff = _time_from(settings.get("no_new_trade_after"))
        current = _time_from(settings.get("timestamp") or option_quote.get("timestamp") or candle.get("timestamp"))
        if cutoff and current and current >= cutoff:
            blockers.append("No new trades after configured cutoff.")

        signal_age = option_quote.get("signal_age_seconds", candle.get("signal_age_seconds"))
        if signal_age not in ("", None):
            try:
                if float(signal_age) > float(settings.get("max_signal_age_seconds") or 20):
                    blockers.append("Signal is stale.")
            except (TypeError, ValueError):
                blockers.append("Signal age is invalid.")

        intended = _number(option_quote.get("intended_entry", option_quote.get("entry_price", option_quote.get("ltp"))))
        ltp = _number(option_quote.get("ltp", option_quote.get("last_price")))
        option_atr = _number(option_quote.get("option_atr14", option_quote.get("atr14")))
        max_chase = _number(settings.get("max_chase_points"), 3.0)
        chase_distance = ltp - intended if intended > 0 and ltp > 0 else 0.0
        if chase_distance > max_chase:
            blockers.append("Entry is chasing premium.")
        if option_atr > 0 and chase_distance > option_atr * 0.35:
            blockers.append("Entry moved too far from signal.")

        high = _number(candle.get("high"))
        low = _number(candle.get("low"))
        open_ = _number(candle.get("open"))
        close = _number(candle.get("close"))
        candle_range = high - low
        avg_range_10 = _number(candle.get("avg_range_10", candle.get("average_range_10")))
        if avg_range_10 > 0 and candle_range > avg_range_10 * 2.2:
            blockers.append("Signal candle is overextended.")
        atr = _number(candle.get("atr", candle.get("atr14")))
        if avg_range_10 <= 0 and atr > 0 and candle_range > atr * 2.5:
            blockers.append("Signal candle is overextended.")

        upper_wick_pct = _number(candle.get("upper_wick_pct"))
        if candle_range > 0 and upper_wick_pct > 45 and close < high - candle_range * 0.35:
            blockers.append("Option candle shows rejection.")
        close_position = _number(candle.get("close_position_pct"), 50.0)
        if close_position < 55:
            warnings.append("Candle confirmation is not near the strong close zone.")

        return {
            "allowed": not blockers,
            "state": "TIMING_OK" if not blockers else "BLOCKED_BY_TIMING",
            "chase_distance": round(chase_distance, 4),
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": warnings,
        }


def backtest_buy_limit(signal_close: float, avg_range_10: float, settings: dict[str, Any]) -> float:
    multiplier = _number(settings.get("buy_limit_offset_multiplier"), 0.25)
    minimum_offset = _number(settings.get("minimum_limit_offset_points"), 0.5)
    maximum_offset = _number(settings.get("maximum_limit_offset_points"), 2.0)
    offset = min(max(_number(avg_range_10) * multiplier, minimum_offset), maximum_offset)
    tick = _number(settings.get("tick_size"), 0.05)
    return round_to_tick(_number(signal_close) - offset, tick)


def round_to_tick(value: float, tick_size: float = 0.05) -> float:
    raw_tick = tick_size if _number(tick_size, 0.05) > 0 else 0.05
    try:
        price = Decimal(str(value))
        tick = Decimal(str(raw_tick))
        ticks = (price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return float((ticks * tick).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
        return 0.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _time_from(value: Any) -> time | None:
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).time()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).time()
    except ValueError:
        return None
