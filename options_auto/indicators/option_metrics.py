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


def liquidity_score(volume: Any = None, oi: Any = None, spread_pct: Any = None, depth_imbalance: Any = None) -> float:
    def safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    volume_value = safe_float(volume)
    oi_value = safe_float(oi)
    spread_value = safe_float(spread_pct, 100.0)
    depth_value = abs(safe_float(depth_imbalance))
    volume_component = min(35.0, volume_value / 5000.0 * 35.0) if volume_value > 0 else 12.0
    oi_component = min(25.0, oi_value / 10000.0 * 25.0) if oi_value > 0 else 8.0
    spread_component = max(0.0, 25.0 - spread_value * 20.0)
    depth_component = max(0.0, 15.0 - depth_value * 0.08)
    return round(max(0.0, min(100.0, volume_component + oi_component + spread_component + depth_component)), 2)

