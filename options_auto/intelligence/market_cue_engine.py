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
    components: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    last_updated: str = field(default_factory=iso_now)
    next_refresh: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
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
        phase = str(phase or payload.get("phase") or "").upper()
        technical = float(payload.get("technical_score") or payload.get("trend_score") or 0)
        news = float(payload.get("news_score") or 0)
        option_oi = float(payload.get("option_oi_score") or payload.get("oi_score") or 0)
        fii_dii = float(payload.get("fii_dii_score") or 0) if phase in {"PREMARKET", "PRE_MARKET", ""} else 0.0
        score = max(-100.0, min(100.0, technical + option_oi + min(5.0, max(-5.0, news)) + fii_dii))
        if score >= 55:
            cue, side = "strong_bullish", SIDE_CE
        elif score >= 20:
            cue, side = "mild_bullish", SIDE_CE
        elif score <= -55:
            cue, side = "strong_bearish", SIDE_PE
        elif score <= -20:
            cue, side = "mild_bearish", SIDE_PE
        elif abs(score) <= 8:
            cue, side = "neutral_sideways", SIDE_WAIT
        else:
            cue, side = "volatile_uncertain", SIDE_WAIT
        confidence = min(100.0, 45.0 + abs(score) * 0.55)
        components = {
            "technical": technical,
            "option_oi": option_oi,
            "news": news,
            "fii_dii": fii_dii,
        }
        return MarketCue(
            cue=cue,
            score=round(score, 2),
            confidence=round(confidence, 2),
            recommended_side=side,
            components=components,
            reason=f"{cue.replace('_', ' ').title()} from score {score:.1f}.",
        )

