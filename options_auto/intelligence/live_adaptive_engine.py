from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from options_auto.constants import MODE_REAL, SIDE_CE, SIDE_PE, SIDE_WAIT
from options_auto.core.clock import iso_now
from options_auto.indicators.technicals import bid_ask_spread_pct
from options_auto.intelligence.entry_timing_engine import round_to_tick


@dataclass
class LiveAdaptiveEngine:
    log_path: str | None = None
    action_log: list[dict[str, Any]] = field(default_factory=list)

    def evaluate_pre_entry(
        self,
        candidate: dict,
        latest_index_features: dict,
        latest_option_features: dict,
        market_cue: dict,
        regime: dict,
        settings: dict,
        risk_state: dict,
        data_quality: dict,
    ) -> dict:
        blockers = []
        warnings = []
        side = str(candidate.get("option_type") or candidate.get("side") or SIDE_WAIT).upper()
        score = _number(candidate.get("score"), _number((candidate.get("trade_score") or {}).get("score")))
        threshold = self.adjusted_score_threshold(settings, candidate, market_cue, regime, latest_option_features, risk_state, data_quality)["threshold"]
        if score < threshold:
            blockers.append("Trade score below adaptive threshold.")
        if _number(data_quality.get("score"), 100) < _number(settings.get("data_quality_threshold"), 80):
            blockers.append("Data quality score below threshold.")
        cue_side = _side(market_cue)
        regime_side = _side(regime)
        if cue_side in {SIDE_CE, SIDE_PE} and side in {SIDE_CE, SIDE_PE} and cue_side != side:
            blockers.append("Market cue is opposite the candidate side.")
        if regime_side == SIDE_WAIT:
            blockers.append("Regime changed to WAIT.")
        elif regime_side in {SIDE_CE, SIDE_PE} and side in {SIDE_CE, SIDE_PE} and regime_side != side:
            blockers.append("Regime does not support selected side.")
        if not latest_option_features.get("premium_expansion_confirmed", candidate.get("premium_expansion_confirmed")):
            blockers.append("Option premium is not confirming index direction.")
        if _number(candidate.get("spread_pct"), _number(latest_option_features.get("spread_pct"))) > _number(settings.get("max_spread_pct"), 0.60):
            blockers.append("Spread too wide.")
        if _number(risk_state.get("cooldown_until_epoch")) > time.time():
            blockers.append("Cooldown is active.")
        aggression = self.aggression(latest_index_features, latest_option_features, market_cue, regime, settings, risk_state, data_quality)
        action = "ENTER" if not blockers else "HOLD"
        return self._result(action, [], None, None, None, 0, "", aggression, blockers, warnings, "Pre-entry adaptive validation.", {
            "candidate": candidate,
            "market_cue": market_cue,
            "regime": regime,
        })

    def evaluate_pending_entry(
        self,
        pending_order: dict,
        candidate: dict,
        latest_quote: dict,
        latest_index_features: dict,
        latest_option_features: dict,
        market_cue: dict,
        regime: dict,
        settings: dict,
    ) -> dict:
        blockers = []
        warnings = []
        actions = []
        old_limit = _number(pending_order.get("price"), _number(pending_order.get("entry_limit")))
        planned_entry = _number(pending_order.get("planned_entry"), old_limit)
        ltp = _number(latest_quote.get("ltp"), latest_quote.get("last_price"))
        bid = _number(latest_quote.get("bid"))
        ask = _number(latest_quote.get("ask"))
        tick_size = _number(latest_quote.get("tick_size"), pending_order.get("tick_size") or 0.05)
        spread_pct = _number(latest_quote.get("spread_pct"), bid_ask_spread_pct(bid, ask))
        side = str(pending_order.get("side") or candidate.get("option_type") or candidate.get("side") or SIDE_WAIT).upper()
        now_epoch = _number(latest_quote.get("now_epoch"), time.time())
        created_epoch = _number(pending_order.get("created_epoch"), now_epoch)

        if now_epoch - created_epoch > _number(settings.get("limit_order_timeout_seconds"), 30):
            blockers.append("Limit order timeout reached.")
        signal_age = latest_quote.get("signal_age_seconds", pending_order.get("signal_age_seconds"))
        if signal_age not in ("", None) and _number(signal_age) > _number(settings.get("max_signal_age_seconds"), 20):
            blockers.append("Signal is stale.")
        if _quote_stale(latest_quote, settings, now_epoch):
            blockers.append("Quote stale.")
        if bid <= 0 or ask <= 0 or ask < bid:
            blockers.append("Invalid bid/ask.")
        if spread_pct > _number(settings.get("max_spread_pct"), 0.60):
            blockers.append("Spread too wide.")
        cue_side = _side(market_cue)
        regime_side = _side(regime)
        if regime_side == SIDE_WAIT:
            blockers.append("Regime changed to WAIT.")
        elif regime_side in {SIDE_CE, SIDE_PE} and side in {SIDE_CE, SIDE_PE} and regime_side != side:
            blockers.append("Regime reversed.")
        if cue_side in {SIDE_CE, SIDE_PE} and side in {SIDE_CE, SIDE_PE} and cue_side != side:
            blockers.append("Market cue flipped opposite.")
        if _number(latest_option_features.get("premium_return_1")) < -0.3:
            blockers.append("Premium no longer confirms.")
        if ltp > old_limit + _number(settings.get("max_chase_points"), 3.0):
            blockers.append("Entry is chasing premium.")
        if _number(latest_option_features.get("upper_wick_pct")) > 45:
            blockers.append("Option candle shows rejection.")

        aggression = self.aggression(latest_index_features, latest_option_features, market_cue, regime, settings, {}, {"score": 100})
        if blockers:
            reason = "Pending entry cancelled because " + " and ".join(blockers[:3]).lower() + "."
            result = self._result("CANCEL_ENTRY", ["CANCEL_ENTRY"], None, None, None, 0, "CANCEL_ENTRY", aggression, blockers, warnings, reason, {
                "pending_order": pending_order,
                "latest_quote": latest_quote,
                "trade_state": "ENTRY_PENDING",
            })
            self.log_action(result, mode=settings.get("mode"), trade_id=pending_order.get("entry_id") or pending_order.get("order_id"), state="ENTRY_PENDING")
            return result

        new_limit = None
        if settings.get("modify_limit_allowed", True) and settings.get("pending_entry_dynamic_modify_enabled", True):
            modifications = int(_number(pending_order.get("modification_count"), 0))
            if modifications < int(settings.get("max_buy_limit_modifications") or 2):
                raw_limit = min(ask, ltp + _number(settings.get("slippage_buffer_points"), 0.1))
                candidate_limit = round_to_tick(raw_limit, tick_size)
                if candidate_limit - old_limit >= tick_size and candidate_limit - planned_entry <= _number(settings.get("max_chase_points"), 3.0):
                    new_limit = candidate_limit
                    actions.append("MODIFY_ENTRY")
        action = "MODIFY_ENTRY" if new_limit is not None else "HOLD"
        result = self._result(action, actions, new_limit, None, None, 0, "", aggression, [], warnings, "Pending entry remains valid." if not actions else "Pending entry limit updated within chase rules.", {
            "pending_order": pending_order,
            "latest_quote": latest_quote,
            "trade_state": "ENTRY_PENDING",
        })
        if actions:
            self.log_action(result, mode=settings.get("mode"), trade_id=pending_order.get("entry_id") or pending_order.get("order_id"), state="ENTRY_PENDING")
        return result

    def evaluate_active_trade(
        self,
        trade: dict,
        latest_quote: dict,
        latest_index_features: dict,
        latest_option_features: dict,
        market_cue: dict,
        regime: dict,
        settings: dict,
        broker_orders: list | None = None,
    ) -> dict:
        trade = dict(trade or {})
        latest_quote = dict(latest_quote or {})
        settings = dict(settings or {})
        actions: list[str] = []
        blockers: list[str] = []
        warnings: list[str] = []
        entry = _number(trade.get("entry_price"))
        current_sl = _number(trade.get("stoploss"))
        current_target = _number(trade.get("target"))
        ltp = _number(latest_quote.get("ltp"), latest_quote.get("last_price") or trade.get("last_ltp"))
        tick = _number(trade.get("tick_size"), latest_quote.get("tick_size") or 0.05)
        initial_sl = _number(trade.get("initial_stoploss"), current_sl)
        risk_points = max(0.01, entry - initial_sl)
        profit_points = ltp - entry
        target_distance = max(0.01, current_target - entry)
        trade_state = self.classify_trade_state(trade, latest_quote, latest_index_features, latest_option_features, market_cue, regime, settings)
        new_sl = current_sl
        new_target = None
        partial_quantity = 0
        exit_reason = ""

        invalidations = self.invalidation_count(trade, latest_quote, latest_index_features, latest_option_features, market_cue, settings)
        if invalidations >= int(settings.get("early_exit_min_conditions") or 3):
            if profit_points <= 0:
                action = "EXIT"
                actions.append("EXIT")
                exit_reason = "Early exit triggered because setup invalidated."
            else:
                tightened = max(current_sl, entry + 0.2 * profit_points)
                if tightened > current_sl:
                    new_sl = tightened
                    actions.append("MODIFY_SL")
                if settings.get("partial_exit_enabled"):
                    partial_quantity = _partial_quantity(trade)
                    if partial_quantity:
                        actions.append("PARTIAL_EXIT")
                action = actions[0] if actions else "HOLD"
        else:
            action = "HOLD"

        if profit_points >= 0.50 * target_distance:
            new_sl = max(new_sl, entry)
        if profit_points >= 0.75 * target_distance:
            new_sl = max(new_sl, entry + 0.35 * target_distance)
        option_atr14 = _number(latest_option_features.get("option_atr14"), latest_option_features.get("atr14") or latest_quote.get("option_atr14"))
        if profit_points >= target_distance and trade_state == "WINNER_TRENDING" and option_atr14 > 0:
            new_sl = max(new_sl, ltp - option_atr14 * 0.8)
        if trade_state == "WINNER_SLOWING" and option_atr14 > 0:
            new_sl = max(new_sl, ltp - option_atr14 * 0.5)
        theta_risk = str(latest_option_features.get("theta_risk") or latest_quote.get("theta_risk") or "").upper()
        if theta_risk == "HIGH" and profit_points > 0:
            new_sl = max(new_sl, entry + 0.20 * profit_points)
        if theta_risk == "EXTREME":
            action = "EXIT"
            actions.append("EXIT")
            exit_reason = "Extreme theta risk."

        target_decision = self._target_extension(trade, latest_quote, latest_option_features, market_cue, regime, settings, trade_state, profit_points, target_distance)
        if target_decision.get("new_target"):
            new_target = target_decision["new_target"]
            new_sl = max(new_sl, target_decision.get("new_stoploss") or new_sl)
            actions.extend(["MODIFY_TARGET", "MODIFY_SL"])

        rounded_sl = round_to_tick(new_sl, tick)
        if rounded_sl > current_sl:
            if rounded_sl - current_sl < int(settings.get("minimum_sl_improvement_ticks") or 2) * tick:
                rounded_sl = current_sl
            elif self._sl_throttled(trade, latest_quote, settings):
                warnings.append("Stoploss modification throttle is active.")
                rounded_sl = current_sl
            elif str(settings.get("mode") or trade.get("mode") or "").upper() == MODE_REAL and not trade.get("stoploss_order_id"):
                blockers.append("Cannot modify real stoploss because broker SL order id is missing.")
                rounded_sl = current_sl
            else:
                actions.append("MODIFY_SL")
        else:
            rounded_sl = current_sl

        actions = list(dict.fromkeys(actions))
        if blockers:
            action = "MANUAL_ATTENTION"
        elif "EXIT" in actions:
            action = "EXIT"
        elif "PARTIAL_EXIT" in actions:
            action = "PARTIAL_EXIT"
        elif "MODIFY_TARGET" in actions:
            action = "MODIFY_TARGET"
        elif "MODIFY_SL" in actions:
            action = "MODIFY_SL"
        elif action not in {"EXIT", "PARTIAL_EXIT"}:
            action = "HOLD"

        aggression = self.aggression(latest_index_features, latest_option_features, market_cue, regime, settings, {}, {"score": 100})
        reason = self._active_reason(action, trade_state, invalidations, exit_reason)
        result = self._result(action, actions, None, rounded_sl if rounded_sl > current_sl else None, new_target, partial_quantity, exit_reason, aggression, blockers, warnings, reason, {
            "trade": trade,
            "trade_state": trade_state,
            "invalidation_count": invalidations,
            "r_multiple": round(profit_points / risk_points, 4),
        })
        if actions and action != "HOLD":
            self.log_action(result, mode=settings.get("mode"), trade_id=trade.get("trade_id"), state=trade_state)
        return result

    def classify_trade_state(self, trade: dict[str, Any], quote: dict[str, Any], index_features: dict[str, Any], option_features: dict[str, Any], market_cue: dict[str, Any], regime: dict[str, Any], settings: dict[str, Any]) -> str:
        side = str(trade.get("side") or trade.get("option_type") or SIDE_CE).upper()
        entry = _number(trade.get("entry_price"))
        ltp = _number(quote.get("ltp"), quote.get("last_price") or trade.get("last_ltp"))
        premium_return_1 = _number(option_features.get("premium_return_1"))
        option_vwap = _number(option_features.get("option_vwap"), option_features.get("vwap"))
        index_close = _number(index_features.get("close"))
        index_vwap = _number(index_features.get("vwap"))
        ema9 = _number(index_features.get("ema9"))
        ema20 = _number(index_features.get("ema20"))
        cue_side = _side(market_cue)
        spread_valid = _number(option_features.get("spread_pct"), quote.get("spread_pct")) <= _number(settings.get("max_spread_pct"), 0.60)
        if _quote_stale(quote, settings, _number(quote.get("now_epoch"), time.time())) or not spread_valid:
            return "DATA_RISK"
        if ltp > entry and option_vwap > 0 and ltp >= option_vwap:
            if side == SIDE_CE and index_close > index_vwap and ema9 >= ema20 and premium_return_1 > 0 and cue_side != SIDE_PE:
                return "WINNER_TRENDING"
            if side == SIDE_PE and index_close < index_vwap and ema9 <= ema20 and premium_return_1 > 0 and cue_side != SIDE_CE:
                return "WINNER_TRENDING"
        if ltp > entry and (premium_return_1 <= 0 or (option_vwap > 0 and ltp < option_vwap) or _number(option_features.get("upper_wick_pct")) > 45):
            return "WINNER_SLOWING"
        risk_points = max(0.01, _number(trade.get("entry_price")) - _number(trade.get("initial_stoploss"), trade.get("stoploss")))
        if 0 < ltp - entry < 0.5 * risk_points:
            return "SMALL_PROFIT_UNCONFIRMED"
        if abs(ltp - entry) <= max(1.0, 0.1 * risk_points) and _number(trade.get("candles_in_trade")) >= int(settings.get("premium_stagnation_candles") or 3):
            return "BREAKEVEN_NO_MOMENTUM"
        if ltp < entry and self.invalidation_count(trade, quote, index_features, option_features, market_cue, settings) >= int(settings.get("early_exit_min_conditions") or 3):
            return "LOSER_INVALIDATED"
        if ltp < entry:
            return "LOSER_STILL_VALID"
        return "HOLD"

    def invalidation_count(self, trade: dict[str, Any], quote: dict[str, Any], index_features: dict[str, Any], option_features: dict[str, Any], market_cue: dict[str, Any], settings: dict[str, Any]) -> int:
        side = str(trade.get("side") or SIDE_CE).upper()
        index_close = _number(index_features.get("close"))
        index_vwap = _number(index_features.get("vwap"))
        ema9 = _number(index_features.get("ema9"))
        ema20 = _number(index_features.get("ema20"))
        option_ltp = _number(quote.get("ltp"), quote.get("last_price"))
        option_vwap = _number(option_features.get("option_vwap"), option_features.get("vwap"))
        cue_side = _side(market_cue)
        spread_pct = _number(option_features.get("spread_pct"), quote.get("spread_pct"))
        depth_imbalance = _number(option_features.get("depth_imbalance"), quote.get("depth_imbalance"))
        premium_return_1 = _number(option_features.get("premium_return_1"))
        premium_return_3 = _number(option_features.get("premium_return_3"))
        risk_points = max(0.01, _number(trade.get("entry_price")) - _number(trade.get("initial_stoploss"), trade.get("stoploss")))
        profit_points = option_ltp - _number(trade.get("entry_price"))
        after_three = _number(trade.get("candles_in_trade")) >= int(settings.get("premium_stagnation_candles") or 3)
        conditions = []
        if side == SIDE_CE:
            conditions.extend([index_close < index_vwap, ema9 < ema20, cue_side in {SIDE_PE, SIDE_WAIT}])
        else:
            conditions.extend([index_close > index_vwap, ema9 > ema20, cue_side in {SIDE_CE, SIDE_WAIT}])
        conditions.extend([
            option_vwap > 0 and option_ltp < option_vwap,
            premium_return_1 < -0.5,
            premium_return_3 < 0,
            depth_imbalance < -25,
            spread_pct > _number(settings.get("max_spread_pct"), 0.60),
            _number(option_features.get("upper_wick_pct")) > 45,
            after_three and profit_points < 0.25 * risk_points,
        ])
        return len([condition for condition in conditions if condition])

    def aggression(self, index_features: dict[str, Any], option_features: dict[str, Any], market_cue: dict[str, Any], regime: dict[str, Any], settings: dict[str, Any], risk_state: dict[str, Any], data_quality: dict[str, Any]) -> dict[str, Any]:
        side = str(option_features.get("side") or option_features.get("option_type") or SIDE_CE).upper()
        score = 50.0
        cue_side = _side(market_cue)
        regime_side = _side(regime)
        if str(market_cue.get("cue") or "").startswith("strong") and cue_side in {side, SIDE_WAIT}:
            score += 20
        if str(regime.get("regime") or "").startswith("strong") and regime_side in {side, SIDE_WAIT}:
            score += 15
        if option_features.get("premium_expansion_confirmed"):
            score += 15
        if _number(option_features.get("spread_pct")) <= 0.25:
            score += 10
        if _number(option_features.get("relative_volume")) >= 1.5:
            score += 10
        if _number(data_quality.get("session_health"), 100) >= 80:
            score += 10
        if _number(data_quality.get("bot_health"), 100) >= 80:
            score += 10
        if risk_state.get("recent_loss"):
            score -= 20
        theta = str(option_features.get("theta_risk") or "").upper()
        if theta == "HIGH":
            score -= 20
        if theta == "EXTREME":
            score -= 35
        if _number(option_features.get("spread_pct")) > 0.50:
            score -= 25
        if "neutral" in str(regime.get("regime") or "").lower() or "choppy" in str(regime.get("regime") or "").lower():
            score -= 25
        if _number(data_quality.get("score"), 100) < 80:
            score -= 30
        if data_quality.get("kite_api_poor"):
            score -= 30
        if int(risk_state.get("rejected_setups_recently") or 0) >= 2:
            score -= 20
        score = max(0.0, min(100.0, score))
        high_threshold = _number(settings.get("aggressive_mode_min_score"), 75.0)
        if score >= high_threshold:
            level = "HIGH"
        elif score >= 50:
            level = "MEDIUM"
        else:
            level = "LOW"
        return {
            "score": round(score, 2),
            "level": level,
            "scan_interval_seconds": self.scan_interval(settings, level),
            "plan_validity_seconds": self.plan_validity(settings, level),
            "allow_target_extension": level == "HIGH" and bool(settings.get("allow_target_extension", True)),
        }

    def adjusted_score_threshold(self, settings: dict[str, Any], candidate: dict[str, Any], market_cue: dict[str, Any], regime: dict[str, Any], option_features: dict[str, Any], risk_state: dict[str, Any], data_quality: dict[str, Any]) -> dict[str, Any]:
        aggression = self.aggression({}, {**dict(option_features or {}), "side": candidate.get("option_type") or candidate.get("side")}, market_cue, regime, settings, risk_state, data_quality)
        threshold = _number(settings.get("buy_score_threshold"), 75.0)
        quantity_pct = 100
        if aggression["level"] == "LOW":
            threshold += _number(settings.get("low_aggression_score_boost"), 10)
            if settings.get("reduce_quantity_on_low_aggression", True):
                quantity_pct = int(_number(settings.get("low_aggression_quantity_pct"), 50))
        return {"threshold": threshold, "quantity_pct": quantity_pct, "aggression": aggression}

    def scan_interval(self, settings: dict[str, Any], aggression_level: str) -> float:
        if aggression_level == "HIGH":
            return _number(settings.get("adaptive_scan_seconds_aggressive"), 1)
        if aggression_level == "LOW":
            return _number(settings.get("adaptive_scan_seconds_conservative"), 3)
        return _number(settings.get("adaptive_scan_seconds_balanced"), 2)

    def plan_validity(self, settings: dict[str, Any], aggression_level: str) -> float:
        if aggression_level == "HIGH":
            return _number(settings.get("max_plan_age_seconds_aggressive"), 3)
        if aggression_level == "LOW":
            return _number(settings.get("max_plan_age_seconds_conservative"), 8)
        return _number(settings.get("max_plan_age_seconds_balanced"), 5)

    def log_action(self, decision: dict[str, Any], mode: Any = "", trade_id: Any = "", state: str = "") -> dict[str, Any]:
        row = {
            "timestamp": iso_now(),
            "mode": mode,
            "trade_id": trade_id,
            "state": state,
            "action": decision.get("action"),
            "old_entry": (decision.get("snapshot") or {}).get("old_entry"),
            "new_entry": decision.get("new_entry_limit"),
            "old_target": (decision.get("snapshot") or {}).get("old_target"),
            "new_target": decision.get("new_target"),
            "old_stoploss": (decision.get("snapshot") or {}).get("old_stoploss"),
            "new_stoploss": decision.get("new_stoploss"),
            "ltp": ((decision.get("snapshot") or {}).get("latest_quote") or {}).get("ltp"),
            "market_cue": ((decision.get("snapshot") or {}).get("market_cue") or {}).get("cue"),
            "regime": ((decision.get("snapshot") or {}).get("regime") or {}).get("regime"),
            "trade_state": (decision.get("snapshot") or {}).get("trade_state"),
            "aggression_level": decision.get("aggression_level"),
            "reason": decision.get("reason"),
            "blockers": decision.get("blockers") or [],
            "warnings": decision.get("warnings") or [],
        }
        self.action_log.append(row)
        self.action_log = self.action_log[-500:]
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, default=str) + "\n")
        return row

    def snapshot(self) -> dict[str, Any]:
        return {"action_log": self.action_log[-100:]}

    def _target_extension(self, trade, quote, option_features, market_cue, regime, settings, trade_state, profit_points, target_distance) -> dict[str, Any]:
        if not settings.get("allow_target_extension", True):
            return {}
        if _expiry_scalp_context(trade, settings) and not settings.get("expiry_scalp_extension_enabled", False):
            return {}
        if trade_state != "WINNER_TRENDING":
            return {}
        ltp = _number(quote.get("ltp"), quote.get("last_price"))
        current_target = _number(trade.get("target"))
        entry = _number(trade.get("entry_price"))
        if not (ltp >= current_target * 0.92 or profit_points >= 0.85 * target_distance):
            return {}
        if not str(regime.get("regime") or "").startswith("strong"):
            return {}
        if not option_features.get("premium_expansion_confirmed"):
            return {}
        if _number(option_features.get("relative_volume")) < 1.3:
            return {}
        if _number(option_features.get("spread_pct")) > _number(settings.get("max_spread_pct"), 0.60):
            return {}
        if str(option_features.get("theta_risk") or "").upper() == "EXTREME":
            return {}
        option_atr14 = _number(option_features.get("option_atr14"), option_features.get("atr14"))
        extension = min(option_atr14 * _number(settings.get("target_extension_atr_fraction"), 0.8), target_distance * _number(settings.get("target_extension_target_fraction"), 0.5))
        tick = _number(trade.get("tick_size"), 0.05)
        return {
            "new_target": round_to_tick(current_target + extension, tick),
            "new_stoploss": round_to_tick(max(_number(trade.get("stoploss")), entry + (_number(settings.get("target_extension_profit_protection_pct"), 40) / 100.0) * (ltp - entry)), tick),
        }

    def _sl_throttled(self, trade, quote, settings) -> bool:
        now_epoch = quote.get("now_epoch")
        last_epoch = trade.get("last_stoploss_modified_epoch")
        if now_epoch in ("", None) or last_epoch in ("", None):
            return False
        return _number(now_epoch) - _number(last_epoch) < _number(settings.get("sl_modify_throttle_seconds"), 10)

    def _active_reason(self, action: str, trade_state: str, invalidations: int, exit_reason: str) -> str:
        if exit_reason:
            return exit_reason
        if action == "MODIFY_SL" and trade_state == "WINNER_TRENDING":
            return "SL moved to protect profit because trade is trending."
        if action == "MODIFY_TARGET":
            return "Target extended because trend remains strong and premium expansion is confirmed."
        if action == "EXIT":
            return "Early exit triggered because setup invalidated."
        return f"Adaptive monitor state {trade_state} with {invalidations} invalidation conditions."

    def _result(self, action, actions, new_entry_limit, new_stoploss, new_target, partial_quantity, exit_reason, aggression, blockers, warnings, reason, snapshot):
        return {
            "action": action,
            "actions": list(dict.fromkeys(actions)),
            "new_entry_limit": new_entry_limit,
            "new_stoploss": new_stoploss,
            "new_target": new_target,
            "partial_quantity": partial_quantity,
            "exit_reason": exit_reason,
            "confidence": aggression["score"],
            "aggression_level": aggression["level"],
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": warnings,
            "reason": reason,
            "snapshot": snapshot,
        }


