from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT


ALLOW = "ALLOW"
ALLOW_SELECTIVE = "ALLOW_SELECTIVE"
WAIT = "WAIT"
BLOCK = "BLOCK"
UNKNOWN = "UNKNOWN"

DISABLED = "DISABLED"
REPORT_ONLY = "REPORT_ONLY"
ENFORCED = "ENFORCED"


@dataclass
class MarketContextDecision:
    market_type: str
    playbook: str
    recommended_side: str
    confidence: float
    permission: str
    enforcement: str
    would_block: bool
    size_multiplier: float = 1.0
    threshold_adjustment: float = 0.0
    target_multiplier_adjustment: float = 0.0
    stoploss_multiplier_adjustment: float = 0.0
    max_holding_minutes: int | None = None
    reason: str = ""
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    raw_market_cue: dict[str, Any] = field(default_factory=dict)
    raw_regime: dict[str, Any] = field(default_factory=dict)
    news_event_signal: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_type": self.market_type,
            "playbook": self.playbook,
            "recommended_side": self.recommended_side,
            "confidence": round(float(self.confidence or 0), 2),
            "permission": self.permission,
            "enforcement": self.enforcement,
            "would_block": bool(self.would_block),
            "size_multiplier": float(self.size_multiplier or 1.0),
            "threshold_adjustment": float(self.threshold_adjustment or 0.0),
            "target_multiplier_adjustment": float(self.target_multiplier_adjustment or 0.0),
            "stoploss_multiplier_adjustment": float(self.stoploss_multiplier_adjustment or 0.0),
            "max_holding_minutes": self.max_holding_minutes,
            "reason": self.reason,
            "blockers": list(self.blockers or []),
            "warnings": list(self.warnings or []),
            "evidence": dict(self.evidence or {}),
            "raw_market_cue": dict(self.raw_market_cue or {}),
            "raw_regime": dict(self.raw_regime or {}),
            "news_event_signal": dict(self.news_event_signal or {}),
        }


