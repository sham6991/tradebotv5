from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from main_app.market_phase_engine import MarketPhaseSnapshot


@dataclass
class DirectionDecision:
    underlying_id: str
    phase: str
    gap_type: str
    bull_score: float
    bear_score: float
    futures_confirmation: str
    futures_vwap_zone: str
    bias: str
    bias_strength: str
    allowed_side: str
    invalidation_levels: dict[str, float]
    reason: str
    blockers: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


class DirectionEngine:
    def decide(
        self,
        underlying_id: str,
        spot_candles: list[dict[str, Any]],
        futures_candles: list[dict[str, Any]],
        phase: MarketPhaseSnapshot,
        *,
        previous_close: float = 0.0,
        today_open: float = 0.0,
        manual_bias: str = "",
        risk_mode: str = "BALANCED",
    ) -> DirectionDecision:
        latest = dict(spot_candles[-1] if spot_candles else {})
        spot_price = float(latest.get("close") or latest.get("last_price") or 0)
        bull_score = _price_score("BULL", spot_candles, phase, previous_close, today_open)
        bear_score = _price_score("BEAR", spot_candles, phase, previous_close, today_open)
        futures = futures_confirmation(futures_candles, "BULL" if bull_score >= bear_score else "BEAR")
        if futures["confirmation"] == "WEAK":
            bull_score *= 0.8
            bear_score *= 0.8
        raw_bias = _manual_bias(manual_bias) or _score_bias(bull_score, bear_score)
        allowed_side = _allowed_side(raw_bias, risk_mode)
        blockers = list(phase.blockers)
        if futures["vwap_zone"].endswith("OVEREXTENDED"):
            blockers.append("Futures VWAP second deviation reached; no fresh momentum entry until retest.")
        if raw_bias in {"NEUTRAL", "CHOP"}:
            blockers.append("No directional edge from spot price action.")
        return DirectionDecision(
            underlying_id=underlying_id,
            phase=phase.phase,
            gap_type=phase.gap_type,
            bull_score=round(bull_score, 2),
            bear_score=round(bear_score, 2),
            futures_confirmation=futures["confirmation"],
            futures_vwap_zone=futures["vwap_zone"],
            bias=raw_bias,
            bias_strength="STRONG" if raw_bias.startswith("STRONG") else "WEAK" if raw_bias.startswith("WEAK") else "NONE",
            allowed_side=allowed_side,
            invalidation_levels={
                "bullish_invalid_below": phase.opening_micro_midpoint or today_open,
                "bearish_invalid_above": phase.opening_micro_midpoint or today_open,
            },
            reason=f"{raw_bias} from spot score {bull_score:.0f}/{bear_score:.0f} with futures {futures['confirmation']}.",
            blockers=blockers,
            debug={"spot_price": spot_price, "futures": futures},
        )


def futures_confirmation(candles: list[dict[str, Any]], side: str) -> dict[str, Any]:
    if len(candles) < 5:
        return {"confirmation": "UNAVAILABLE", "vwap_zone": "VWAP_UNAVAILABLE", "debug": {}}
    latest = candles[-1]
    previous = candles[-2]
    vwap = futures_vwap(candles)
    volume_ratio_5 = _volume_ratio(candles, 5)
    close = float(latest.get("close") or 0)
    open_ = float(latest.get("open") or 0)
    prev_close = float(previous.get("close") or 0)
    prev_high = float(previous.get("high") or 0)
    prev_low = float(previous.get("low") or 0)
    zone = _vwap_zone(close, vwap)
    if side == "BULL":
        strong = close > open_ and close > prev_high and volume_ratio_5 >= 1.10 and close > vwap["vwap"] and not zone.endswith("OVEREXTENDED")
        ok = close > open_ and close > prev_close and volume_ratio_5 >= 0.80
    else:
        strong = close < open_ and close < prev_low and volume_ratio_5 >= 1.10 and close < vwap["vwap"] and not zone.endswith("OVEREXTENDED")
        ok = close < open_ and close < prev_close and volume_ratio_5 >= 0.80
    confirmation = "STRONG" if strong else "OK" if ok else "WEAK" if volume_ratio_5 < 0.60 else "MISMATCH"
    return {"confirmation": confirmation, "vwap_zone": zone, "volume_ratio_5": round(volume_ratio_5, 3), **vwap}


