from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT
from options_auto.core.clock import iso_now


@dataclass
class MarketCue:
    cue: str
    score: float
    confidence: float
    recommended_side: str
    phase: str = "LUNCH"
    components: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    last_updated: str = field(default_factory=iso_now)
    next_refresh: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "cue": self.cue,
            "score": self.score,
            "confidence": self.confidence,
            "recommended_side": self.recommended_side,
            "components": self.components,
            "reason": self.reason,
            "last_updated": self.last_updated,
            "next_refresh": self.next_refresh,
        }


class MarketCueEngine:
    def evaluate(self, payload: dict[str, Any] | None = None, phase: str = "") -> MarketCue:
        payload = dict(payload or {})
        phase = _normalize_phase(phase or payload.get("phase") or payload.get("market_phase"))
        features = dict(payload.get("index_features") or payload.get("features") or {})

        if phase == "PREMARKET":
            components = self._premarket_components(payload)
            weights = {
                "fii_dii": 0.30,
                "global_cue": 0.20,
                "previous_day_trend": 0.20,
                "news": 0.10,
                "volatility": 0.10,
                "option_oi": 0.10,
            }
        elif phase == "AFTERNOON":
            components = self._afternoon_components(payload, features)
            weights = {
                "intraday_trend": 0.30,
                "trend_continuation": 0.20,
                "reversal_risk": 0.15,
                "vwap": 0.15,
                "option_premium_behavior": 0.10,
                "volatility": 0.05,
                "news": 0.05,
            }
        else:
            phase = "LUNCH"
            components = self._lunch_components(payload, features)
            weights = {
                "intraday_trend": 0.35,
                "vwap": 0.20,
                "volume": 0.15,
                "option_oi": 0.15,
                "news": 0.05,
                "volatility": 0.10,
                "fii_dii": 0.0,
            }

        contributions = {key: _clamp(components.get(key, 0.0)) * weight for key, weight in weights.items()}
        score = _clamp(sum(contributions.values()))
        cue, side = _classify(score)

        high_volatility = abs(components.get("volatility", 0.0)) >= 40
        conflict = _component_conflict(contributions)
        if abs(score) < 20 and high_volatility:
            cue, side = "volatile_uncertain", SIDE_WAIT
        if conflict >= 20 and abs(score) < 55:
            cue, side = "volatile_uncertain", SIDE_WAIT

        confidence = min(100.0, 45.0 + abs(score) * 0.55)
        confidence = max(0.0, confidence - conflict)
        return MarketCue(
            phase=phase,
            cue=cue,
            score=round(score, 2),
            confidence=round(confidence, 2),
            recommended_side=side,
            components={key: round(float(value), 2) for key, value in components.items()},
            reason=f"{cue.replace('_', ' ').title()} from {phase.lower()} score {score:.1f}.",
        )

    def _premarket_components(self, payload: dict[str, Any]) -> dict[str, float]:
        return {
            "fii_dii": _fii_dii_component(payload),
            "global_cue": _component(payload, "global_cue_score", "global_score"),
            "previous_day_trend": _component(payload, "previous_day_trend_score", "previous_trend_score"),
            "news": _news_component(payload),
            "volatility": _volatility_component(payload),
            "option_oi": _component(payload, "option_oi_score", "oi_score"),
        }

    def _lunch_components(self, payload: dict[str, Any], features: dict[str, Any]) -> dict[str, float]:
        trend = _component(features, "trend_strength_score", "technical_score", fallback=_component(payload, "technical_score", "trend_score"))
        return {
            "intraday_trend": trend,
            "vwap": _vwap_component(features),
            "volume": _volume_component(features, trend),
            "option_oi": _component(payload, "option_oi_score", "oi_score"),
            "news": _news_component(payload),
            "volatility": _volatility_component(payload),
            "fii_dii": 0.0,
        }

    def _afternoon_components(self, payload: dict[str, Any], features: dict[str, Any]) -> dict[str, float]:
        trend = _component(features, "trend_strength_score", fallback=_component(payload, "technical_score", "trend_score"))
        return {
            "intraday_trend": trend,
            "trend_continuation": _trend_continuation_component(features),
            "reversal_risk": _reversal_risk_component(features, payload),
            "vwap": _vwap_component(features),
            "option_premium_behavior": _component(payload, "option_premium_behavior_score", "premium_behavior_score"),
            "volatility": _volatility_component(payload),
            "news": _news_component(payload),
            "fii_dii": 0.0,
        }


def _normalize_phase(value: Any) -> str:
    text = str(value or "LUNCH").strip().upper().replace("_", "")
    if text in {"PREMARKET", "PREOPEN", "MORNING"}:
        return "PREMARKET"
    if text in {"AFTERNOON", "CLOSING"}:
        return "AFTERNOON"
    return "LUNCH"


