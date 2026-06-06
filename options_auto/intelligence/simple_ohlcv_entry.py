from __future__ import annotations

from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT


SIMPLE_ENTRY_MODES = {"OHLCV_VOLUME", "OHLCV_VOLUME_PROFILE", "SIMPLE_OHLCV", "MAIN_APP_STYLE"}
FULL_ENTRY_MODE = "FULL_CONFIRMATION"
PROFILE_ENTRY_MODE = "PROFILE"
SIMPLE_ENTRY_MODE = "OHLCV_VOLUME_PROFILE"


def resolve_entry_dependency_mode(settings: dict[str, Any] | None = None) -> str:
    settings = dict(settings or {})
    raw_mode = str(settings.get("entry_dependency_mode") or PROFILE_ENTRY_MODE).strip().upper()
    if raw_mode in SIMPLE_ENTRY_MODES:
        return SIMPLE_ENTRY_MODE
    if raw_mode in {"FULL", "FULL_CONFIRMATION", "CONFIRMATION_STACK"}:
        return FULL_ENTRY_MODE
    profile = str(settings.get("strategy_profile") or "BALANCED").strip().upper()
    if profile == "AGGRESSIVE" and _bool(settings.get("aggressive_uses_simple_ohlcv_entry"), True):
        return SIMPLE_ENTRY_MODE
    return FULL_ENTRY_MODE


def simple_ohlcv_entry_enabled(settings: dict[str, Any] | None = None) -> bool:
    return resolve_entry_dependency_mode(settings) == SIMPLE_ENTRY_MODE


def simple_ohlcv_threshold(settings: dict[str, Any] | None = None) -> float:
    settings = dict(settings or {})
    if settings.get("simple_ohlcv_score_threshold") not in ("", None):
        return _number(settings.get("simple_ohlcv_score_threshold"), 50.0)
    return _number(settings.get("aggressive_ohlcv_score_threshold"), 50.0)


