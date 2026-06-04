from __future__ import annotations

from dataclasses import dataclass
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

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class RegimeClassifier:
    def classify(self, features: dict[str, Any] | None = None, market_cue: dict[str, Any] | None = None) -> RegimeDecision:
        features = dict(features or {})
        market_cue = dict(market_cue or {})
        score = 0.0
        score += float(features.get("ema_alignment_score") or 0)
        score += float(features.get("vwap_score") or 0)
        score += float(features.get("rsi_slope_score") or 0)
        score += float(features.get("volume_score") or 0)
        score += float(features.get("depth_score") or 0)
        cue_side = market_cue.get("recommended_side")
        if cue_side == SIDE_CE:
            score += 12
        elif cue_side == SIDE_PE:
            score -= 12
        if features.get("news_warning"):
            score *= 0.75
        atr_expansion = float(features.get("atr_expansion") or 0)
        if abs(score) < 12 and atr_expansion > 1.8:
            return RegimeDecision("volatile_choppy", 62.0, SIDE_WAIT, "low", 0.8, 0.7, "FAST_TIGHT", "Volatility is high without directional alignment.")
        if score >= 45:
            return RegimeDecision("strong_bullish", min(100.0, 55 + score * 0.5), SIDE_CE, "high", 1.8, 1.0, "TRAIL_AFTER_1R")
        if score >= 18:
            return RegimeDecision("mild_bullish", min(90.0, 50 + score * 0.6), SIDE_CE, "medium", 1.4, 0.9, "BREAKEVEN_THEN_TRAIL")
        if score <= -45:
            return RegimeDecision("strong_bearish", min(100.0, 55 + abs(score) * 0.5), SIDE_PE, "high", 1.8, 1.0, "TRAIL_AFTER_1R")
        if score <= -18:
            return RegimeDecision("mild_bearish", min(90.0, 50 + abs(score) * 0.6), SIDE_PE, "medium", 1.4, 0.9, "BREAKEVEN_THEN_TRAIL")
        return RegimeDecision("neutral_sideways", 55.0, SIDE_WAIT, "low", 0.8, 0.7, "NO_NEW_TRADE", "No directional edge.")

