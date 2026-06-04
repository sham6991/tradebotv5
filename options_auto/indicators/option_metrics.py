from __future__ import annotations

from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE


def intrinsic_value(spot: float, strike: float, option_type: str) -> float:
    option_type = str(option_type).upper()
    if option_type == SIDE_CE:
        return max(0.0, float(spot) - float(strike))
    if option_type == SIDE_PE:
        return max(0.0, float(strike) - float(spot))
    return 0.0


def moneyness(spot: float, strike: float, option_type: str, atm_tolerance_pct: float = 0.35) -> str:
    spot = float(spot)
    strike = float(strike)
    if spot <= 0:
        return "UNKNOWN"
    distance_pct = abs(strike - spot) / spot * 100
    if distance_pct <= atm_tolerance_pct:
        return "ATM"
    option_type = str(option_type).upper()
    if option_type == SIDE_CE:
        return "ITM" if strike < spot else "OTM"
    if option_type == SIDE_PE:
        return "ITM" if strike > spot else "OTM"
    return "UNKNOWN"


def premium_affordability_score(premium: Any, available_capital: Any, lot_size: Any) -> float:
    try:
        required = float(premium) * int(lot_size)
        available = float(available_capital)
    except (TypeError, ValueError):
        return 0.0
    if required <= 0 or available <= 0:
        return 0.0
    if required > available:
        return max(0.0, 40.0 * available / required)
    usage = required / available
    if usage <= 0.2:
        return 100.0
    if usage <= 0.5:
        return 80.0
    return max(45.0, 80.0 - (usage - 0.5) * 100)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def liquidity_components(
    volume: Any = None,
    oi: Any = None,
    spread_pct: Any = None,
    bid_qty: Any = None,
    ask_qty: Any = None,
    total_depth: Any = None,
    oi_source_missing: bool = False,
) -> dict[str, Any]:
    volume_value = _safe_float(volume)
    oi_value = _safe_float(oi)
    spread_value = _safe_float(spread_pct, 100.0)
    if total_depth in ("", None):
        total_depth_value = _safe_float(bid_qty) + _safe_float(ask_qty)
    else:
        total_depth_value = _safe_float(total_depth)

    if volume_value >= 100000:
        volume_component = 35
    elif volume_value >= 50000:
        volume_component = 28
    elif volume_value >= 20000:
        volume_component = 20
    elif volume_value >= 5000:
        volume_component = 12
    elif volume_value > 0:
        volume_component = 4
    else:
        volume_component = 0

    if oi_value >= 1000000:
        oi_component = 25
    elif oi_value >= 500000:
        oi_component = 20
    elif oi_value >= 100000:
        oi_component = 14
    elif oi_value >= 25000:
        oi_component = 8
    elif oi_value > 0:
        oi_component = 3
    else:
        oi_component = 0

    if spread_value <= 0.10:
        spread_component = 25
    elif spread_value <= 0.25:
        spread_component = 20
    elif spread_value <= 0.50:
        spread_component = 12
    elif spread_value <= 0.75:
        spread_component = 5
    else:
        spread_component = 0

    if total_depth_value >= 5000:
        depth_component = 15
    elif total_depth_value >= 2000:
        depth_component = 12
    elif total_depth_value >= 1000:
        depth_component = 8
    elif total_depth_value >= 500:
        depth_component = 4
    else:
        depth_component = 0

    warnings = []
    if oi_value <= 0 and oi_source_missing:
        warnings.append("OI missing from source; liquidity confidence reduced.")
    score = volume_component + oi_component + spread_component + depth_component
    return {
        "score": round(float(max(0, min(100, score))), 2),
        "volume_component": volume_component,
        "oi_component": oi_component,
        "spread_component": spread_component,
        "depth_component": depth_component,
        "total_depth": total_depth_value,
        "warnings": warnings,
    }


def liquidity_score(
    volume: Any = None,
    oi: Any = None,
    spread_pct: Any = None,
    depth_imbalance: Any = None,
    bid_qty: Any = None,
    ask_qty: Any = None,
    total_depth: Any = None,
) -> float:
    if total_depth in ("", None) and bid_qty in ("", None) and ask_qty in ("", None):
        total_depth = 0
    return liquidity_components(
        volume=volume,
        oi=oi,
        spread_pct=spread_pct,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        total_depth=total_depth,
    )["score"]


def premium_momentum_metrics(
    quote: dict[str, Any] | None = None,
    candle: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quote = dict(quote or {})
    candle = dict(candle or {})
    settings = dict(settings or {})
    blockers: list[str] = []
    warnings: list[str] = []

    return_1 = _safe_float(quote.get("premium_return_1", candle.get("premium_return_1")), 0.0)
    return_3 = _safe_float(quote.get("premium_return_3", candle.get("premium_return_3")), 0.0)
    relative_volume = _safe_float(quote.get("relative_volume", candle.get("relative_volume")), 1.0)
    spread_pct = _safe_float(quote.get("spread_pct", candle.get("spread_pct")), 100.0)
    max_spread = _safe_float(settings.get("max_spread_pct"), 0.6)
    upper_wick_pct = _safe_float(quote.get("upper_wick_pct", candle.get("upper_wick_pct")), 0.0)
    open_ = _safe_float(quote.get("open", candle.get("open")), 0.0)
    close = _safe_float(quote.get("close", candle.get("close", quote.get("ltp", quote.get("last_price")))), 0.0)
    option_vwap = _safe_float(quote.get("option_vwap", candle.get("vwap")), 0.0)
    premium_above_vwap = bool(close > option_vwap > 0)
    spread_widening = bool(quote.get("spread_widening") or candle.get("spread_widening"))

    if "premium_return_1" not in quote and "premium_return_1" not in candle:
        warnings.append("Premium return 1-candle is unavailable.")
    if "premium_return_3" not in quote and "premium_return_3" not in candle:
        warnings.append("Premium return 3-candle is unavailable.")

    score = 50.0
    if return_1 > 2.0:
        score += 15
    elif return_1 > 1.0:
        score += 10
    if return_3 > 6.0:
        score += 25
    elif return_3 > 3.0:
        score += 15
    if premium_above_vwap:
        score += 10
    if relative_volume >= 1.5:
        score += 10
    strong_rejection = upper_wick_pct > 45 and close < open_
    if strong_rejection:
        score -= 15
    if spread_widening:
        score -= 10

    confirmed = (
        return_1 > 0.5
        and return_3 > 1.5
        and relative_volume >= 1.0
        and spread_pct <= max_spread
        and not strong_rejection
    )
    if settings.get("premium_expansion_required") and not confirmed:
        blockers.append("Option premium is not confirming index direction.")
    return {
        "premium_return_1": round(return_1, 4),
        "premium_return_3": round(return_3, 4),
        "relative_volume": round(relative_volume, 4),
        "premium_above_vwap": premium_above_vwap,
        "premium_momentum_score": round(max(0.0, min(100.0, score)), 2),
        "premium_expansion_confirmed": confirmed,
        "strong_rejection": strong_rejection,
        "blockers": blockers,
        "warnings": warnings,
    }
