from __future__ import annotations

from datetime import datetime, time
from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT


class TradeScoreEngine:
    def score(self, candidate: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = dict(context or {})
        side = str(context.get("selected_side") or candidate.get("option_type") or "").upper()
        features = dict(context.get("index_features") or {})
        regime = dict(context.get("regime") or {})
        market_cue = dict(context.get("market_cue") or {})
        theta = dict(candidate.get("theta_premium_risk") or context.get("theta_premium_risk") or {})
        settings = dict(context.get("settings") or {})
        news_weight = max(0.0, min(0.15, _number(settings.get("news_sentiment_weight"), 3.0) / 100.0))

        breakdown = {
            "regime_alignment": _regime_alignment_score(side, regime),
            "market_cue_alignment": _market_cue_alignment_score(side, market_cue, bool(context.get("reversal_setup_confirmed"))),
            "trend_premium_momentum": _trend_premium_momentum_score(side, features, candidate, settings),
            "vwap_ema_structure": _vwap_ema_structure_score(side, features),
            "volume_relative_volume": _volume_score(features.get("relative_volume", candidate.get("relative_volume"))),
            "option_liquidity_oi": _clamp(candidate.get("liquidity_score")),
            "spread_depth": _spread_depth_score(candidate),
            "volatility_theta_suitability": _volatility_theta_suitability_score(features, theta),
            "news_sentiment": _news_score(context.get("news_score", market_cue.get("components", {}).get("news", 0))),
            "time_of_day_quality": _time_of_day_score(context.get("timestamp"), context),
        }
        weights = {
            "regime_alignment": 0.20,
            "market_cue_alignment": 0.12,
            "trend_premium_momentum": 0.15,
            "vwap_ema_structure": 0.10,
            "volume_relative_volume": 0.08,
            "option_liquidity_oi": 0.12,
            "spread_depth": 0.10,
            "volatility_theta_suitability": 0.06,
            "news_sentiment": news_weight,
            "time_of_day_quality": 0.04,
        }
        weight_sum = sum(weights.values()) or 1.0
        normalized_weights = {key: value / weight_sum for key, value in weights.items()}
        total = sum(breakdown[key] * normalized_weights[key] for key in normalized_weights)
        return {
            "score": round(_clamp(total), 2),
            "breakdown": {key: round(value, 2) for key, value in breakdown.items()},
            "weights": {key: round(value, 4) for key, value in normalized_weights.items()},
        }


def _regime_alignment_score(side: str, regime: dict[str, Any]) -> float:
    regime_name = str(regime.get("regime") or "").lower()
    regime_side = str(regime.get("recommended_side") or SIDE_WAIT).upper()
    if regime_side == SIDE_WAIT:
        return 0.0
    if side != regime_side:
        return 0.0
    if side == SIDE_CE:
        if regime_name == "strong_bullish":
            return 100.0
        if regime_name == "mild_bullish":
            return 75.0
    if side == SIDE_PE:
        if regime_name == "strong_bearish":
            return 100.0
        if regime_name == "mild_bearish":
            return 75.0
    return _clamp(regime.get("confidence"))


def _market_cue_alignment_score(side: str, cue: dict[str, Any], reversal_setup_confirmed: bool) -> float:
    cue_name = str(cue.get("cue") or "").lower()
    cue_side = str(cue.get("recommended_side") or SIDE_WAIT).upper()
    if cue_side == SIDE_WAIT:
        return 40.0
    if side != cue_side:
        return 60.0 if reversal_setup_confirmed else 0.0
    if cue_name.startswith("strong"):
        return 100.0
    if cue_name.startswith("mild"):
        return 75.0
    return _clamp(cue.get("confidence"))


def _trend_premium_momentum_score(side: str, features: dict[str, Any], candidate: dict[str, Any], settings: dict[str, Any]) -> float:
    trend = _number(features.get("trend_strength_score"))
    index_component = max(0.0, trend) if side == SIDE_CE else max(0.0, -trend)
    threshold = _number(settings.get("trend_strength_threshold"), 55.0)
    if threshold > 0 and index_component < threshold:
        index_component *= max(0.25, index_component / threshold)
    premium = _clamp(candidate.get("premium_momentum_score", candidate.get("momentum_score", 0)))
    return (index_component + premium) / 2.0


def _vwap_ema_structure_score(side: str, features: dict[str, Any]) -> float:
    close = _number(features.get("close"))
    vwap = _number(features.get("vwap"))
    ema9 = _number(features.get("ema9"))
    ema20 = _number(features.get("ema20"))
    ema50 = _number(features.get("ema50"))
    score = 0.0
    if side == SIDE_CE:
        if close > vwap:
            score += 50
        if ema9 > ema20:
            score += 30
        if ema20 > ema50:
            score += 20
    elif side == SIDE_PE:
        if close < vwap:
            score += 50
        if ema9 < ema20:
            score += 30
        if ema20 < ema50:
            score += 20
    return _clamp(score)


def _volume_score(relative_volume: Any) -> float:
    value = _number(relative_volume)
    if value >= 2.0:
        return 100.0
    if value >= 1.5:
        return 80.0
    if value >= 1.2:
        return 65.0
    if value >= 1.0:
        return 50.0
    if value >= 0.7:
        return 35.0
    return 20.0


def _spread_depth_score(candidate: dict[str, Any]) -> float:
    spread = _number(candidate.get("spread_pct"), 100.0)
    if spread <= 0.10:
        spread_score = 100.0
    elif spread <= 0.25:
        spread_score = 85.0
    elif spread <= 0.50:
        spread_score = 60.0
    elif spread <= 0.75:
        spread_score = 30.0
    else:
        spread_score = 0.0

    total_depth = _number(candidate.get("total_depth"), _number(candidate.get("bid_qty")) + _number(candidate.get("ask_qty")))
    if total_depth >= 5000:
        depth_score = 100.0
    elif total_depth >= 2000:
        depth_score = 80.0
    elif total_depth >= 1000:
        depth_score = 60.0
    elif total_depth >= 500:
        depth_score = 35.0
    else:
        depth_score = 0.0
    return 0.7 * spread_score + 0.3 * depth_score


def _volatility_theta_suitability_score(features: dict[str, Any], theta: dict[str, Any]) -> float:
    theta_score = _clamp(theta.get("theta_risk_score", theta.get("score", 70)))
    atr_pct = _number(features.get("atr_pct"))
    strong_trend = abs(_number(features.get("trend_strength_score"))) >= 55
    if 0.10 <= atr_pct <= 0.35:
        volatility_score = 100.0
    elif 0.35 < atr_pct <= 0.55 and strong_trend:
        volatility_score = 75.0
    elif atr_pct > 0.55 and not strong_trend:
        volatility_score = 20.0
    elif atr_pct < 0.08:
        volatility_score = 35.0
    else:
        volatility_score = 60.0
    return 0.6 * theta_score + 0.4 * volatility_score


def _news_score(raw_news_score: Any) -> float:
    return _clamp(50.0 + _number(raw_news_score) * 0.5)


def _time_of_day_score(timestamp: Any, context: dict[str, Any]) -> float:
    if context.get("time_of_day_score") not in ("", None):
        return _clamp(context.get("time_of_day_score"))
    current = _time_from(timestamp)
    if current is None:
        return 75.0
    if time(9, 15) <= current < time(9, 30):
        return 0.0 if context.get("avoid_first_minutes_enabled") else 60.0
    if time(9, 30) <= current < time(10, 30):
        return 100.0
    if time(10, 30) <= current < time(13, 30):
        return 75.0
    if time(13, 30) <= current < time(14, 45):
        return 55.0
    if current >= time(14, 45):
        return 35.0 if context.get("late_scalp_enabled") else 0.0
    return 75.0


def _time_from(timestamp: Any) -> time | None:
    if isinstance(timestamp, datetime):
        return timestamp.time()
    text = str(timestamp or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text[:19] if "Y" not in fmt else text[:19], fmt).time()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).time()
    except ValueError:
        return None


def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, _number(value)))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
