from __future__ import annotations

from typing import Any


class TradeScoreEngine:
    def score(self, candidate: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = dict(context or {})
        breakdown = {
            "regime_alignment": float(context.get("regime_alignment") or 0),
            "market_cue": float(context.get("market_cue_score") or 0),
            "momentum": float(candidate.get("momentum_score") or 0),
            "liquidity": float(candidate.get("liquidity_score") or 0),
            "spread_depth": float(candidate.get("spread_depth_score") or 0),
            "affordability": float(candidate.get("affordability_score") or 0),
            "theta_risk": float(candidate.get("theta_score") or 0),
            "time_of_day": float(context.get("time_of_day_score") or 0),
            "news": max(-5.0, min(5.0, float(context.get("news_score") or 0))),
        }
        weights = {
            "regime_alignment": 0.16,
            "market_cue": 0.10,
            "momentum": 0.14,
            "liquidity": 0.20,
            "spread_depth": 0.14,
            "affordability": 0.10,
            "theta_risk": 0.08,
            "time_of_day": 0.05,
            "news": 0.03,
        }
        total = 0.0
        for key, value in breakdown.items():
            normalized = value if key == "news" else max(0.0, min(100.0, value))
            if key == "news":
                normalized = 50.0 + normalized * 10.0
            total += normalized * weights[key]
        return {"score": round(max(0.0, min(100.0, total)), 2), "breakdown": breakdown}