class MarketContextRouter:
    def route(
        self,
        *,
        market_cue: dict[str, Any],
        regime: dict[str, Any],
        index_features: dict[str, Any],
        news_event_signal: dict[str, Any] | None,
        settings: dict[str, Any],
        timestamp: Any,
        recent_side_state: dict[str, Any] | None = None,
        feed_health: dict[str, Any] | None = None,
    ) -> MarketContextDecision:
        settings = dict(settings or {})
        market_cue = dict(market_cue or {})
        regime = dict(regime or {})
        features = dict(index_features or {})
        news = dict(news_event_signal or {})
        feed = dict(feed_health or {})
        evidence = self._evidence(market_cue, regime, features, news, feed, recent_side_state, timestamp, settings)
        side, side_confidence, side_warning = self._recommended_side(market_cue, regime, features)
        warnings = [side_warning] if side_warning else []

        market_type, confidence, reason = self._classify(evidence, side, side_confidence, settings)
        playbook, playbook_side, permission = self._playbook(market_type, side, settings)
        if playbook_side != side:
            side = playbook_side
        enforcement = self._enforcement(settings)
        would_block = permission in {WAIT, BLOCK} or (
            permission == UNKNOWN and bool(settings.get("market_context_unknown_blocks_when_enforced", True))
        )
        blockers = []
        if would_block:
            blockers.append(f"Market context would block trade: {market_type} / {playbook}")
        return MarketContextDecision(
            market_type=market_type,
            playbook=playbook,
            recommended_side=side,
            confidence=confidence,
            permission=permission,
            enforcement=enforcement,
            would_block=would_block,
            size_multiplier=self._size_multiplier(market_type),
            threshold_adjustment=self._threshold_adjustment(market_type),
            target_multiplier_adjustment=self._target_adjustment(market_type),
            stoploss_multiplier_adjustment=self._stop_adjustment(market_type),
            max_holding_minutes=self._max_holding_minutes(market_type, settings),
            reason=reason,
            blockers=blockers,
            warnings=list(dict.fromkeys(warnings)),
            evidence=evidence,
            raw_market_cue=market_cue,
            raw_regime=regime,
            news_event_signal=news,
        )

    def _classify(self, evidence: dict[str, Any], side: str, side_confidence: float, settings: dict[str, Any]) -> tuple[str, float, str]:
        if evidence["low_liquidity"]:
            return "LOW_LIQUIDITY", max(60.0, evidence["feed_confidence"]), "Low liquidity or stale data: fresh tradable option data is not available."
        if evidence["news_shock"]:
            return "NEWS_EVENT_SHOCK", max(70.0, evidence["news_score"]), "Explicit news/event shock is active and confirmed by available market evidence."
        if evidence["post_spike_trap"]:
            return "POST_SPIKE_PREMIUM_TRAP", max(65.0, side_confidence), "Premium spike or trap evidence is stronger than trend continuation."
        if evidence["volatile_chop"]:
            return "VOLATILE_CHOP", max(60.0, evidence["volatility_confidence"]), "Volatility is high without a clean directional market structure."
        if evidence["sideways_range"]:
            return "SIDEWAYS_RANGE", max(55.0, evidence["sideways_confidence"]), "Market is range-bound or neutral; no directional edge is clear."
        if evidence["trend_exhaustion"]:
            return "TREND_EXHAUSTION", max(55.0, side_confidence), "Trend exhaustion evidence is present; avoid chasing the move."
        if evidence["expiry_fast_scalp"] and side in {SIDE_CE, SIDE_PE}:
            return "EXPIRY_FAST_SCALP", max(55.0, side_confidence), "Expiry fast scalp conditions are enabled and directional evidence is present."
        if side == SIDE_CE and side_confidence >= 70:
            return "STRONG_BULL_TREND", side_confidence, "Bull trend confirmed by market cue/regime alignment and index structure."
        if side == SIDE_PE and side_confidence >= 70:
            return "STRONG_BEAR_TREND", side_confidence, "Bear trend confirmed by market cue/regime alignment and index structure."
        if side == SIDE_CE and side_confidence >= 50:
            return "MILD_BULL_TREND", side_confidence, "Bullish context exists, but it is selective rather than strong."
        if side == SIDE_PE and side_confidence >= 50:
            return "MILD_BEAR_TREND", side_confidence, "Bearish context exists, but it is selective rather than strong."
        return "UNKNOWN", min(side_confidence, 49.0), "Market context is unknown from available evidence."

    def _playbook(self, market_type: str, side: str, settings: dict[str, Any]) -> tuple[str, str, str]:
        if market_type == "LOW_LIQUIDITY":
            return "WAIT_LOW_LIQUIDITY", SIDE_WAIT, WAIT
        if market_type == "NEWS_EVENT_SHOCK":
            return "WAIT_NEWS_SHOCK", SIDE_WAIT, WAIT
        if market_type == "VOLATILE_CHOP":
            return "WAIT_VOLATILE_CHOP", SIDE_WAIT, WAIT
        if market_type == "SIDEWAYS_RANGE":
            return "WAIT_NO_TRADE", SIDE_WAIT, WAIT
        if market_type in {"TREND_EXHAUSTION", "POST_SPIKE_PREMIUM_TRAP"}:
            return "WAIT_TREND_EXHAUSTION", SIDE_WAIT, WAIT
        if market_type == "EXPIRY_FAST_SCALP":
            if side == SIDE_CE:
                return "EXPIRY_FAST_SCALP_CE", SIDE_CE, ALLOW_SELECTIVE
            if side == SIDE_PE:
                return "EXPIRY_FAST_SCALP_PE", SIDE_PE, ALLOW_SELECTIVE
        if market_type == "STRONG_BULL_TREND":
            return "LONG_CE_MOMENTUM", SIDE_CE, ALLOW
        if market_type == "STRONG_BEAR_TREND":
            return "LONG_PE_MOMENTUM", SIDE_PE, ALLOW
        if market_type == "MILD_BULL_TREND":
            return "SELECTIVE_CE_MOMENTUM", SIDE_CE, ALLOW_SELECTIVE
        if market_type == "MILD_BEAR_TREND":
            return "SELECTIVE_PE_MOMENTUM", SIDE_PE, ALLOW_SELECTIVE
        return "UNKNOWN_WAIT", SIDE_WAIT, UNKNOWN

    def _recommended_side(self, market_cue: dict[str, Any], regime: dict[str, Any], features: dict[str, Any]) -> tuple[str, float, str]:
        cue_side = _side(market_cue.get("recommended_side"))
        regime_side = _side(regime.get("recommended_side"))
        cue_conf = _number(market_cue.get("confidence"), abs(_number(market_cue.get("score"))) + 45)
        regime_conf = _number(regime.get("confidence"), abs(_number(regime.get("score"))) + 45)
        trend = _number(features.get("trend_strength_score"))
        feature_side = SIDE_CE if trend >= 35 else SIDE_PE if trend <= -35 else SIDE_WAIT
        feature_conf = min(100.0, 45.0 + abs(trend) * 0.55)
        scores = {SIDE_CE: 0.0, SIDE_PE: 0.0}
        for side, confidence, weight in ((cue_side, cue_conf, 0.45), (regime_side, regime_conf, 0.45), (feature_side, feature_conf, 0.10)):
            if side in scores:
                scores[side] += confidence * weight
        ce_score = scores[SIDE_CE]
        pe_score = scores[SIDE_PE]
        if ce_score <= 0 and pe_score <= 0:
            return SIDE_WAIT, 0.0, ""
        if ce_score > 0 and pe_score > 0 and abs(ce_score - pe_score) < 15.0:
            return SIDE_WAIT, max(ce_score, pe_score), "CE/PE context conflict is below the 15 point confidence gap."
        if ce_score >= pe_score:
            return SIDE_CE, ce_score, ""
        return SIDE_PE, pe_score, ""

    def _evidence(
        self,
        market_cue: dict[str, Any],
        regime: dict[str, Any],
        features: dict[str, Any],
        news: dict[str, Any],
        feed: dict[str, Any],
        recent_side_state: dict[str, Any] | None,
        timestamp: Any,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        trend = _number(features.get("trend_strength_score"), _number(regime.get("score")))
        atr_pct = _number(features.get("atr_pct"))
        close = _number(features.get("close"))
        vwap = _number(features.get("vwap"))
        vwap_distance_pct = abs((close - vwap) / close * 100) if close > 0 and vwap > 0 else 0.0
        cue_name = str(market_cue.get("cue") or "").lower()
        regime_name = str(regime.get("regime") or "").lower()
        feed_blockers = list(feed.get("blockers") or feed.get("data_gate_blockers") or [])
        missing_quotes = list(feed.get("missing_quote_keys") or [])
        low_liquidity = bool(
            feed.get("stale")
            or feed.get("feed_stale")
            or feed.get("quote_stale")
            or feed.get("quote_missing")
            or feed.get("last_error")
            or feed_blockers
            or missing_quotes
            or (_has_key(feed, "valid_quote_count") and _number(feed.get("valid_quote_count")) <= 0)
        )
        news_score = _number(news.get("score"))
        news_status = str(news.get("status") or "").upper()
        market_confirmed = bool(news.get("market_confirmation") or news.get("market_confirmed") or news.get("confirmed_by_market"))
        news_threshold = _number(settings.get("news_event_min_score_for_shock"), 70.0)
        require_market_confirmation = bool(settings.get("news_event_require_market_confirmation", True))
        news_confirmed = market_confirmed or not require_market_confirmation
        news_shock = news_status == "NEWS_EVENT_SHOCK" and news_score >= news_threshold and news_confirmed
        return {
            "timestamp": str(timestamp or ""),
            "trend_strength_score": trend,
            "atr_pct": atr_pct,
            "vwap_distance_pct": round(vwap_distance_pct, 4),
            "relative_volume": _number(features.get("relative_volume")),
            "market_cue_name": cue_name,
            "regime_name": regime_name,
            "low_liquidity": low_liquidity,
            "feed_confidence": 80.0 if low_liquidity else 0.0,
            "feed_health": feed,
            "news_status": news_status,
            "news_score": news_score,
            "news_market_confirmation": market_confirmed,
            "news_event_min_score_for_shock": news_threshold,
            "news_event_require_market_confirmation": require_market_confirmation,
            "news_shock": news_shock,
            "post_spike_trap": bool(market_cue.get("premium_trap") or market_cue.get("post_spike_trap") or features.get("post_spike_premium_trap")),
            "volatile_chop": regime_name == "volatile_choppy" or cue_name == "volatile_uncertain" or (atr_pct > 0.45 and abs(trend) < 35),
            "volatility_confidence": min(100.0, 45.0 + atr_pct * 80.0),
            "sideways_range": regime_name in {"neutral_sideways", "sideways_range"} or (abs(trend) < 25 and vwap_distance_pct < 0.15),
            "sideways_confidence": max(50.0, 70.0 - abs(trend)),
            "trend_exhaustion": regime_name == "trend_exhaustion" or any("exhaustion" in str(item).lower() for item in regime.get("warnings") or []),
            "expiry_fast_scalp": bool(
                settings.get("market_context_expiry_scalp_enabled")
                or features.get("expiry_fast_scalp")
                or features.get("expiry_scalp")
                or market_cue.get("expiry_fast_scalp")
            ),
            "recent_side_state": dict(recent_side_state or {}),
        }

    def _enforcement(self, settings: dict[str, Any]) -> str:
        if not bool(settings.get("market_context_enabled", True)):
            return DISABLED
        if bool(settings.get("market_context_enforcement_enabled", False)):
            return ENFORCED
        return REPORT_ONLY

    def _size_multiplier(self, market_type: str) -> float:
        if market_type.startswith("STRONG_"):
            return 1.0
        if market_type.startswith("MILD_") or market_type == "EXPIRY_FAST_SCALP":
            return 0.75
        return 0.0

    def _threshold_adjustment(self, market_type: str) -> float:
        if market_type.startswith("STRONG_"):
            return -2.0
        if market_type.startswith("MILD_"):
            return 5.0
        if market_type == "EXPIRY_FAST_SCALP":
            return 3.0
        return 0.0

    def _target_adjustment(self, market_type: str) -> float:
        if market_type.startswith("STRONG_"):
            return 0.15
        if market_type == "EXPIRY_FAST_SCALP":
            return -0.25
        return 0.0

    def _stop_adjustment(self, market_type: str) -> float:
        if market_type == "EXPIRY_FAST_SCALP":
            return -0.15
        return 0.0

    def _max_holding_minutes(self, market_type: str, settings: dict[str, Any]) -> int | None:
        if market_type == "EXPIRY_FAST_SCALP":
            return int(_number(settings.get("market_context_expiry_scalp_max_holding_minutes"), 8))
        return None


def _side(value: Any) -> str:
    side = str(value or "").upper()
    return side if side in {SIDE_CE, SIDE_PE, SIDE_WAIT} else SIDE_WAIT


def _has_key(mapping: dict[str, Any], key: str) -> bool:
    return key in mapping and mapping.get(key) not in ("", None)


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