def _clamp(value: Any, low: float = -100.0, high: float = 100.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _component(payload: dict[str, Any], *keys: str, fallback: Any = 0.0) -> float:
    for key in keys:
        if key in payload and payload[key] not in ("", None):
            return _clamp(payload[key])
    return _clamp(fallback)


def _fii_dii_component(payload: dict[str, Any]) -> float:
    if "fii_dii_score" in payload:
        return _clamp(payload.get("fii_dii_score"))
    fii_net = _number(payload.get("fii_net"), _number(payload.get("fii_buy_value")) - _number(payload.get("fii_sell_value")))
    dii_net = _number(payload.get("dii_net"), _number(payload.get("dii_buy_value")) - _number(payload.get("dii_sell_value")))
    combined = fii_net + dii_net
    turnover = _number(payload.get("total_turnover"))
    if turnover > 0:
        return _clamp(combined / turnover * 100)
    if combined >= 3000:
        return 100.0
    if combined >= 1500:
        return 60.0
    if combined >= 500:
        return 30.0
    if combined > -500:
        return 0.0
    if combined > -1500:
        return -30.0
    if combined > -3000:
        return -60.0
    return -100.0


def _news_component(payload: dict[str, Any]) -> float:
    return _clamp(payload.get("news_score", payload.get("news_sentiment_score", 0.0)), -30.0, 30.0)


def _volatility_component(payload: dict[str, Any]) -> float:
    if "volatility_score" in payload:
        return _clamp(payload.get("volatility_score"))
    vix_change = _number(payload.get("vix_change_pct"), _number(payload.get("india_vix_change_pct")))
    if vix_change > 10:
        return -40.0
    if vix_change > 5:
        return -20.0
    if vix_change < -1:
        return 10.0
    return 0.0


def _vwap_component(features: dict[str, Any]) -> float:
    close = _number(features.get("close"))
    vwap = _number(features.get("vwap"))
    if close <= 0 or vwap <= 0:
        return 0.0
    distance = (close - vwap) / close * 100
    if distance > 0.35:
        return 80.0
    if distance > 0.15:
        return 50.0
    if distance < -0.35:
        return -80.0
    if distance < -0.15:
        return -50.0
    return 0.0


def _volume_component(features: dict[str, Any], trend: float) -> float:
    relative_volume = _number(features.get("relative_volume"))
    if relative_volume >= 1.5 and trend > 0:
        return 40.0
    if relative_volume >= 1.5 and trend < 0:
        return -40.0
    return 0.0


def _trend_continuation_component(features: dict[str, Any]) -> float:
    close = _number(features.get("close"))
    vwap = _number(features.get("vwap"))
    ema9 = _number(features.get("ema9"))
    ema20 = _number(features.get("ema20"))
    rsi = _number(features.get("rsi14"), 50.0)
    relative_volume = _number(features.get("relative_volume"))
    if close > vwap and ema9 > ema20 and 55 <= rsi <= 70 and relative_volume >= 1.2:
        return 50.0
    if close < vwap and ema9 < ema20 and 30 <= rsi <= 45 and relative_volume >= 1.2:
        return -50.0
    return 0.0


def _reversal_risk_component(features: dict[str, Any], payload: dict[str, Any]) -> float:
    score = 0.0
    close = _number(features.get("close"))
    open_ = _number(features.get("open"))
    ema9 = _number(features.get("ema9"))
    rsi = _number(features.get("rsi14"), 50.0)
    rsi_slope = _number(features.get("rsi_slope_3"))
    if _number(features.get("upper_wick_pct")) > 45 and close < open_:
        score -= 20
    if rsi > 75 and rsi_slope < 0:
        score -= 15
    if close < ema9:
        score -= 10
    if _number(payload.get("premium_behavior_score")) < 0:
        score -= 10
    if _number(features.get("lower_wick_pct")) > 45 and close > open_:
        score += 20
    if rsi < 25 and rsi_slope > 0:
        score += 15
    return _clamp(score)


def _classify(score: float) -> tuple[str, str]:
    if score >= 55:
        return "strong_bullish", SIDE_CE
    if score >= 20:
        return "mild_bullish", SIDE_CE
    if score <= -55:
        return "strong_bearish", SIDE_PE
    if score <= -20:
        return "mild_bearish", SIDE_PE
    return "neutral_sideways", SIDE_WAIT


def _component_conflict(contributions: dict[str, float]) -> float:
    positive_sum = sum(value for value in contributions.values() if value > 0)
    negative_sum = abs(sum(value for value in contributions.values() if value < 0))
    if positive_sum > 25 and negative_sum > 25:
        return min(30.0, min(positive_sum, negative_sum) * 0.5)
    return 0.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
