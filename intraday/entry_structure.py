from __future__ import annotations

import math
from typing import Any

from .candle_feed import interval_minutes
from .constants import SIDE_LONG, SIDE_SHORT


def analyse_entry_structure(
    candles: list[dict[str, Any]],
    snapshot,
    settings,
    ema20_values: list[float] | None = None,
    ema50_values: list[float] | None = None,
    rsi_values: list[float] | None = None,
    traps: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candles = list(candles or [])
    latest = candles[-1] if candles else {}
    previous = candles[-2] if len(candles) >= 2 else {}
    threshold = max(0.1, float(getattr(settings, "relative_volume_threshold", 1.5) or 1.5))
    opening_bars = max(1, int(math.ceil(15 / max(1, interval_minutes(getattr(settings, "candle_interval", "minute"))))))
    opening = candles[:opening_bars] if len(candles) > opening_bars else candles[:-1] or candles
    prior = candles[:-1]
    swing_window = prior[-7:] or prior

    levels = {
        "opening_range_high": _max(opening, "high"),
        "opening_range_low": _min(opening, "low"),
        "day_high": _max(candles, "high"),
        "day_low": _min(candles, "low"),
        "prior_day_high": _max(prior, "high"),
        "prior_day_low": _min(prior, "low"),
        "previous_swing_high": _max(swing_window, "high"),
        "previous_swing_low": _min(swing_window, "low"),
        "vwap": float(getattr(snapshot, "vwap", 0.0) or 0.0),
        "poc": float(getattr(snapshot, "poc", 0.0) or 0.0),
        "vah": float(getattr(snapshot, "vah", 0.0) or 0.0),
        "val": float(getattr(snapshot, "val", 0.0) or 0.0),
        "opening_bars": opening_bars,
    }

    open_price = _price(latest, "open", getattr(snapshot, "open", 0.0))
    high = _price(latest, "high", getattr(snapshot, "high", 0.0))
    low = _price(latest, "low", getattr(snapshot, "low", 0.0))
    close = _price(latest, "close", getattr(snapshot, "close", getattr(snapshot, "ltp", 0.0)))
    previous_close = _price(previous, "close", close)
    candle_range = max(high - low, 0.01)
    body_strength = min(1.0, abs(close - open_price) / candle_range)
    bullish_body = close > open_price and body_strength >= 0.45
    bearish_body = close < open_price and body_strength >= 0.45

    relative_volume = float(getattr(snapshot, "relative_volume", 0.0) or 0.0)
    volume_confirm = relative_volume >= threshold
    current_volume = float(getattr(snapshot, "volume", 0.0) or _price(latest, "volume", 0.0))
    average_volume = _average([_price(row, "volume", 0.0) for row in prior[-max(2, int(getattr(settings, "volume_lookback", 20) or 20)):]])
    volume_spike = bool(average_volume and current_volume >= average_volume * threshold)

    ema20_slope_up = _slope_up(ema20_values)
    ema20_slope_down = _slope_down(ema20_values)
    ema50_slope_up = _slope_up(ema50_values)
    ema50_slope_down = _slope_down(ema50_values)
    rsi_rising = _slope_up(rsi_values)
    rsi_falling = _slope_down(rsi_values)
    try:
        rsi_value = float(getattr(snapshot, "rsi", 50.0))
    except (TypeError, ValueError):
        rsi_value = 50.0

    spread_pct = float(getattr(snapshot, "spread_pct", 0.0) or 0.0)
    liquidity_score = float(getattr(snapshot, "liquidity_score", 0.0) or 0.0)
    bid_qty = float(getattr(snapshot, "bid_qty", 0.0) or 0.0)
    ask_qty = float(getattr(snapshot, "ask_qty", 0.0) or 0.0)
    imbalance = float(getattr(snapshot, "depth_imbalance", 0.0) or 0.0)
    spread_acceptable = spread_pct <= 0.08 or liquidity_score >= 60
    bid_support = bid_qty >= ask_qty or imbalance >= -0.05
    ask_pressure = ask_qty >= bid_qty or imbalance <= 0.05

    long_triggers = _long_structure_triggers(
        close=close,
        high=high,
        low=low,
        previous_close=previous_close,
        bullish_body=bullish_body,
        levels=levels,
        volume_confirm=volume_confirm,
    )
    short_triggers = _short_structure_triggers(
        close=close,
        high=high,
        low=low,
        previous_close=previous_close,
        bearish_body=bearish_body,
        levels=levels,
        volume_confirm=volume_confirm,
    )

    trend = {
        "ema20_slope_up": ema20_slope_up,
        "ema20_slope_down": ema20_slope_down,
        "ema50_slope_up": ema50_slope_up,
        "ema50_slope_down": ema50_slope_down,
        "rsi_rising": rsi_rising,
        "rsi_falling": rsi_falling,
    }
    participation = {
        "relative_volume": round(relative_volume, 4),
        "relative_volume_threshold": threshold,
        "volume_confirmation": volume_confirm,
        "volume_spike": volume_spike,
        "candle_body_strength": round(body_strength, 4),
        "breakout_volume": bool(volume_confirm and _above(close, levels["opening_range_high"])),
        "breakdown_volume": bool(volume_confirm and _below(close, levels["opening_range_low"])),
        "price_acceptance_above_key": bool(_above(close, levels["vwap"]) or _above(close, levels["vah"])),
        "price_acceptance_below_key": bool(_below(close, levels["vwap"]) or _below(close, levels["val"])),
    }
    liquidity = {
        "spread_acceptable": spread_acceptable,
        "bid_support": bid_support,
        "ask_pressure": ask_pressure,
        "fill_probability": round(max(0.0, min(1.0, liquidity_score / 100.0)), 4),
        "liquidity_wall_long": bid_qty > ask_qty * 1.25 if ask_qty else bid_qty > 0,
        "liquidity_wall_short": ask_qty > bid_qty * 1.25 if bid_qty else ask_qty > 0,
        "sudden_depth_disappearance": False,
    }

    long_state = _side_state(
        side=SIDE_LONG,
        triggers=long_triggers,
        direction_confirmation=_above(close, levels["vwap"]),
        trend_filter=ema20_slope_up and (rsi_value >= float(getattr(settings, "rsi_bullish_threshold", 55.0) or 55.0) or rsi_rising),
        volume_confirmation=volume_confirm,
        liquidity_confirmation=spread_acceptable and bid_support,
        trap=traps.get("long") if traps else None,
    )
    short_state = _side_state(
        side=SIDE_SHORT,
        triggers=short_triggers,
        direction_confirmation=_below(close, levels["vwap"]),
        trend_filter=ema20_slope_down and (rsi_value <= float(getattr(settings, "rsi_bearish_threshold", 45.0) or 45.0) or rsi_falling),
        volume_confirmation=volume_confirm,
        liquidity_confirmation=spread_acceptable and ask_pressure,
        trap=traps.get("short") if traps else None,
    )

    return {
        "priority_hierarchy": [
            "Tier 1 structure/value",
            "Tier 2 participation/volume",
            "Tier 3 liquidity/execution",
            "Tier 4 trend/momentum filters",
            "Tier 5 context",
        ],
        "levels": levels,
        "participation": participation,
        "liquidity": liquidity,
        "trend": trend,
        "long": long_state,
        "short": short_state,
    }


def _long_structure_triggers(
    *,
    close: float,
    high: float,
    low: float,
    previous_close: float,
    bullish_body: bool,
    levels: dict[str, float],
    volume_confirm: bool,
) -> list[str]:
    triggers = []
    if volume_confirm and _above(close, levels["opening_range_high"]):
        triggers.append("Break above 15-minute opening range high with relative volume")
    if _below(previous_close, levels["vwap"]) and _above(close, levels["vwap"]):
        triggers.append("VWAP reclaim and hold")
    if _above(close, levels["vwap"]) and _touches(low, levels["vwap"]) and bullish_body:
        triggers.append("VWAP reclaim and hold")
    if _above(close, levels["vah"]) and low >= levels["vah"] * 0.999:
        triggers.append("Acceptance above VAH")
    if bullish_body and (_touches(low, levels["poc"]) or _touches(low, levels["val"])) and (_above(close, levels["poc"]) or _above(close, levels["val"])):
        triggers.append("Strong bounce from POC or VAL")
    if volume_confirm and _above(close, levels["previous_swing_high"]):
        triggers.append("Break previous swing high with volume")
    if bullish_body and _below(low, levels["prior_day_low"]) and close > levels["prior_day_low"]:
        triggers.append("Liquidity sweep below low followed by bullish reversal")
    return _dedupe(triggers)


def _short_structure_triggers(
    *,
    close: float,
    high: float,
    low: float,
    previous_close: float,
    bearish_body: bool,
    levels: dict[str, float],
    volume_confirm: bool,
) -> list[str]:
    triggers = []
    if volume_confirm and _below(close, levels["opening_range_low"]):
        triggers.append("Break below 15-minute opening range low with relative volume")
    if _above(previous_close, levels["vwap"]) and _below(close, levels["vwap"]):
        triggers.append("VWAP rejection from below")
    if _below(close, levels["vwap"]) and _touches(high, levels["vwap"]) and bearish_body:
        triggers.append("VWAP rejection from below")
    if _below(close, levels["val"]) and high <= levels["val"] * 1.001:
        triggers.append("Acceptance below VAL")
    if bearish_body and (_touches(high, levels["poc"]) or _touches(high, levels["vah"])) and (_below(close, levels["poc"]) or _below(close, levels["vah"])):
        triggers.append("Rejection from POC or VAH")
    if volume_confirm and _below(close, levels["previous_swing_low"]):
        triggers.append("Break previous swing low with volume")
    if bearish_body and _above(high, levels["prior_day_high"]) and close < levels["prior_day_high"]:
        triggers.append("Liquidity sweep above high followed by bearish reversal")
    return _dedupe(triggers)


def _side_state(
    *,
    side: str,
    triggers: list[str],
    direction_confirmation: bool,
    trend_filter: bool,
    volume_confirmation: bool,
    liquidity_confirmation: bool,
    trap: dict[str, Any] | None,
) -> dict[str, Any]:
    trap_warning = str((trap or {}).get("trap_warning") or "NONE").upper()
    trap_pass = trap_warning not in {"HIGH"}
    blockers = []
    label = "Long" if side == SIDE_LONG else "Short"
    if not direction_confirmation:
        blockers.append(f"{label} direction confirmation failed.")
    if not triggers:
        blockers.append(f"{label} structure trigger missing.")
    if not volume_confirmation:
        blockers.append(f"{label} volume confirmation failed.")
    if not liquidity_confirmation:
        blockers.append(f"{label} liquidity confirmation failed.")
    if not trend_filter:
        blockers.append(f"{label} EMA/RSI support filter failed.")
    if not trap_pass:
        blockers.append(f"{label} trap filter failed.")
    return {
        "side": side,
        "valid_without_risk": not blockers,
        "direction_confirmation": direction_confirmation,
        "structure_trigger": bool(triggers),
        "structure_triggers": triggers,
        "primary_trigger": triggers[0] if triggers else "",
        "volume_confirmation": volume_confirmation,
        "liquidity_confirmation": liquidity_confirmation,
        "trend_filter_pass": trend_filter,
        "trap_filter_pass": trap_pass,
        "trap_warning": trap_warning,
        "blockers": blockers,
    }


def _price(row: dict[str, Any], key: str, default: Any = 0.0) -> float:
    try:
        return float(row.get(key, default) if isinstance(row, dict) else default)
    except (TypeError, ValueError):
        return float(default or 0.0)


def _max(rows: list[dict[str, Any]], key: str) -> float:
    values = [_price(row, key, 0.0) for row in rows or []]
    return max(values) if values else 0.0


def _min(rows: list[dict[str, Any]], key: str) -> float:
    values = [_price(row, key, 0.0) for row in rows or []]
    return min(values) if values else 0.0


def _average(values: list[float]) -> float:
    values = [float(value) for value in values if value not in ("", None)]
    return sum(values) / len(values) if values else 0.0


def _slope_up(values: list[float] | None) -> bool:
    return bool(values and len(values) >= 2 and float(values[-1]) > float(values[-2]))


def _slope_down(values: list[float] | None) -> bool:
    return bool(values and len(values) >= 2 and float(values[-1]) < float(values[-2]))


def _above(price: float, level: float) -> bool:
    return bool(level and price > level)


def _below(price: float, level: float) -> bool:
    return bool(level and price < level)


def _touches(price: float, level: float, tolerance: float = 0.0015) -> bool:
    return bool(level and abs(price - level) / max(abs(level), 0.01) <= tolerance)


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
