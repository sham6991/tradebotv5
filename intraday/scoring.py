from __future__ import annotations

from .constants import SIDE_LONG, SIDE_NO_TRADE, SIDE_SHORT
from .models import IntradaySettings, Signal, StockSnapshot


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def score_snapshot(snapshot: StockSnapshot, settings: IntradaySettings, context: dict | None = None) -> StockSnapshot:
    context = context or {}
    long_breakdown = long_score(snapshot, settings, context)
    short_breakdown = short_score(snapshot, settings, context)
    snapshot.final_long_score = long_breakdown["score"]
    snapshot.final_short_score = short_breakdown["score"]
    eligible_long = settings.allow_long and long_breakdown["eligible"]
    eligible_short = settings.allow_short and short_breakdown["eligible"]
    if eligible_long and (not eligible_short or snapshot.final_long_score >= snapshot.final_short_score):
        snapshot.selected_side = SIDE_LONG
        snapshot.reason["score_breakdown"] = long_breakdown
    elif eligible_short:
        snapshot.selected_side = SIDE_SHORT
        snapshot.reason["score_breakdown"] = short_breakdown
    else:
        snapshot.selected_side = SIDE_NO_TRADE
        snapshot.reason["score_breakdown"] = long_breakdown if snapshot.final_long_score >= snapshot.final_short_score else short_breakdown
    return snapshot


def long_score(snapshot: StockSnapshot, settings: IntradaySettings, context: dict | None = None) -> dict:
    return _priority_score(snapshot, settings, context or {}, SIDE_LONG)


def short_score(snapshot: StockSnapshot, settings: IntradaySettings, context: dict | None = None) -> dict:
    return _priority_score(snapshot, settings, context or {}, SIDE_SHORT)


def build_signal(snapshot: StockSnapshot, settings: IntradaySettings, session_id: str) -> Signal:
    side = snapshot.selected_side
    breakdown = snapshot.reason.get("score_breakdown") or {}
    if side == SIDE_LONG:
        score = snapshot.final_long_score
    elif side == SIDE_SHORT:
        score = snapshot.final_short_score
    else:
        score = float(breakdown.get("score") or max(snapshot.final_long_score, snapshot.final_short_score))
    plan = _trade_plan(snapshot, settings, side)
    entry = plan["entry"]
    stop = plan["stoploss"]
    target = plan["target"]
    risk = plan["risk"]
    reward = abs(target - entry)
    risk_reward = round(reward / risk, 2) if risk else 0.0
    confidence = _clamp((score * 0.65) + (snapshot.liquidity_score * 0.2) + max(0.0, 100 - snapshot.trap_score) * 0.15)
    blockers = list(breakdown.get("blockers") or [])
    if side == SIDE_NO_TRADE:
        blockers.append("No valid entry passed the structure, volume, liquidity, trap, risk, and score gates.")
    if side != SIDE_NO_TRADE and risk_reward < settings.minimum_risk_reward:
        blockers.append("Risk reward is below the locked minimum.")
    if side != SIDE_NO_TRADE and not breakdown.get("gates") and snapshot.trap_warning == "HIGH" and "trap" not in " ".join(blockers).lower():
        blockers.append("High trap risk.")
    if side != SIDE_NO_TRADE and not breakdown.get("gates") and snapshot.liquidity_score < 35 and "liquidity" not in " ".join(blockers).lower():
        blockers.append("Liquidity score is too low.")
    blockers = _dedupe(blockers)
    trigger = breakdown.get("primary_trigger") or "No qualifying structure"
    explanation = (
        f"{snapshot.symbol} {side} priority score {score:.1f}. "
        f"Structure: {trigger}. VWAP {snapshot.vwap:.2f}, RSI {snapshot.rsi:.1f}, "
        f"relative volume {snapshot.relative_volume:.2f}, liquidity {snapshot.liquidity_score:.1f}, "
        f"trap warning {snapshot.trap_warning}."
    )
    return Signal(
        session_id=session_id,
        symbol=snapshot.symbol,
        exchange=snapshot.exchange,
        side=side,
        setup_name=f"{side.title()} {trigger}" if side != SIDE_NO_TRADE else "No trade",
        score=round(score, 2),
        score_breakdown=breakdown,
        entry_price=entry,
        stoploss=stop,
        target=target,
        risk_reward=risk_reward,
        confidence=round(confidence, 2),
        explanation=explanation,
        blockers=blockers,
        final_decision="BLOCKED" if blockers else "PENDING_APPROVAL" if settings.ask_permission_before_entry else "ELIGIBLE",
    )


