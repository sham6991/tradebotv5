from __future__ import annotations

import time
from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT
from options_auto.core.performance_monitor import PerformanceMonitor
from options_auto.core.task_priority import FAST_LANE_DISALLOWED_TASKS
from options_auto.indicators.technicals import bid_ask_spread_pct
from options_auto.intelligence.entry_timing_engine import round_to_tick


def fast_entry_limit_formula(plan: dict[str, Any], latest_quote: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    entry_plan = dict(plan.get("entry_plan") or {})
    planned_entry = _number(entry_plan.get("entry_limit"), _number(entry_plan.get("signal_price")))
    bid = _number(latest_quote.get("bid"))
    ask = _number(latest_quote.get("ask"))
    ltp = _number(latest_quote.get("ltp"), latest_quote.get("last_price"))
    tick_size = _number(latest_quote.get("tick_size"), entry_plan.get("tick_size") or 0.05)
    raw_entry = min(ask, ltp + _number(settings.get("slippage_buffer_points"), 0.1), planned_entry + _number(settings.get("max_chase_points"), 3.0))
    entry_limit = round_to_tick(raw_entry, tick_size)
    if bid > 0 and entry_limit < bid:
        entry_limit = round_to_tick(bid, tick_size)
    if ask > 0 and entry_limit > ask:
        entry_limit = round_to_tick(ask, tick_size)
    blockers = []
    if entry_limit - planned_entry > _number(settings.get("max_chase_points"), 3.0):
        blockers.append("Entry would chase beyond max chase.")
    return {"entry_limit": entry_limit, "blockers": blockers}


class LowLatencyDecisionEngine:
    def __init__(self, performance_monitor: PerformanceMonitor | None = None) -> None:
        self.performance_monitor = performance_monitor or PerformanceMonitor()

    def validate_final_entry(
        self,
        plan: dict[str, Any],
        latest_quote: dict[str, Any],
        settings: dict[str, Any],
        state: dict[str, Any] | None = None,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        started = self.performance_monitor.now()
        now_epoch = time.time() if now_epoch is None else float(now_epoch)
        state = dict(state or {})
        settings = dict(settings or {})
        plan = dict(plan or {})
        latest_quote = dict(latest_quote or {})
        blockers: list[str] = []
        warnings: list[str] = []

        if not plan or plan.get("status") != "READY":
            blockers.append("Ready trade plan is not ready.")
        last_refresh = _number(plan.get("last_refreshed_epoch"))
        max_age = _max_plan_age(settings)
        plan_age = now_epoch - last_refresh if last_refresh > 0 else 9999.0
        if plan_age > max_age:
            blockers.append("Ready trade plan expired.")

        quote_age = _quote_age(latest_quote, now_epoch)
        if quote_age > _number(settings.get("quote_stale_seconds"), 3.0):
            blockers.append("Quote stale.")
        bid = _number(latest_quote.get("bid"))
        ask = _number(latest_quote.get("ask"))
        if bid <= 0 or ask <= 0 or ask < bid:
            blockers.append("Invalid bid/ask.")
        spread_pct = bid_ask_spread_pct(bid, ask)
        if spread_pct > _number(settings.get("max_spread_pct"), 0.60):
            blockers.append("Spread too wide.")

        planned_entry = _number((plan.get("entry_plan") or {}).get("entry_limit"))
        chase_distance = ask - planned_entry if ask > 0 and planned_entry > 0 else 0.0
        if chase_distance > _number(settings.get("max_chase_points"), 3.0):
            blockers.append("Entry is chasing premium.")
        option_atr14 = _number(latest_quote.get("option_atr14"), _number((plan.get("premium_context") or {}).get("option_atr14")))
        if option_atr14 > 0 and chase_distance > option_atr14 * _number(settings.get("max_chase_atr_fraction"), 0.35):
            blockers.append("Entry moved too far from signal.")

        side = str(plan.get("side") or SIDE_WAIT).upper()
        premium_return_1 = _number(latest_quote.get("premium_return_1"), _number((plan.get("premium_context") or {}).get("premium_return_1")))
        ltp = _number(latest_quote.get("ltp"), latest_quote.get("last_price"))
        signal_price = _number((plan.get("entry_plan") or {}).get("signal_price"))
        if side in {SIDE_CE, SIDE_PE} and premium_return_1 < 0 and ltp < signal_price:
            blockers.append("Premium no longer confirms.")

        market_cue = dict(state.get("market_cue") or (plan.get("market_context") or {}).get("market_cue") or {})
        cue_side = str(market_cue.get("recommended_side") or market_cue.get("side") or SIDE_WAIT).upper()
        if side == SIDE_CE and cue_side == SIDE_PE and not state.get("reversal_setup_confirmed"):
            blockers.append("Market cue reversed.")
        if side == SIDE_PE and cue_side == SIDE_CE and not state.get("reversal_setup_confirmed"):
            blockers.append("Market cue reversed.")

        regime = dict(state.get("regime") or (plan.get("market_context") or {}).get("regime") or {})
        regime_side = str(regime.get("recommended_side") or regime.get("side") or SIDE_WAIT).upper()
        if regime_side == SIDE_WAIT:
            blockers.append("Regime changed to WAIT.")
        elif regime_side in {SIDE_CE, SIDE_PE} and side in {SIDE_CE, SIDE_PE} and regime_side != side:
            blockers.append("Regime reversed.")

        if state.get("active_order_conflict"):
            blockers.append("Active order conflict.")
        if state.get("risk_locked"):
            blockers.append("Risk lock active.")
        if not (state.get("mode_guard_allowed", True) and state.get("governor_allowed", True) and state.get("rate_limiter_healthy", True)):
            blockers.append("Safety gate blocked fast entry.")
        if _number(state.get("data_quality_score"), 100) < _number(settings.get("data_quality_threshold"), 80):
            blockers.append("Data quality below threshold.")

        limit = fast_entry_limit_formula(plan, latest_quote, settings)
        blockers.extend(limit["blockers"])
        latency_ms = self.performance_monitor.elapsed_ms(started)
        event = self.performance_monitor.record_latency("final_validation", latency_ms, {"side": side, "symbol": (plan.get("contract") or {}).get("tradingsymbol")})
        warnings.extend(event.get("warnings") or [])
        if quote_age + latency_ms / 1000.0 > _number(settings.get("quote_stale_seconds"), 3.0):
            blockers.append("Quote became stale during validation.")
        blockers = list(dict.fromkeys(blockers))
        return {
            "allowed": not blockers,
            "latency_ms": latency_ms,
            "blockers": blockers,
            "warnings": warnings,
            "entry_limit": limit["entry_limit"],
            "reason": "Fast final entry validation passed." if not blockers else "Fast final entry validation blocked: " + "; ".join(blockers),
            "slow_lane_tasks_used": [],
        }

    def scan_atm_candidates(
        self,
        instruments: list[dict[str, Any]],
        spot: float,
        settings: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        settings = dict(settings or {})
        span = int(settings.get("atm_scan_strike_span") or 4)
        if not instruments:
            return []
        strikes = sorted({float(item.get("strike") or 0) for item in instruments if _number(item.get("strike")) > 0})
        if not strikes:
            return []
        atm = min(strikes, key=lambda strike: abs(strike - float(spot or 0)))
        sorted_by_distance = sorted(strikes, key=lambda strike: abs(strike - atm))
        allowed = set(sorted_by_distance[: span * 2 + 1])
        return [dict(item) for item in instruments if _number(item.get("strike")) in allowed and str(item.get("instrument_type") or item.get("option_type") or "").upper() in {SIDE_CE, SIDE_PE}]

    def fast_lane_contains_slow_task(self, tasks: list[str]) -> bool:
        return any(str(task).strip().lower() in FAST_LANE_DISALLOWED_TASKS for task in tasks)


def validate_final_entry(plan: dict[str, Any], latest_quote: dict[str, Any], settings: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    return LowLatencyDecisionEngine().validate_final_entry(plan, latest_quote, settings, state)


def _max_plan_age(settings: dict[str, Any]) -> float:
    profile = str(settings.get("strategy_profile") or "BALANCED").upper()
    if profile == "AGGRESSIVE":
        return _number(settings.get("max_plan_age_seconds_aggressive"), 3.0)
    if profile == "CONSERVATIVE":
        return _number(settings.get("max_plan_age_seconds_conservative"), 8.0)
    return _number(settings.get("max_plan_age_seconds_balanced"), 5.0)


def _quote_age(quote: dict[str, Any], now_epoch: float) -> float:
    if quote.get("age_seconds") not in ("", None):
        return _number(quote.get("age_seconds"), 9999.0)
    timestamp = quote.get("timestamp_epoch") or quote.get("last_updated_epoch")
    if timestamp in ("", None):
        return 0.0
    return max(0.0, now_epoch - _number(timestamp, now_epoch))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