def futures_vwap(candles: list[dict[str, Any]]) -> dict[str, float]:
    weighted = 0.0
    volume_sum = 0.0
    typicals: list[tuple[float, float]] = []
    for row in candles:
        volume = float(row.get("volume") or 0)
        tp = (float(row.get("high") or 0) + float(row.get("low") or 0) + float(row.get("close") or 0)) / 3
        if volume <= 0 or tp <= 0:
            continue
        typicals.append((tp, volume))
        weighted += tp * volume
        volume_sum += volume
    if volume_sum <= 0:
        return {"vwap": 0.0, "vwap_sd": 0.0, "upper_1": 0.0, "lower_1": 0.0, "upper_2": 0.0, "lower_2": 0.0}
    vwap = weighted / volume_sum
    variance = sum(volume * ((tp - vwap) ** 2) for tp, volume in typicals) / volume_sum
    sd = math.sqrt(max(0.0, variance))
    return {"vwap": vwap, "vwap_sd": sd, "upper_1": vwap + sd, "lower_1": vwap - sd, "upper_2": vwap + 2 * sd, "lower_2": vwap - 2 * sd}


def _price_score(side: str, candles: list[dict[str, Any]], phase: MarketPhaseSnapshot, previous_close: float, today_open: float) -> float:
    if not candles:
        return 0.0
    latest = candles[-1]
    close = float(latest.get("close") or 0)
    open_ = float(latest.get("open") or 0)
    high = float(latest.get("high") or close)
    low = float(latest.get("low") or close)
    range_ = max(0.0, high - low)
    body_percent = 100 * abs(close - open_) / range_ if range_ > 0 else 0.0
    close_pos = 100 * (close - low) / range_ if range_ > 0 else 50.0
    score = 0.0
    if side == "BULL":
        score += 15 if close > today_open > 0 else 0
        score += 15 if close > previous_close > 0 else 0
        score += 20 if phase.opening_micro_range_high and close > phase.opening_micro_range_high else 0
        score += 15 if phase.opening_micro_midpoint and low >= phase.opening_micro_midpoint else 0
        score += 10 if close > open_ and body_percent >= 50 else 0
        score += 10 if close_pos >= 65 else 0
    else:
        score += 15 if 0 < close < today_open else 0
        score += 15 if 0 < close < previous_close else 0
        score += 20 if phase.opening_micro_range_low and close < phase.opening_micro_range_low else 0
        score += 15 if phase.opening_micro_midpoint and high <= phase.opening_micro_midpoint else 0
        score += 10 if close < open_ and body_percent >= 50 else 0
        score += 10 if close_pos <= 35 else 0
    return score


def _volume_ratio(candles: list[dict[str, Any]], count: int) -> float:
    if len(candles) < count + 1:
        return 0.0
    current = float(candles[-1].get("volume") or 0)
    previous = [float(row.get("volume") or 0) for row in candles[-count - 1:-1]]
    avg = sum(previous) / len(previous) if previous else 0.0
    return current / avg if avg > 0 else 0.0


def _vwap_zone(close: float, vwap: dict[str, float]) -> str:
    if not vwap.get("vwap"):
        return "VWAP_UNAVAILABLE"
    if close > vwap["upper_2"]:
        return "BULLISH_OVEREXTENDED"
    if close > vwap["upper_1"]:
        return "BULLISH_STRETCHED"
    if close > vwap["vwap"]:
        return "BULLISH_HEALTHY"
    if close < vwap["lower_2"]:
        return "BEARISH_OVEREXTENDED"
    if close < vwap["lower_1"]:
        return "BEARISH_STRETCHED"
    if close < vwap["vwap"]:
        return "BEARISH_HEALTHY"
    return "AT_VWAP"


def _score_bias(bull: float, bear: float) -> str:
    if bull >= 70 and bull >= bear + 15:
        return "STRONG_BULLISH"
    if bear >= 70 and bear >= bull + 15:
        return "STRONG_BEARISH"
    if bull >= 45 and bull > bear:
        return "WEAK_BULLISH"
    if bear >= 45 and bear > bull:
        return "WEAK_BEARISH"
    return "NEUTRAL"


def _manual_bias(value: str) -> str:
    text = str(value or "").upper()
    if text in {"CE", "BULL", "BULLISH", "STRONG_BULLISH"}:
        return "STRONG_BULLISH"
    if text in {"PE", "BEAR", "BEARISH", "STRONG_BEARISH"}:
        return "STRONG_BEARISH"
    return ""


def _allowed_side(bias: str, risk_mode: str) -> str:
    if bias == "STRONG_BULLISH":
        return "CE_ONLY"
    if bias == "WEAK_BULLISH":
        return "CE_ONLY_STRICT"
    if bias == "STRONG_BEARISH":
        return "PE_ONLY"
    if bias == "WEAK_BEARISH":
        return "PE_ONLY_STRICT"
    return "NO_TRADE"
