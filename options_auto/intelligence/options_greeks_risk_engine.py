from __future__ import annotations

from datetime import date, datetime, time
from typing import Any


RISK_ORDER = ["LOW", "MEDIUM", "HIGH", "EXTREME"]
RISK_SCORE = {"LOW": 90, "MEDIUM": 70, "HIGH": 45, "EXTREME": 15}


class OptionsGreeksRiskEngine:
    """Theta and option-premium risk proxy without fabricating Greeks."""

    def evaluate(self, candidate: dict[str, Any], settings: dict[str, Any] | None = None, today: date | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        candidate = dict(candidate or {})
        blockers: list[str] = []
        warnings: list[str] = []
        today = today or date.today()
        expiry = _parse_date(candidate.get("expiry"))
        days_to_expiry = (expiry - today).days if expiry else None
        if expiry is None:
            warnings.append("Expiry missing; theta risk cannot be fully evaluated.")
            if str(settings.get("strategy_profile") or "").upper() == "AGGRESSIVE":
                blockers.append("Expiry missing; aggressive mode is not allowed.")

        theta_level = _theta_level(days_to_expiry)
        theta_level = _adjust_theta_level(theta_level, candidate, settings)
        theta_score = RISK_SCORE[theta_level]

        if theta_level == "EXTREME" and not settings.get("expiry_scalp_enabled"):
            blockers.append("EXTREME theta risk blocks trade.")
        if candidate.get("moneyness") == "OTM" and days_to_expiry is not None and days_to_expiry <= 1:
            blockers.append("Near-expiry OTM theta risk is too high.")
        if (
            candidate.get("moneyness") == "OTM"
            and days_to_expiry == 0
            and _number(candidate.get("distance_pct")) > 1.2
            and not settings.get("allow_expiry_far_otm")
        ):
            blockers.append("Far OTM expiry-day option is blocked.")

        expected = _expected_edge(candidate, settings, theta_level)
        if expected["expected_edge_after_costs"] <= 0 or expected["edge_ratio"] < 1.25:
            blockers.append("Expected premium move does not beat theta/spread/slippage/charges.")

        iv = candidate.get("iv")
        greeks_available = iv not in ("", None)
        return {
            "allowed": not blockers,
            "theta_risk": theta_level,
            "theta_risk_score": theta_score,
            "days_to_expiry": days_to_expiry,
            "greeks_available": greeks_available,
            "expected_edge": expected,
            "blockers": blockers,
            "warnings": warnings,
        }


def _theta_level(days_to_expiry: int | None) -> str:
    if days_to_expiry is None:
        return "MEDIUM"
    if days_to_expiry >= 5:
        return "LOW"
    if 2 <= days_to_expiry <= 4:
        return "MEDIUM"
    if days_to_expiry == 1:
        return "HIGH"
    return "EXTREME"


def _adjust_theta_level(level: str, candidate: dict[str, Any], settings: dict[str, Any]) -> str:
    index = RISK_ORDER.index(level)
    moneyness = str(candidate.get("moneyness") or "").upper()
    if moneyness == "OTM":
        index += 1
    if moneyness == "OTM" and _number(candidate.get("distance_pct")) > 1.2:
        index += 1
    current_time = _time_from(settings.get("timestamp") or candidate.get("timestamp"))
    if current_time and current_time >= time(13, 30):
        index += 1
    if current_time and current_time >= time(14, 30):
        index += 1
    if candidate.get("premium_expansion_confirmed"):
        index -= 1
    if moneyness in {"ATM", "ITM"} and _number(candidate.get("premium_momentum_score")) >= 75:
        index -= 1
    if level == "EXTREME":
        index = max(index, RISK_ORDER.index("MEDIUM"))
    index = max(0, min(len(RISK_ORDER) - 1, index))
    return RISK_ORDER[index]


def _expected_edge(candidate: dict[str, Any], settings: dict[str, Any], theta_level: str) -> dict[str, float]:
    ltp = _number(candidate.get("ltp"), _number(candidate.get("ask")))
    bid = _number(candidate.get("bid"))
    ask = _number(candidate.get("ask"), ltp)
    quantity = max(1, int(_number(candidate.get("quantity"), _number(settings.get("quantity"), candidate.get("lot_size") or 1))))
    spread_cost = max(0.0, ask - bid)
    slippage_points = max(
        _number(settings.get("slippage_buffer_points")),
        ltp * _number(settings.get("slippage_buffer_pct")) / 100 if settings.get("slippage_buffer_pct") not in ("", None) else 0.0,
    )
    charges_impact = _number(settings.get("estimated_total_charges"), 40.0) / quantity
    days_to_expiry = candidate.get("days_to_expiry")
    if days_to_expiry in ("", None):
        expiry = _parse_date(candidate.get("expiry"))
        today = settings.get("today")
        today_date = _parse_date(today) or date.today()
        days_to_expiry = (expiry - today_date).days if expiry else None
    days = int(days_to_expiry) if days_to_expiry is not None else 2
    if days >= 5:
        theta_decay_per_30min = ltp * 0.002
    elif 2 <= days <= 4:
        theta_decay_per_30min = ltp * 0.004
    elif days == 1:
        theta_decay_per_30min = ltp * 0.008
    else:
        theta_decay_per_30min = ltp * 0.015
    holding_factor = _number(settings.get("expected_holding_minutes"), 15.0) / 30.0
    estimated_theta_decay = theta_decay_per_30min * holding_factor
    minimum_profit_buffer = max(_number(settings.get("minimum_profit_buffer_points"), 1.0), ltp * 0.005)
    required_edge = spread_cost + slippage_points + charges_impact + estimated_theta_decay + minimum_profit_buffer

    option_atr = _number(candidate.get("option_atr14"), _number(candidate.get("atr14")))
    regime_multiplier = _number(settings.get("regime_target_multiplier"), _number(settings.get("atr_target_multiplier"), 1.5))
    regime_name = str(settings.get("regime") or "").lower()
    if option_atr > 0:
        expected_move = option_atr * regime_multiplier
    elif "strong" in regime_name:
        expected_move = ltp * 0.03
    elif "mild" in regime_name:
        expected_move = ltp * 0.02
    else:
        expected_move = ltp * 0.01
    if candidate.get("premium_expansion_confirmed"):
        expected_move *= 1.15
    if _number(candidate.get("relative_volume")) >= 1.5:
        expected_move *= 1.10
    if theta_level == "HIGH":
        expected_move *= 0.85
    elif theta_level == "EXTREME":
        expected_move *= 0.70
    if _number(candidate.get("spread_pct")) > 0.5:
        expected_move *= 0.80

    expected_edge_after_costs = expected_move - required_edge
    edge_ratio = expected_move / max(required_edge, 0.01)
    return {
        "spread_cost_points": round(spread_cost, 4),
        "slippage_points": round(slippage_points, 4),
        "charges_impact_points": round(charges_impact, 4),
        "estimated_theta_decay_points": round(estimated_theta_decay, 4),
        "minimum_profit_buffer_points": round(minimum_profit_buffer, 4),
        "required_edge_points": round(required_edge, 4),
        "expected_premium_move": round(expected_move, 4),
        "expected_edge_after_costs": round(expected_edge_after_costs, 4),
        "edge_ratio": round(edge_ratio, 4),
    }


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _time_from(value: Any) -> time | None:
    if isinstance(value, datetime):
        return value.time()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).time()
    except ValueError:
        pass
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