def resolve_simple_ohlcv_side(index_features: dict[str, Any] | None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    features = dict(index_features or {})
    settings = dict(settings or {})
    ce_score = _directional_index_score(SIDE_CE, features, settings)
    pe_score = _directional_index_score(SIDE_PE, features, settings)
    threshold = _number(settings.get("simple_ohlcv_side_score_threshold"), 45.0)
    side = SIDE_WAIT
    score = max(ce_score, pe_score)
    if ce_score >= pe_score and ce_score >= threshold:
        side = SIDE_CE
    elif pe_score > ce_score and pe_score >= threshold:
        side = SIDE_PE
    return {
        "side": side,
        "score": round(score, 2),
        "ce_score": round(ce_score, 2),
        "pe_score": round(pe_score, 2),
        "threshold": threshold,
        "reason": (
            "Simple OHLCV/volume-profile side selected."
            if side != SIDE_WAIT
            else "Simple OHLCV/volume-profile side did not pass threshold."
        ),
    }


def score_simple_ohlcv_entry(
    candidate: dict[str, Any],
    context: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = dict(candidate or {})
    context = dict(context or {})
    settings = dict(settings or {})
    side = str(context.get("selected_side") or candidate.get("option_type") or "").upper()
    features = dict(context.get("index_features") or {})
    index_score = _directional_index_score(side, features, settings)
    option_score = _option_premium_score(candidate, settings)
    volume_profile_score = _volume_profile_score(side, features, candidate)
    quote_quality_score = _quote_quality_score(candidate)
    breakdown = {
        "index_ohlcv_direction": index_score,
        "option_premium_ohlcv": option_score["score"],
        "volume_profile": volume_profile_score,
        "quote_quality": quote_quality_score,
    }
    weights = {
        "index_ohlcv_direction": 0.42,
        "option_premium_ohlcv": 0.28,
        "volume_profile": 0.20,
        "quote_quality": 0.10,
    }
    score = sum(breakdown[key] * weights[key] for key in weights)
    return {
        "score": round(_clamp(score), 2),
        "breakdown": {key: round(value, 2) for key, value in breakdown.items()},
        "weights": weights,
        "entry_dependency_mode": SIMPLE_ENTRY_MODE,
        "entry_dependency_reason": "Aggressive simple entry uses index OHLCV, volume profile/VWAP, and option premium OHLCV when available.",
        "warnings": option_score["warnings"],
    }


def _directional_index_score(side: str, features: dict[str, Any], settings: dict[str, Any]) -> float:
    side = str(side or "").upper()
    close = _number(features.get("close"))
    open_ = _number(features.get("open"))
    high = _number(features.get("high"))
    low = _number(features.get("low"))
    vwap = _number(features.get("vwap"))
    trend = _number(features.get("trend_strength_score"))
    relative_volume = _number(features.get("relative_volume"))
    body_pct = _number(features.get("body_pct"))
    close_position = _close_position(close, high, low)
    min_rvol = _number(settings.get("simple_ohlcv_min_relative_volume"), 0.8)

    score = 0.0
    if side == SIDE_CE:
        if close > open_ > 0:
            score += 18
        if close > vwap > 0:
            score += 24
        if close_position >= 60:
            score += 18
        if trend >= 20:
            score += 16
        if trend >= 45:
            score += 8
    elif side == SIDE_PE:
        if 0 < close < open_:
            score += 18
        if 0 < close < vwap:
            score += 24
        if close_position <= 40 and close > 0:
            score += 18
        if trend <= -20:
            score += 16
        if trend <= -45:
            score += 8
    else:
        return 0.0

    if relative_volume >= min_rvol:
        score += 10
    if relative_volume >= 1.2:
        score += 6
    if body_pct >= 25:
        score += 8
    if body_pct >= 45:
        score += 4
    return _clamp(score)


def _option_premium_score(candidate: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    candle = dict(candidate.get("candle") or {})
    ltp = _number(candidate.get("ltp"), candidate.get("last_price"))
    close = _number(candle.get("close"), ltp)
    open_ = _number(candle.get("open"))
    high = _number(candle.get("high"))
    low = _number(candle.get("low"))
    vwap = _number(candidate.get("option_vwap"), _number(candidate.get("vwap"), _number(candle.get("vwap"))))
    ret1 = _number(candidate.get("premium_return_1"), _number((candidate.get("premium_momentum") or {}).get("premium_return_1")))
    ret3 = _number(candidate.get("premium_return_3"), _number((candidate.get("premium_momentum") or {}).get("premium_return_3")))
    relative_volume = _number(candidate.get("relative_volume"))
    momentum_score = _number(candidate.get("momentum_score"), candidate.get("premium_momentum_score"))
    has_ohlcv = open_ > 0 and high > 0 and low > 0 and close > 0
    min_rvol = _number(settings.get("simple_ohlcv_min_relative_volume"), 0.8)

    score = 0.0
    warnings: list[str] = []
    if has_ohlcv:
        if close > open_:
            score += 24
        if _close_position(close, high, low) >= 55:
            score += 18
        body_pct = abs(close - open_) / max(high - low, 0.01) * 100
        if body_pct >= 25:
            score += 10
        if close > vwap > 0:
            score += 16
    else:
        score += 48
        warnings.append("Option premium OHLCV unavailable; simple mode used fresh quote plus index OHLCV.")

    if ret1 > 0:
        score += 12
    if ret3 > 0:
        score += 10
    if relative_volume >= min_rvol:
        score += 8
    if relative_volume >= 1.2:
        score += 6
    if momentum_score > 0:
        score = max(score, min(100.0, momentum_score))
    return {"score": _clamp(score), "warnings": warnings}


def _volume_profile_score(side: str, features: dict[str, Any], candidate: dict[str, Any]) -> float:
    side = str(side or "").upper()
    close = _number(features.get("close"))
    vwap = _number(features.get("vwap"))
    option_ltp = _number(candidate.get("ltp"), candidate.get("last_price"))
    option_vwap = _number(candidate.get("option_vwap"), _number(candidate.get("vwap")))
    score = 50.0
    if side == SIDE_CE and close > vwap > 0:
        score += 25
    elif side == SIDE_PE and 0 < close < vwap:
        score += 25
    if option_ltp > option_vwap > 0:
        score += 25
    return _clamp(score)


def _quote_quality_score(candidate: dict[str, Any]) -> float:
    spread = _number(candidate.get("spread_pct"), 100.0)
    total_depth = _number(candidate.get("total_depth"), _number(candidate.get("bid_qty")) + _number(candidate.get("ask_qty")))
    score = 100.0
    if spread > 0.25:
        score -= min(50.0, spread * 40.0)
    if total_depth <= 0:
        score -= 20
    elif total_depth < 500:
        score -= 10
    return _clamp(score)


def _close_position(close: float, high: float, low: float) -> float:
    candle_range = high - low
    if candle_range <= 0:
        return 50.0
    return (close - low) / candle_range * 100.0


def _bool(value: Any, default: bool) -> bool:
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, _number(value)))
