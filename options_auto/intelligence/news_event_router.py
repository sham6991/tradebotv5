from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


HIGH_IMPACT_KEYWORDS = {
    "rbi",
    "repo rate",
    "rate hike",
    "rate cut",
    "inflation",
    "cpi",
    "budget",
    "election",
    "fed",
    "fomc",
    "war",
    "attack",
    "ceasefire",
    "crude",
    "oil",
    "rupee",
    "currency",
    "us yields",
    "bond yield",
    "sebi",
}

SHOCK_KEYWORDS = {
    "crash",
    "plunge",
    "collapse",
    "panic",
    "selloff",
    "war",
    "attack",
    "sanction",
    "emergency",
    "surprise",
    "rate hike",
    "rate cut",
}

INDEX_KEYWORDS = {
    "nifty",
    "sensex",
    "bank nifty",
    "banknifty",
    "india",
    "stock market",
    "equity market",
    "f&o",
    "options",
}


@dataclass
class NewsEventSignal:
    provider: str
    status: str
    score: float = 0.0
    severity: str = "NONE"
    event_type: str = "NONE"
    would_block: bool = False
    market_confirmation: bool = False
    reason: str = ""
    matched_headlines: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    newest_item_age_minutes: float | None = None
    fetched_at: str = ""
    fetched_at_epoch: float = 0.0
    cache_status: str = ""
    error: str = ""
    item_count: int = 0
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "score": round(float(self.score or 0.0), 2),
            "severity": self.severity,
            "event_type": self.event_type,
            "would_block": bool(self.would_block),
            "market_confirmation": bool(self.market_confirmation),
            "market_confirmed": bool(self.market_confirmation),
            "confirmed_by_market": bool(self.market_confirmation),
            "reason": self.reason,
            "matched_headlines": list(self.matched_headlines or []),
            "matched_keywords": list(self.matched_keywords or []),
            "newest_item_age_minutes": self.newest_item_age_minutes,
            "fetched_at": self.fetched_at,
            "fetched_at_epoch": self.fetched_at_epoch,
            "cache_status": self.cache_status,
            "error": self.error,
            "item_count": int(self.item_count or 0),
            "stale": bool(self.stale),
        }


