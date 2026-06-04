from __future__ import annotations

from dataclasses import dataclass
from typing import Any


POSITIVE_WORDS = {"rally", "gain", "surge", "positive", "growth", "strong", "record", "beat", "inflow"}
NEGATIVE_WORDS = {"fall", "drop", "weak", "negative", "selloff", "crash", "miss", "outflow", "inflation"}
HIGH_IMPACT_WORDS = {"rbi", "rate", "inflation", "budget", "election", "war", "fed", "crude", "currency"}
INDEX_WORDS = {"nifty", "sensex", "bank", "market", "index", "india", "global"}


@dataclass
class NewsSentimentEngine:
    max_items: int = 25

    def classify_items(self, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        scored = [self.classify_item(item) for item in list(items or [])[: self.max_items]]
        relevant = [item for item in scored if item["relevance"] > 0]
        if not relevant:
            return {"sentiment": "neutral", "score": 0.0, "high_impact": False, "items": scored}
        score = sum(item["score"] for item in relevant) / len(relevant)
        high_impact = any(item["high_impact"] for item in relevant)
        if score > 0.25:
            sentiment = "positive"
        elif score < -0.25:
            sentiment = "negative"
        else:
            sentiment = "neutral"
        return {
            "sentiment": sentiment,
            "score": round(max(-5.0, min(5.0, score * 5.0)), 2),
            "high_impact": high_impact,
            "items": scored,
        }

    def classify_item(self, item: dict[str, Any]) -> dict[str, Any]:
        title = str(item.get("title") or item.get("headline") or "")
        summary = str(item.get("summary") or item.get("description") or "")
        text = f"{title} {summary}".lower()
        positive = sum(1 for word in POSITIVE_WORDS if word in text)
        negative = sum(1 for word in NEGATIVE_WORDS if word in text)
        relevance = sum(1 for word in INDEX_WORDS if word in text)
        high_impact = any(word in text for word in HIGH_IMPACT_WORDS)
        raw_score = positive - negative
        return {
            **item,
            "sentiment": "positive" if raw_score > 0 else "negative" if raw_score < 0 else "neutral",
            "score": max(-1.0, min(1.0, raw_score / 3.0)),
            "relevance": relevance,
            "high_impact": high_impact,
        }

