from __future__ import annotations

from typing import Any

import pandas as pd

from options_auto.indicators.technicals import enrich_technicals


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def build_index_features(index_history: pd.DataFrame) -> dict[str, Any]:
    frame = enrich_technicals(index_history)
    if frame.empty:
        return {
            "close": 0.0,
            "ema9": 0.0,
            "ema20": 0.0,
            "ema50": 0.0,
            "vwap": 0.0,
            "rsi14": 50.0,
            "rsi_slope_3": 0.0,
            "atr14": 0.0,
            "atr_pct": 0.0,
            "relative_volume": 0.0,
            "body_pct": 0.0,
            "upper_wick_pct": 0.0,
            "lower_wick_pct": 0.0,
            "ema_alignment": "MIXED",
            "vwap_position": "AT_VWAP",
            "trend_strength_score": 0.0,
            "warmup_complete": False,
        }

    row = frame.iloc[-1]
    close = _float(row.get("close"))
    ema9 = _float(row.get("ema9"))
    ema20 = _float(row.get("ema20"))
    ema50 = _float(row.get("ema50"))
    vwap = _float(row.get("vwap"))
    rsi14 = _float(row.get("rsi14"), 50.0)
    rsi_3 = _float(frame.iloc[-4].get("rsi14"), rsi14) if len(frame) >= 4 else rsi14
    rsi_slope_3 = rsi14 - rsi_3
    atr14 = _float(row.get("atr14"))
    atr_pct = atr14 / close * 100 if close > 0 else 0.0
    relative_volume = _float(row.get("relative_volume"))
    body_pct = _float(row.get("body_pct"))
    upper_wick_pct = _float(row.get("upper_wick_pct"))
    lower_wick_pct = _float(row.get("lower_wick_pct"))
    open_ = _float(row.get("open"))

    if ema9 > ema20 > ema50:
        ema_alignment = "BULLISH"
    elif ema9 < ema20 < ema50:
        ema_alignment = "BEARISH"
    else:
        ema_alignment = "MIXED"

    vwap_distance_pct = abs(close - vwap) / close * 100 if close > 0 else 0.0
    if vwap_distance_pct <= 0.05:
        vwap_position = "AT_VWAP"
    elif close > vwap:
        vwap_position = "ABOVE_VWAP"
    else:
        vwap_position = "BELOW_VWAP"

    trend_strength_score = _trend_strength_score(
        close=close,
        open_=open_,
        vwap=vwap,
        ema_alignment=ema_alignment,
        rsi14=rsi14,
        rsi_slope_3=rsi_slope_3,
        relative_volume=relative_volume,
        upper_wick_pct=upper_wick_pct,
        lower_wick_pct=lower_wick_pct,
    )

    return {
        "close": round(close, 4),
        "ema9": round(ema9, 4),
        "ema20": round(ema20, 4),
        "ema50": round(ema50, 4),
        "vwap": round(vwap, 4),
        "rsi14": round(rsi14, 4),
        "rsi_slope_3": round(rsi_slope_3, 4),
        "atr14": round(atr14, 4),
        "atr_pct": round(atr_pct, 4),
        "relative_volume": round(relative_volume, 4),
        "body_pct": round(body_pct, 4),
        "upper_wick_pct": round(upper_wick_pct, 4),
        "lower_wick_pct": round(lower_wick_pct, 4),
        "ema_alignment": ema_alignment,
        "vwap_position": vwap_position,
        "trend_strength_score": round(trend_strength_score, 2),
        "warmup_complete": len(frame) >= 50,
    }


def _trend_strength_score(
    *,
    close: float,
    open_: float,
    vwap: float,
    ema_alignment: str,
    rsi14: float,
    rsi_slope_3: float,
    relative_volume: float,
    upper_wick_pct: float,
    lower_wick_pct: float,
) -> float:
    score = 0.0
    if close > vwap:
        score += 15
    elif close < vwap:
        score -= 15

    if ema_alignment == "BULLISH":
        score += 25
    elif ema_alignment == "BEARISH":
        score -= 25

    if 60 <= rsi14 <= 75:
        score += 15
    elif rsi14 > 75:
        score += 5
    elif 25 <= rsi14 <= 40:
        score -= 15
    elif rsi14 < 25:
        score -= 5

    if rsi_slope_3 > 3:
        score += 10
    elif rsi_slope_3 < -3:
        score -= 10

    if relative_volume >= 1.5 and score > 0:
        score += 10
    elif relative_volume >= 1.5 and score < 0:
        score -= 10
    elif relative_volume < 0.7:
        score = max(0.0, score - 10) if score > 0 else min(0.0, score + 10)

    if upper_wick_pct > 45 and close < open_:
        score -= 10
    if lower_wick_pct > 45 and close > open_:
        score += 10

    return max(-100.0, min(100.0, score))