class NewsEventRouter:
    def route(
        self,
        provider_result: dict[str, Any],
        settings: dict[str, Any] | None = None,
        *,
        market_context: dict[str, Any] | None = None,
    ) -> NewsEventSignal:
        settings = dict(settings or {})
        provider_result = dict(provider_result or {})
        if not bool(settings.get("news_event_enabled", True)):
            return NewsEventSignal(
                provider=str(provider_result.get("provider") or settings.get("news_event_provider") or "ZERODHA_PULSE"),
                status="DISABLED",
                severity="NONE",
                reason="News/event scanning is disabled in Options Auto settings.",
            )

        provider = str(provider_result.get("provider") or settings.get("news_event_provider") or "ZERODHA_PULSE")
        status = str(provider_result.get("status") or "FETCH_FAILED").upper()
        items = [dict(item or {}) for item in list(provider_result.get("items") or [])]
        if status in {"FETCH_FAILED", "PARSE_FAILED"}:
            return NewsEventSignal(
                provider=provider,
                status=status,
                severity="NONE",
                reason=provider_result.get("error") or "News provider did not return a usable signal.",
                fetched_at=str(provider_result.get("fetched_at") or ""),
                fetched_at_epoch=float(provider_result.get("fetched_at_epoch") or 0.0),
                cache_status=str(provider_result.get("cache_status") or ""),
                error=str(provider_result.get("error") or ""),
                item_count=len(items),
                stale=bool(provider_result.get("stale")),
            )

        scored = self._score_items(items)
        min_warning = float(settings.get("news_event_min_score_for_warning") or 40.0)
        min_shock = float(settings.get("news_event_min_score_for_shock") or 70.0)
        require_confirmation = bool(settings.get("news_event_require_market_confirmation", True))
        market_confirmation = self._market_confirmation(market_context or {})
        score = min(100.0, sum(item["score"] for item in scored[:5]))
        matched = [item for item in scored if item["score"] > 0]
        matched_keywords = sorted({keyword for item in matched for keyword in item["matched_keywords"]})
        matched_headlines = [item["title"] for item in matched[:3]]
        newest_age = self._newest_age(items, provider_result)
        if not matched or score < min_warning:
            return NewsEventSignal(
                provider=provider,
                status="NO_RELEVANT_NEWS",
                score=score,
                severity="NONE",
                event_type="NONE",
                reason="No relevant Zerodha Pulse market shock headline crossed the warning threshold.",
                matched_headlines=matched_headlines,
                matched_keywords=matched_keywords,
                newest_item_age_minutes=newest_age,
                fetched_at=str(provider_result.get("fetched_at") or ""),
                fetched_at_epoch=float(provider_result.get("fetched_at_epoch") or 0.0),
                cache_status=str(provider_result.get("cache_status") or "NETWORK"),
                item_count=len(items),
                stale=bool(provider_result.get("stale")),
            )

        confirmed = market_confirmation or not require_confirmation
        is_shock = score >= min_shock and confirmed
        severity = "SHOCK" if is_shock else "WARNING"
        status_text = "NEWS_EVENT_SHOCK" if is_shock else "NEWS_WARNING"
        event_type = self._event_type(matched_keywords)
        return NewsEventSignal(
            provider=provider,
            status=status_text,
            score=score,
            severity=severity,
            event_type=event_type,
            would_block=is_shock,
            market_confirmation=market_confirmation,
            reason=self._reason(status_text, score, matched_headlines, require_confirmation, market_confirmation),
            matched_headlines=matched_headlines,
            matched_keywords=matched_keywords,
            newest_item_age_minutes=newest_age,
            fetched_at=str(provider_result.get("fetched_at") or ""),
            fetched_at_epoch=float(provider_result.get("fetched_at_epoch") or 0.0),
            cache_status=str(provider_result.get("cache_status") or "NETWORK"),
            item_count=len(items),
            stale=bool(provider_result.get("stale")),
        )

    def _score_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scored: list[dict[str, Any]] = []
        for item in items:
            title = str(item.get("title") or item.get("headline") or "")
            summary = str(item.get("summary") or "")
            text = f"{title} {summary}".lower()
            keywords = [word for word in HIGH_IMPACT_KEYWORDS | SHOCK_KEYWORDS | INDEX_KEYWORDS if word in text]
            index_hits = [word for word in INDEX_KEYWORDS if word in text]
            shock_hits = [word for word in SHOCK_KEYWORDS if word in text]
            high_hits = [word for word in HIGH_IMPACT_KEYWORDS if word in text]
            score = len(index_hits) * 8 + len(high_hits) * 14 + len(shock_hits) * 18
            if shock_hits and high_hits:
                score += 12
            if index_hits and (shock_hits or high_hits):
                score += 10
            if score:
                scored.append({"title": title, "score": float(score), "matched_keywords": keywords, "item": item})
        return sorted(scored, key=lambda item: item["score"], reverse=True)

    def _market_confirmation(self, context: dict[str, Any]) -> bool:
        features = dict(context.get("index_features") or context.get("features") or {})
        market_cue = dict(context.get("market_cue") or {})
        feed = dict(context.get("feed_health") or {})
        trend = abs(_number(features.get("trend_strength_score"), _number(market_cue.get("score"))))
        atr_pct = _number(features.get("atr_pct"))
        cue_conf = _number(market_cue.get("confidence"))
        if trend >= 55 or atr_pct >= 0.45 or cue_conf >= 80:
            return True
        if feed.get("feed_stale") or feed.get("stale"):
            return False
        return bool(context.get("market_confirmation"))

    def _newest_age(self, items: list[dict[str, Any]], provider_result: dict[str, Any]) -> float | None:
        ages = [_number(item.get("age_minutes"), None) for item in items]
        ages = [age for age in ages if age is not None]
        if ages:
            return round(min(ages), 2)
        fetched = float(provider_result.get("fetched_at_epoch") or 0.0)
        if fetched > 0:
            return round(max(0.0, (time.time() - fetched) / 60.0), 2)
        return None

    def _event_type(self, keywords: list[str]) -> str:
        lowered = {str(item).lower() for item in keywords}
        if {"rbi", "repo rate", "rate hike", "rate cut", "fed", "fomc"} & lowered:
            return "RATE_POLICY"
        if {"war", "attack", "ceasefire", "sanction"} & lowered:
            return "GEOPOLITICAL"
        if {"crude", "oil", "rupee", "currency", "us yields", "bond yield"} & lowered:
            return "MACRO_MARKET"
        if {"sebi", "budget", "election"} & lowered:
            return "DOMESTIC_POLICY"
        return "MARKET_NEWS"

    def _reason(self, status: str, score: float, headlines: list[str], require_confirmation: bool, confirmed: bool) -> str:
        headline = headlines[0] if headlines else "Relevant headline"
        if status == "NEWS_EVENT_SHOCK":
            return f"{headline} scored {score:.0f}; market confirmation is {'present' if confirmed else 'not required'}."
        if require_confirmation and not confirmed:
            return f"{headline} scored {score:.0f}, but market confirmation is not present, so it remains report-only."
        return f"{headline} scored {score:.0f}; warning only."


def _number(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