def _side(payload: dict[str, Any]) -> str:
    return str(payload.get("recommended_side") or payload.get("side") or SIDE_WAIT).upper()


def _quote_stale(quote: dict[str, Any], settings: dict[str, Any], now_epoch: float) -> bool:
    if quote.get("age_seconds") not in ("", None):
        return _number(quote.get("age_seconds")) > _number(settings.get("quote_stale_seconds"), 3)
    if quote.get("timestamp_epoch") not in ("", None):
        return now_epoch - _number(quote.get("timestamp_epoch"), now_epoch) > _number(settings.get("quote_stale_seconds"), 3)
    return False


def _expiry_scalp_context(trade: dict[str, Any], settings: dict[str, Any]) -> bool:
    if bool(settings.get("expiry_scalp_enabled") or settings.get("expiry_scalping_mode") or settings.get("market_context_expiry_scalp_enabled")):
        return True
    days = trade.get("days_to_expiry")
    if days in ("", None):
        return False
    try:
        return int(float(days)) <= 0
    except (TypeError, ValueError):
        return False


def _partial_quantity(trade: dict[str, Any]) -> int:
    quantity = int(_number(trade.get("quantity")))
    lot_size = max(1, int(_number(trade.get("lot_size"), 1)))
    if quantity < lot_size * 2:
        return 0
    return max(lot_size, (quantity // lot_size // 2) * lot_size)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