def _priority_score(snapshot: StockSnapshot, settings: IntradaySettings, context: dict, side: str) -> dict:
    structure = snapshot.reason.get("entry_structure") or {}
    side_key = "long" if side == SIDE_LONG else "short"
    side_state = structure.get(side_key) or _missing_side_state(side)
    participation = structure.get("participation") or {}
    liquidity_state = structure.get("liquidity") or {}
    trend_state = structure.get("trend") or {}
    plan = _trade_plan(snapshot, settings, side)

    tier1 = 35.0 if side_state.get("structure_trigger") else 0.0
    if side_state.get("primary_trigger") in {"VWAP reclaim and hold", "VWAP rejection from below"}:
        tier1 += 2.0
    tier1 = min(35.0, tier1)

    tier2 = 0.0
    if side_state.get("volume_confirmation"):
        tier2 += 13.0
    if participation.get("volume_spike"):
        tier2 += 3.0
    tier2 += min(4.0, float(participation.get("candle_body_strength") or 0.0) * 4.0)
    tier2 = min(20.0, tier2)

    tier3 = 0.0
    if side_state.get("liquidity_confirmation"):
        tier3 += 8.0
    tier3 += min(5.0, snapshot.liquidity_score / 100.0 * 5.0)
    if side == SIDE_LONG and liquidity_state.get("liquidity_wall_long"):
        tier3 += 2.0
    if side == SIDE_SHORT and liquidity_state.get("liquidity_wall_short"):
        tier3 += 2.0
    tier3 = min(15.0, tier3)

    tier4 = 0.0
    if side_state.get("trend_filter_pass"):
        tier4 += 10.0
    if side == SIDE_LONG and snapshot.ema20 > snapshot.ema50 and trend_state.get("ema50_slope_up"):
        tier4 += 3.0
    if side == SIDE_SHORT and snapshot.ema20 < snapshot.ema50 and trend_state.get("ema50_slope_down"):
        tier4 += 3.0
    if side == SIDE_LONG and snapshot.rsi >= settings.rsi_bullish_threshold:
        tier4 += 2.0
    if side == SIDE_SHORT and snapshot.rsi <= settings.rsi_bearish_threshold:
        tier4 += 2.0
    tier4 = min(15.0, tier4)

    tier5 = _context_score(snapshot, settings, context, side)
    trap_penalty = 0.0 if side_state.get("trap_filter_pass") else 12.0
    total = tier1 + tier2 + tier3 + tier4 + tier5 - trap_penalty
    total = _clamp(total)

    blockers = list(side_state.get("blockers") or [])
    if plan["risk_reward"] < settings.minimum_risk_reward:
        blockers.append("Risk reward is below the locked minimum.")
    if total < settings.minimum_entry_score:
        blockers.append("Score is below the user-defined threshold.")
    if side == SIDE_LONG and not settings.allow_long:
        blockers.append("Long entries are disabled.")
    if side == SIDE_SHORT and not settings.allow_short:
        blockers.append("Short entries are disabled.")
    blockers = _dedupe(blockers)

    gates = {
        "direction_confirmation": bool(side_state.get("direction_confirmation")),
        "structure_trigger": bool(side_state.get("structure_trigger")),
        "volume_confirmation": bool(side_state.get("volume_confirmation")),
        "liquidity_confirmation": bool(side_state.get("liquidity_confirmation")),
        "trend_filter_pass": bool(side_state.get("trend_filter_pass")),
        "trap_filter_pass": bool(side_state.get("trap_filter_pass")),
        "risk_reward_pass": plan["risk_reward"] >= settings.minimum_risk_reward,
        "score_threshold_pass": total >= settings.minimum_entry_score,
    }
    eligible = all(gates.values()) and not blockers
    return {
        "side": side,
        "score": round(total, 2),
        "eligible": eligible,
        "primary_trigger": side_state.get("primary_trigger") or "",
        "structure_triggers": list(side_state.get("structure_triggers") or []),
        "gates": gates,
        "blockers": blockers,
        "trade_plan": {
            "entry": plan["entry"],
            "stoploss": plan["stoploss"],
            "target": plan["target"],
            "risk_reward": plan["risk_reward"],
        },
        "components": {
            "tier1_structure_value": round(tier1, 2),
            "tier2_participation_volume": round(tier2, 2),
            "tier3_liquidity_execution": round(tier3, 2),
            "tier4_trend_momentum_filter": round(tier4, 2),
            "tier5_context": round(tier5, 2),
            "trap_penalty": round(trap_penalty, 2),
        },
    }


