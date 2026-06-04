from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT


@dataclass
class RegimeDecision:
    regime: str
    confidence: float
    recommended_side: str
    aggressiveness: str
    target_multiplier: float
    stoploss_multiplier: float
    trailing_style: str
    no_trade_reason: str = ""
    score: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class RegimeClassifier:
    def classify(self, features: dict[str, Any] | None = None, market_cue: dict[str, Any] | None = None) -> RegimeDecision:
        features = dict(features or {})
        market_cue = dict(market_cue or {})
        warnings: list[str] = []
        confidence_adjustment = 0.0
        score = _number(features.get("trend_strength_score"), _legacy_feature_score(features))

        cue_side = market_cue.get("recommended_side")
        if cue_side == SIDE_CE:
            score += 15
        elif cue_side == SIDE_PE:
            score -= 15
        elif cue_side == SIDE_WAIT:
            score *= 0.8

        close = _number(features.get("close"))
        vwap = _number(features.get("vwap"))
        if close > 0 and vwap > 0:
            vwap_distance_pct = (close - vwap) / close * 100
            if vwap_distance_pct > 0.15:
                score += 10
            elif vwap_distance_pct < -0.15:
                score -= 10
        else:
            vwap_distance_pct = 0.0

        ema9 = _number(features.get("ema9"))
        ema20 = _number(features.get("ema20"))
        ema50 = _number(features.get("ema50"))
        if ema9 > ema20 > ema50:
            score += 20
        elif ema9 < ema20 < ema50:
            score -= 20
        if close > 0 and abs(ema9 - ema20) / close * 100 < 0.05:
            confidence_adjustment -= 15

        rsi = _number(features.get("rsi14"), 50.0)
        if 60 <= rsi <= 72:
            score += 10
        elif 50 <= rsi < 60:
            score += 5
        elif 40 <= rsi < 50:
            score -= 5
        elif 28 <= rsi < 40:
            score -= 10
        elif rsi > 78:
            warnings.append("RSI exhaustion warning.")
        elif rsi < 22:
            warnings.append("RSI exhaustion warning.")

        atr_pct = _number(features.get("atr_pct"))
        if atr_pct > 0.45 and abs(score) < 35:
            return RegimeDecision(
                "volatile_choppy",
                55.0,
                SIDE_WAIT,
                "low",
                0.8,
                0.7,
                "NO_NEW_TRADE",
                "ATR is high without a clear directional score.",
                round(score, 2),
                warnings,
            )

        open_ = _number(features.get("open"))
        if score > 0 and _number(features.get("upper_wick_pct")) > 45 and close < open_:
            score -= 20
        if score < 0 and _number(features.get("lower_wick_pct")) > 45 and close > open_:
            score += 20

        premium_strong = bool(market_cue.get("premium_expansion_confirmed") or market_cue.get("option_premium_behavior_score", 0) >= 60)
        if rsi > 78 and vwap_distance_pct > 0.6 and not premium_strong:
            return RegimeDecision(
                "trend_exhaustion",
                50.0,
                SIDE_WAIT,
                "low",
                0.8,
                0.7,
                "NO_NEW_TRADE",
                "RSI and VWAP extension show bullish exhaustion.",
                round(score, 2),
                warnings,
            )
        if rsi < 22 and vwap_distance_pct < -0.6 and not premium_strong:
            return RegimeDecision(
                "trend_exhaustion",
                50.0,
                SIDE_WAIT,
                "low",
                0.8,
                0.7,
                "NO_NEW_TRADE",
                "RSI and VWAP extension show bearish exhaustion.",
                round(score, 2),
                warnings,
            )

        return _classified_regime(score, confidence_adjustment, warnings)


def _classified_regime(score: float, confidence_adjustment: float, warnings: list[str]) -> RegimeDecision:
    confidence = max(0.0, min(100.0, 45.0 + abs(score) * 0.55 + confidence_adjustment))
    score = max(-100.0, min(100.0, score))
    if score >= 70:
        return RegimeDecision("strong_bullish", round(confidence, 2), SIDE_CE, "high", 1.8, 1.0, "TRAIL_AFTER_1R", score=round(score, 2), warnings=warnings)
    if score >= 35:
        return RegimeDecision("mild_bullish", round(confidence, 2), SIDE_CE, "medium", 1.4, 0.9, "BREAKEVEN_THEN_TRAIL", score=round(score, 2), warnings=warnings)
    if score <= -70:
        return RegimeDecision("strong_bearish", round(confidence, 2), SIDE_PE, "high", 1.8, 1.0, "TRAIL_AFTER_1R", score=round(score, 2), warnings=warnings)
    if score <= -35:
        return RegimeDecision("mild_bearish", round(confidence, 2), SIDE_PE, "medium", 1.4, 0.9, "BREAKEVEN_THEN_TRAIL", score=round(score, 2), warnings=warnings)
    return RegimeDecision("neutral_sideways", round(confidence, 2), SIDE_WAIT, "low", 0.8, 0.7, "NO_NEW_TRADE", "No directional edge.", round(score, 2), warnings)


def _legacy_feature_score(features: dict[str, Any]) -> float:
    return (
        _number(features.get("ema_alignment_score"))
        + _number(features.get("vwap_score"))
        + _number(features.get("rsi_slope_score"))
        + _number(features.get("volume_score"))
        + _number(features.get("depth_score"))
    )


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
