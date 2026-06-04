from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Any

STRONG_BULLISH = "STRONG_BULLISH"
MILD_BULLISH = "MILD_BULLISH"
SIDEWAYS = "SIDEWAYS"
MILD_BEARISH = "MILD_BEARISH"
STRONG_BEARISH = "STRONG_BEARISH"

STRONG_TRENDING = "STRONG_TRENDING"
SLOW_TRENDING = "SLOW_TRENDING"
RANGE_BOUND = "RANGE_BOUND"
CHOPPY = "CHOPPY"
HIGH_VOLATILITY = "HIGH_VOLATILITY"
LOW_VOLUME = "LOW_VOLUME"
NEWS_DRIVEN = "NEWS_DRIVEN"
TRAP_HEAVY = "TRAP_HEAVY"


@dataclass(frozen=True)
class MarketCue:
    phase: str
    state: str
    regime: str
    score: float
    long_bonus: float
    short_bonus: float
    nifty_trend: str
    sector_trend: str
    global_cue: str
    fii_dii_used: bool
    ignored_sources: list[str]
    algo_adjustment: str
    source_breakdown: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_market_cue(payload: dict | None = None, current_time: datetime | None = None) -> MarketCue:
    payload = payload or {}
    phase = _phase(payload, current_time)
    score = 0.0
    breakdown: dict[str, Any] = {}

    nifty_score = _trend_score(payload.get("nifty_trend") or payload.get("market_trend") or payload.get("trend"))
    score += nifty_score * 2.0
    breakdown["nifty_trend_score"] = nifty_score

    sector_score = _trend_score(payload.get("sector_trend"))
    score += sector_score
    breakdown["sector_trend_score"] = sector_score

    global_score = _trend_score(payload.get("global_cue") or payload.get("global_trend"))
    score += global_score * (0.5 if phase != "MORNING" else 1.0)
    breakdown["global_cue_score"] = global_score

    technical_score = _technical_score(payload)
    score += technical_score
    breakdown["technical_score"] = technical_score

    fii_dii_used = phase == "MORNING"
    ignored_sources = []
    if fii_dii_used:
        fii_dii_score = _fii_dii_score(payload.get("fii_dii") or payload.get("fii_dii_cue"))
        score += fii_dii_score
        breakdown["fii_dii_score"] = fii_dii_score
    else:
        ignored_sources.append("FII/DII previous-day data ignored outside morning cue")
        breakdown["fii_dii_score"] = 0.0

    state = _state_from_score(score)
    regime = _regime(payload, state)
    long_bonus, short_bonus = _bonuses(state, regime)
    return MarketCue(
        phase=phase,
        state=state,
        regime=regime,
        score=round(score, 2),
        long_bonus=long_bonus,
        short_bonus=short_bonus,
        nifty_trend=str(payload.get("nifty_trend") or payload.get("market_trend") or "Neutral"),
        sector_trend=str(payload.get("sector_trend") or "Unavailable"),
        global_cue=str(payload.get("global_cue") or payload.get("global_trend") or "Unavailable"),
        fii_dii_used=fii_dii_used,
        ignored_sources=ignored_sources,
        algo_adjustment=_adjustment(state, regime),
        source_breakdown=breakdown,
    )


def _phase(payload: dict, current_time: datetime | None) -> str:
    explicit = str(payload.get("market_phase") or payload.get("cue_phase") or "").strip().upper()
    if explicit in {"MORNING", "MIDDAY", "AFTERNOON"}:
        return explicit
    now = current_time or datetime.now()
    current = now.time()
    if current < time(11, 0):
        return "MORNING"
    if current < time(13, 30):
        return "MIDDAY"
    return "AFTERNOON"


def _trend_score(value: Any) -> float:
    text = str(value or "").strip().upper()
    if text in {"STRONG_BULLISH", "BULLISH", "UP", "POSITIVE"}:
        return 2.0 if text.startswith("STRONG") else 1.0
    if text in {"MILD_BULLISH", "SLIGHTLY_BULLISH"}:
        return 0.5
    if text in {"STRONG_BEARISH", "BEARISH", "DOWN", "NEGATIVE"}:
        return -2.0 if text.startswith("STRONG") else -1.0
    if text in {"MILD_BEARISH", "SLIGHTLY_BEARISH"}:
        return -0.5
    return 0.0


