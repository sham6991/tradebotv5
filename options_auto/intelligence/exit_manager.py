from __future__ import annotations

from typing import Any

from options_auto.intelligence.entry_timing_engine import round_to_tick


PROTECTION_ORDER_TYPES = {"SL", "SL-M", "SL-LIMIT", "STOPLOSS", "STOPLOSS_LIMIT"}
OPEN_ORDER_STATUSES = {"OPEN", "TRIGGER PENDING", "PENDING", "OPEN PENDING", "MODIFY PENDING"}


def build_long_option_trade_plan(
    selected: dict[str, Any],
    sizing: dict[str, Any],
    regime: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = dict(selected or {})
    sizing = dict(sizing or {})
    regime = dict(regime or {})
    settings = dict(settings or {})
    entry = _number(selected.get("ask"), _number(selected.get("ltp")))
    if entry <= 0:
        return {}
    tick_size = _number(selected.get("tick_size"), 0.05)
    option_atr = _number(selected.get("option_atr14"), _number(selected.get("atr14")))
    stop_distance = max(
        option_atr * _number(settings.get("atr_stoploss_multiplier"), 1.0),
        entry * _number(settings.get("min_stoploss_pct"), 3.0) / 100.0,
        _number(settings.get("minimum_stoploss_points"), 2.0),
    )
    stop_distance *= max(0.5, 1.0 + _number(settings.get("market_context_stoploss_multiplier_adjustment"), 0.0))
    profile = str(settings.get("strategy_profile") or "BALANCED").upper()
    if str(regime.get("regime") or "").startswith("strong"):
        risk_reward = max(_profile_rr(profile), _number(regime.get("target_multiplier"), 1.6))
    elif str(regime.get("regime") or "").startswith("mild"):
        risk_reward = max(1.2, min(1.4, _number(regime.get("target_multiplier"), _profile_rr(profile))))
    else:
        risk_reward = _profile_rr(profile)
    if selected.get("days_to_expiry") == 0:
        risk_reward = min(risk_reward, 1.2)
    risk_reward = max(0.7, risk_reward + _number(settings.get("market_context_target_multiplier_adjustment"), 0.0))
    target_distance = stop_distance * risk_reward
    stoploss = round_to_tick(entry - stop_distance, tick_size)
    target = round_to_tick(entry + target_distance, tick_size)
    return {
        "tradingsymbol": selected.get("tradingsymbol"),
        "instrument_token": selected.get("instrument_token") or selected.get("token"),
        "exchange": selected.get("exchange") or "NFO",
        "side": selected.get("option_type"),
        "entry_price": round_to_tick(entry, tick_size),
        "stop_distance": round(stop_distance, 4),
        "target_distance": round(target_distance, 4),
        "stoploss": max(tick_size, stoploss),
        "target": target,
        "quantity": int(sizing.get("quantity") or 0),
        "lots": int(sizing.get("lots") or 0),
        "lot_size": int(selected.get("lot_size") or 0),
        "risk_reward": round(risk_reward, 4),
        "max_holding_minutes": settings.get("max_holding_minutes"),
        "order_type": str(settings.get("buy_order_type") or "LIMIT").upper(),
        "stoploss_order_type": str(settings.get("stoploss_order_type") or "SL").upper(),
        "target_order_type": str(settings.get("target_order_type") or "LIMIT").upper(),
        "trailing_style": regime.get("trailing_style"),
    }


class ExitManager:
    def evaluate(self, trade: dict[str, Any], market: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        trade = dict(trade or {})
        market = dict(market or {})
        settings = dict(settings or {})
        entry = float(trade.get("entry_price") or 0)
        stoploss = float(trade.get("stoploss") or 0)
        target = float(trade.get("target") or 0)
        ltp = float(market.get("ltp") or market.get("last_price") or 0)
        side = str(trade.get("side") or "CE").upper()
        if entry <= 0 or ltp <= 0:
            return {"action": "HOLD", "actions": [], "blockers": [], "warnings": [], "reason": "Missing entry or LTP."}

        blockers = []
        warnings = []
        broker_orders = list(market.get("broker_orders") or trade.get("broker_orders") or [])
        duplicate_sl = self._duplicate_stoploss_orders(trade, broker_orders)
        if duplicate_sl:
            blockers.append("Duplicate live stoploss orders detected.")

        risk = max(0.05, entry - stoploss) if stoploss else max(0.05, entry * 0.15)
        r_multiple = (ltp - entry) / risk
        desired_stoploss = stoploss
        actions = []

        if stoploss > 0 and ltp <= stoploss:
            actions.append("STOPLOSS_EXIT")
        if target > 0 and ltp >= target:
            actions.append("TARGET_EXIT")

        if settings.get("break_even_sl_enabled") and r_multiple >= 1.0:
            desired_stoploss = max(desired_stoploss, entry)
            actions.append("MOVE_SL_TO_BREAKEVEN")
        if settings.get("trailing_stop_enabled") and r_multiple >= 1.2:
            trail = ltp - risk * 0.8
            desired_stoploss = max(desired_stoploss, trail)
            actions.append("TRAIL_SL")

        if desired_stoploss < stoploss:
            desired_stoploss = stoploss
            warnings.append("Stoploss widening was ignored.")
        stoploss_change = desired_stoploss > stoploss > 0
        if stoploss_change and self._sl_modification_throttled(trade, market, settings):
            desired_stoploss = stoploss
            stoploss_change = False
            warnings.append("Stoploss modification throttle is active.")
        if stoploss_change and self._real_mode(settings, trade) and not trade.get("stoploss_order_id"):
            blockers.append("Cannot modify real stoploss because broker SL order id is missing.")
        if stoploss_change:
            actions.append("MODIFY_SL")

        partial_quantity = 0
        if settings.get("partial_exit_enabled") and r_multiple >= 1.0:
            partial_quantity = self._partial_quantity(trade)
        if partial_quantity > 0:
            actions.append("PARTIAL_EXIT")

        if settings.get("time_exit_enabled") and float(trade.get("minutes_in_trade") or 0) >= float(settings.get("max_holding_minutes") or 45):
            actions.append("TIME_EXIT")
        if settings.get("reversal_exit_enabled") and market.get("reversal_signal"):
            actions.append("REVERSAL_EXIT")
        if settings.get("volatility_exit_enabled") and self._iv_crush_detected(market, settings):
            actions.append("IV_CRUSH_EXIT")
        if self._theta_exit_detected(market, settings):
            actions.append("THETA_EXIT")
        if settings.get("time_exit_enabled") and self._is_square_off_time(market, settings):
            actions.append("END_OF_DAY_EXIT")

        actions = list(dict.fromkeys(actions))
        action = "MANUAL_ATTENTION" if blockers else self._primary_action(actions)
        return {
            "action": action,
            "actions": actions,
            "r_multiple": round(r_multiple, 3),
            "old_stoploss": stoploss,
            "new_stoploss": round(desired_stoploss, 2),
            "stoploss_change": stoploss_change and not blockers,
            "target": target,
            "side": side,
            "partial_quantity": partial_quantity,
            "blockers": blockers,
            "warnings": warnings,
            "duplicate_stoploss_orders": duplicate_sl,
            "reason": "SL never widens; only profit-protecting changes are suggested.",
        }

    def _primary_action(self, actions: list[str]) -> str:
        priority = [
            "STOPLOSS_EXIT",
            "TARGET_EXIT",
            "THETA_EXIT",
            "IV_CRUSH_EXIT",
            "END_OF_DAY_EXIT",
            "TIME_EXIT",
            "REVERSAL_EXIT",
            "PARTIAL_EXIT",
            "MODIFY_SL",
            "MOVE_SL_TO_BREAKEVEN",
            "TRAIL_SL",
        ]
        for action in priority:
            if action in actions:
                return action
        return "HOLD"

    def _real_mode(self, settings: dict[str, Any], trade: dict[str, Any]) -> bool:
        mode = str(settings.get("mode") or trade.get("mode") or "").upper()
        return mode in {"REAL", "LIVE"}

    def _duplicate_stoploss_orders(self, trade: dict[str, Any], broker_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        symbol = str(trade.get("tradingsymbol") or "").upper()
        if not symbol:
            return []
        stop_orders = []
        for order in broker_orders:
            status = str(order.get("status") or "").upper()
            if status and status not in OPEN_ORDER_STATUSES:
                continue
            if str(order.get("tradingsymbol") or "").upper() != symbol:
                continue
            if str(order.get("transaction_type") or "").upper() != "SELL":
                continue
            if str(order.get("order_type") or "").upper() in PROTECTION_ORDER_TYPES:
                stop_orders.append(order)
        return stop_orders if len(stop_orders) > 1 else []

    def _sl_modification_throttled(self, trade: dict[str, Any], market: dict[str, Any], settings: dict[str, Any]) -> bool:
        now_epoch = market.get("now_epoch")
        last_epoch = trade.get("last_stoploss_modified_epoch")
        if now_epoch in ("", None) or last_epoch in ("", None):
            return False
        try:
            return float(now_epoch) - float(last_epoch) < float(settings.get("sl_modify_throttle_seconds") or 10)
        except (TypeError, ValueError):
            return False

    def _partial_quantity(self, trade: dict[str, Any]) -> int:
        quantity = int(trade.get("quantity") or 0)
        lot_size = int(trade.get("lot_size") or 1)
        if quantity < lot_size * 2:
            return 0
        lots_to_exit = max(1, quantity // lot_size // 2)
        return lots_to_exit * lot_size

    def _theta_exit_detected(self, market: dict[str, Any], settings: dict[str, Any]) -> bool:
        try:
            return float(market.get("theta_risk_score") or 0) >= float(settings.get("theta_exit_risk_score") or 80)
        except (TypeError, ValueError):
            return False

    def _iv_crush_detected(self, market: dict[str, Any], settings: dict[str, Any]) -> bool:
        try:
            return float(market.get("iv_drop_pct") or 0) >= float(settings.get("iv_crush_exit_pct") or 25)
        except (TypeError, ValueError):
            return False

    def _is_square_off_time(self, market: dict[str, Any], settings: dict[str, Any]) -> bool:
        current_time = str(market.get("time") or market.get("current_time") or "")
        square_off = str(settings.get("square_off_time") or "")
        if len(current_time) < 5 or len(square_off) < 5:
            return False
        return current_time[:5] >= square_off[:5]


def _profile_rr(profile: str) -> float:
    if profile == "CONSERVATIVE":
        return 1.5
    if profile == "AGGRESSIVE":
        return 1.1
    return 1.3


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