def _context_score(snapshot: StockSnapshot, settings: IntradaySettings, context: dict, side: str) -> float:
    news_cap = abs(float(getattr(settings, "news_score_cap", 5.0) or 5.0))
    news_score = max(-news_cap, min(news_cap, float(snapshot.news_score or 0.0)))
    if side == SIDE_LONG:
        news = _clamp(5.0 + news_score / 2.0, 0.0, 8.0)
        options = _clamp(2.0 + max(0.0, snapshot.options_bias_score) / 2.0, 0.0, 4.0)
        market = _clamp(1.5 + float(context.get("long_bonus") or 0), 0.0, 3.0)
    else:
        news = _clamp(5.0 - news_score / 2.0, 0.0, 8.0)
        options = _clamp(2.0 + max(0.0, -snapshot.options_bias_score) / 2.0, 0.0, 4.0)
        market = _clamp(1.5 + float(context.get("short_bonus") or 0), 0.0, 3.0)
    return min(15.0, news + options + market)


def _trade_plan(snapshot: StockSnapshot, settings: IntradaySettings, side: str) -> dict:
    if side == SIDE_LONG:
        entry = round(snapshot.ltp + settings.entry_limit_offset, 2)
        stop = round(max(0.05, min(snapshot.low or entry * 0.995, snapshot.vwap or entry) - settings.stoploss_buffer), 2)
        risk = max(entry - stop, 0.05)
        target = round(entry + risk * settings.minimum_risk_reward + settings.target_buffer, 2)
    elif side == SIDE_SHORT:
        entry = round(snapshot.ltp - settings.entry_limit_offset, 2)
        stop = round(max(snapshot.high or entry * 1.005, snapshot.vwap or entry) + settings.stoploss_buffer, 2)
        risk = max(stop - entry, 0.05)
        target = round(max(0.05, entry - risk * settings.minimum_risk_reward - settings.target_buffer), 2)
    else:
        entry = stop = target = risk = 0.0
    reward = abs(target - entry)
    risk_reward = round(reward / risk, 2) if risk else 0.0
    return {"entry": entry, "stoploss": stop, "target": target, "risk": risk, "risk_reward": risk_reward}


def _missing_side_state(side: str) -> dict:
    label = "Long" if side == SIDE_LONG else "Short"
    return {
        "side": side,
        "direction_confirmation": False,
        "structure_trigger": False,
        "structure_triggers": [],
        "primary_trigger": "",
        "volume_confirmation": False,
        "liquidity_confirmation": False,
        "trend_filter_pass": False,
        "trap_filter_pass": False,
        "blockers": [
            f"{label} direction confirmation failed.",
            f"{label} structure trigger missing.",
            f"{label} volume confirmation failed.",
            f"{label} liquidity confirmation failed.",
            f"{label} EMA/RSI support filter failed.",
            f"{label} trap filter failed.",
        ],
    }


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