def _technical_score(payload: dict) -> float:
    score = 0.0
    price = _float(payload.get("nifty_price") or payload.get("ltp"))
    vwap = _float(payload.get("nifty_vwap") or payload.get("vwap"))
    ema20 = _float(payload.get("nifty_ema20") or payload.get("ema20"))
    ema50 = _float(payload.get("nifty_ema50") or payload.get("ema50"))
    rsi = _float(payload.get("nifty_rsi") or payload.get("rsi"))
    opening_high = _float(payload.get("opening_range_high"))
    opening_low = _float(payload.get("opening_range_low"))
    if price and vwap:
        score += 1.0 if price > vwap else -1.0
    if ema20 and ema50:
        score += 0.75 if ema20 > ema50 else -0.75
    if rsi:
        if rsi >= 60:
            score += 0.75
        elif rsi <= 40:
            score -= 0.75
    if price and opening_high and price > opening_high:
        score += 0.75
    if price and opening_low and price < opening_low:
        score -= 0.75
    return score


def _fii_dii_score(value: Any) -> float:
    if isinstance(value, dict):
        fii = _float(value.get("fii_net"))
        dii = _float(value.get("dii_net"))
        combined = fii + dii
        if combined > 0:
            return 0.75
        if combined < 0:
            return -0.75
        return 0.0
    return _trend_score(value) * 0.5


def _state_from_score(score: float) -> str:
    if score >= 4:
        return STRONG_BULLISH
    if score >= 1:
        return MILD_BULLISH
    if score <= -4:
        return STRONG_BEARISH
    if score <= -1:
        return MILD_BEARISH
    return SIDEWAYS


def _regime(payload: dict, state: str) -> str:
    explicit = str(payload.get("market_regime") or "").strip().upper()
    if explicit:
        return explicit
    volatility = str(payload.get("volatility") or "").strip().upper()
    if volatility in {"HIGH", "HIGH_VOLATILITY"}:
        return HIGH_VOLATILITY
    if str(payload.get("trap_heavy") or "").strip().lower() in {"1", "true", "yes"}:
        return TRAP_HEAVY
    if str(payload.get("news_driven") or "").strip().lower() in {"1", "true", "yes"}:
        return NEWS_DRIVEN
    if _float(payload.get("relative_volume")) and _float(payload.get("relative_volume")) < 0.7:
        return LOW_VOLUME
    if state in {STRONG_BULLISH, STRONG_BEARISH}:
        return STRONG_TRENDING
    if state in {MILD_BULLISH, MILD_BEARISH}:
        return SLOW_TRENDING
    return RANGE_BOUND


def _bonuses(state: str, regime: str) -> tuple[float, float]:
    if state == STRONG_BULLISH:
        long_bonus, short_bonus = 3.0, -3.0
    elif state == MILD_BULLISH:
        long_bonus, short_bonus = 1.5, -1.0
    elif state == STRONG_BEARISH:
        long_bonus, short_bonus = -3.0, 3.0
    elif state == MILD_BEARISH:
        long_bonus, short_bonus = -1.0, 1.5
    else:
        long_bonus, short_bonus = 0.0, 0.0
    if regime in {CHOPPY, HIGH_VOLATILITY, LOW_VOLUME, TRAP_HEAVY}:
        long_bonus *= 0.5
        short_bonus *= 0.5
    return long_bonus, short_bonus


def _adjustment(state: str, regime: str) -> str:
    if regime == HIGH_VOLATILITY:
        return "Reduce quantity and require cleaner structure."
    if regime == LOW_VOLUME:
        return "Allow only A or A+ setups."
    if regime == TRAP_HEAVY:
        return "Avoid breakout-only entries; wait for retest/acceptance."
    if state == STRONG_BULLISH:
        return "Prefer long setups; reduce short aggressiveness."
    if state == STRONG_BEARISH:
        return "Prefer short setups; reduce long aggressiveness."
    if state == SIDEWAYS:
        return "Prefer VWAP/POC/VAH/VAL mean reversion; avoid chasing."
    return "Prefer aligned setups but allow exceptional stock-specific strength."


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
