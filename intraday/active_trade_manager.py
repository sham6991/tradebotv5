from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .constants import SIDE_LONG, SIDE_SHORT


ACTION_HOLD = "HOLD"
ACTION_TIGHTEN_SL = "TIGHTEN_SL"
ACTION_MOVE_SL_TO_BREAKEVEN = "MOVE_SL_TO_BREAKEVEN"
ACTION_TRAIL_SL = "TRAIL_SL"
ACTION_PARTIAL_EXIT = "PARTIAL_EXIT"
ACTION_FULL_EXIT = "FULL_EXIT"
ACTION_MODIFY_TARGET = "MODIFY_TARGET"
ACTION_NO_ACTION = "NO_ACTION"

MANAGEMENT_ACTIONS = {
    ACTION_HOLD,
    ACTION_TIGHTEN_SL,
    ACTION_MOVE_SL_TO_BREAKEVEN,
    ACTION_TRAIL_SL,
    ACTION_PARTIAL_EXIT,
    ACTION_FULL_EXIT,
    ACTION_MODIFY_TARGET,
    ACTION_NO_ACTION,
}


@dataclass
class ActiveTradeDecision:
    action: str = ACTION_NO_ACTION
    health_score: float = 50.0
    reason: str = ""
    new_stoploss: float | None = None
    new_target: float | None = None
    exit_price: float | None = None
    partial_quantity: int = 0
    r_multiple: float = 0.0
    opposite_signal_score: float = 0.0
    invalidation_status: str = "VALID"
    require_user_confirmation: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["action"] not in MANAGEMENT_ACTIONS:
            data["action"] = ACTION_NO_ACTION
        return data


class ActiveTradeManager:
    """Evaluates open trades after entry without changing entry scoring rules."""

    def __init__(self, settings):
        self.settings = settings

    def evaluate(
        self,
        trade: dict[str, Any],
        snapshot: dict[str, Any] | Any | None = None,
        market_row: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ActiveTradeDecision:
        now = now or datetime.now()
        if not trade or trade.get("status") != "OPEN":
            return ActiveTradeDecision(action=ACTION_NO_ACTION, reason="No open trade.")

        latest = _latest_bar(market_row or {})
        side = str(trade.get("side") or "").upper()
        entry = _number(trade.get("entry_price"), 0.0)
        current_stop = _number(trade.get("stoploss_trigger"), entry)
        current_target = _number(trade.get("target"), entry)
        ltp = _first_number(_get(snapshot, "ltp"), latest.get("close"), market_row.get("ltp") if market_row else None, entry)
        initial_stop = _number(
            trade.get("initial_stoploss_trigger") or trade.get("initial_stoploss") or current_stop,
            current_stop,
        )
        initial_target = _number(trade.get("initial_target") or current_target, current_target)
        risk_per_share = _risk_per_share(side, entry, initial_stop)
        r_multiple = _r_multiple(side, entry, ltp, risk_per_share)
        health, health_details = self._health_score(side, trade, snapshot, latest, ltp, r_multiple)
        opposite_score = health_details.get("opposite_signal_score", 0.0)
        details = {
            **health_details,
            "ltp": ltp,
            "entry_price": entry,
            "current_stoploss": current_stop,
            "initial_stoploss": initial_stop,
            "current_target": current_target,
            "initial_target": initial_target,
            "risk_per_share": risk_per_share,
            "r_multiple": r_multiple,
            "time_in_trade_minutes": _time_in_trade_minutes(trade, now),
        }

        if not bool(getattr(self.settings, "active_trade_management_enabled", True)):
            return self._decision(
                ACTION_NO_ACTION,
                health,
                "Active management disabled.",
                r_multiple,
                opposite_score,
                details=details,
            )

        time_exit = self._time_exit_action(trade, now, ltp, health, details)
        if time_exit:
            return time_exit

        early_exit_threshold = float(getattr(self.settings, "early_exit_health_threshold", 35.0) or 35.0)
        opposite_exit_threshold = float(getattr(self.settings, "opposite_signal_exit_threshold", 82.0) or 82.0)
        if bool(getattr(self.settings, "condition_exit_enabled", True)):
            if health <= early_exit_threshold or opposite_score >= opposite_exit_threshold:
                return self._decision(
                    ACTION_FULL_EXIT,
                    health,
                    "Trade thesis weakened: health or opposite signal crossed exit threshold.",
                    r_multiple,
                    opposite_score,
                    exit_price=ltp,
                    invalidation_status="INVALIDATED",
                    require_user_confirmation=bool(getattr(self.settings, "ask_confirmation_before_early_exit", False)),
                    details=details,
                )

        partial = self._partial_exit_action(trade, ltp, r_multiple, health, opposite_score, details)
        if partial:
            return partial

        breakeven = self._breakeven_action(side, trade, entry, current_stop, ltp, r_multiple, health, opposite_score, details)
        if breakeven:
            return breakeven

        trailing = self._trailing_action(side, trade, snapshot, market_row or {}, ltp, current_stop, risk_per_share, r_multiple, health, opposite_score, details)
        if trailing:
            return trailing

        target = self._dynamic_target_action(side, trade, ltp, current_target, risk_per_share, r_multiple, health, opposite_score, details)
        if target:
            return target

        tighten = self._tighten_action(side, entry, ltp, current_stop, risk_per_share, r_multiple, health, opposite_score, details)
        if tighten:
            return tighten

        action = ACTION_HOLD if health >= 60.0 else ACTION_NO_ACTION
        return self._decision(action, health, "Trade remains within active-management hold rules.", r_multiple, opposite_score, details=details)

    def _health_score(
        self,
        side: str,
        trade: dict[str, Any],
        snapshot: dict[str, Any] | Any | None,
        latest: dict[str, Any],
        ltp: float,
        r_multiple: float,
    ) -> tuple[float, dict[str, Any]]:
        score = 55.0
        reasons: list[str] = []
        if snapshot is None:
            if r_multiple >= 1.0:
                score += 8.0
                reasons.append("1R progress")
            elif r_multiple < -0.35:
                score -= 10.0
                reasons.append("adverse R progress")
            reasons.append("snapshot unavailable; hard SL/target only")
            return max(0.0, min(100.0, score)), {
                "health_reasons": reasons,
                "opposite_signal_score": 0.0,
                "snapshot_available": False,
            }
        ema20 = _number(_get(snapshot, "ema20"), 0.0)
        ema50 = _number(_get(snapshot, "ema50"), 0.0)
        vwap = _number(_get(snapshot, "vwap"), 0.0)
        rsi = _number(_get(snapshot, "rsi"), 50.0)
        rvol = _number(_get(snapshot, "relative_volume"), 0.0)
        poc = _number(_get(snapshot, "poc"), 0.0)
        vah = _number(_get(snapshot, "vah"), 0.0)
        val = _number(_get(snapshot, "val"), 0.0)
        depth_imbalance = _number(_get(snapshot, "depth_imbalance"), 0.0)
        spread_pct = _number(_get(snapshot, "spread_pct"), 0.0)
        news_score = max(-8.0, min(8.0, _number(_get(snapshot, "news_score"), 0.0)))
        trap_score = _number(_get(snapshot, "trap_score"), 0.0)
        long_score = _number(_get(snapshot, "final_long_score"), 0.0)
        short_score = _number(_get(snapshot, "final_short_score"), 0.0)
        opposite_score = short_score if side == SIDE_LONG else long_score

        if r_multiple >= 1.0:
            score += 8.0
            reasons.append("1R progress")
        elif r_multiple < -0.35:
            score -= 10.0
            reasons.append("adverse R progress")

        if side == SIDE_LONG:
            score += _condition(ema20 and ltp >= ema20, 7.0, -8.0, reasons, "above EMA20", "below EMA20")
            score += _condition(ema20 and ema50 and ema20 >= ema50, 6.0, -7.0, reasons, "EMA20 above EMA50", "EMA20 below EMA50")
            score += _condition(vwap and ltp >= vwap, 7.0, -10.0, reasons, "above VWAP", "below VWAP")
            score += _condition(rsi >= float(getattr(self.settings, "rsi_bullish_threshold", 55.0) or 55.0), 6.0, -8.0, reasons, "RSI bullish", "RSI weak")
            score += _condition(depth_imbalance >= -0.05, 3.0, -7.0, reasons, "depth stable", "sell depth pressure")
            if poc and ltp >= poc:
                score += 3.0
            if vah and ltp >= vah:
                score += 2.0
            if val and ltp < val:
                score -= 5.0
        else:
            score += _condition(ema20 and ltp <= ema20, 7.0, -8.0, reasons, "below EMA20", "above EMA20")
            score += _condition(ema20 and ema50 and ema20 <= ema50, 6.0, -7.0, reasons, "EMA20 below EMA50", "EMA20 above EMA50")
            score += _condition(vwap and ltp <= vwap, 7.0, -10.0, reasons, "below VWAP", "above VWAP")
            score += _condition(rsi <= float(getattr(self.settings, "rsi_bearish_threshold", 45.0) or 45.0), 6.0, -8.0, reasons, "RSI bearish", "RSI strong against short")
            score += _condition(depth_imbalance <= 0.05, 3.0, -7.0, reasons, "depth stable", "buy depth pressure")
            if poc and ltp <= poc:
                score += 3.0
            if val and ltp <= val:
                score += 2.0
            if vah and ltp > vah:
                score -= 5.0

        if rvol >= float(getattr(self.settings, "relative_volume_threshold", 1.5) or 1.5):
            score += 4.0
            reasons.append("relative volume confirms")
        elif rvol and rvol < 0.7:
            score -= 5.0
            reasons.append("relative volume weak")

        score += news_score
        if news_score:
            reasons.append("news capped into health")
        if trap_score >= 70.0:
            score -= 10.0
            reasons.append("trap risk high")
        elif trap_score >= 45.0:
            score -= 5.0
            reasons.append("trap risk present")
        if spread_pct >= 0.35:
            score -= 8.0
            reasons.append("spread too wide")
        if opposite_score >= 75.0:
            score -= 12.0
            reasons.append("opposite signal strong")

        candle_strength = _number(latest.get("close"), ltp) - _number(latest.get("open"), ltp)
        if side == SIDE_SHORT:
            candle_strength *= -1
        if candle_strength > 0:
            score += 3.0
        elif candle_strength < 0:
            score -= 3.0

        return max(0.0, min(100.0, score)), {
            "health_reasons": reasons,
            "ema20": ema20,
            "ema50": ema50,
            "vwap": vwap,
            "rsi": rsi,
            "relative_volume": rvol,
            "poc": poc,
            "vah": vah,
            "val": val,
            "depth_imbalance": depth_imbalance,
            "spread_pct": spread_pct,
            "news_score": news_score,
            "trap_score": trap_score,
            "opposite_signal_score": opposite_score,
        }

    def _time_exit_action(self, trade: dict[str, Any], now: datetime, ltp: float, health: float, details: dict[str, Any]) -> ActiveTradeDecision | None:
        if not bool(getattr(self.settings, "time_exit_enabled", True)):
            return None
        minutes = details.get("time_in_trade_minutes", 0.0)
        max_minutes = float(getattr(self.settings, "max_minutes_in_trade", 0.0) or 0.0)
        if max_minutes > 0 and minutes >= max_minutes:
            return self._decision(
                ACTION_FULL_EXIT,
                health,
                "Max time in trade reached.",
                details.get("r_multiple", 0.0),
                details.get("opposite_signal_score", 0.0),
                exit_price=ltp,
                invalidation_status="TIME_EXIT",
                details=details,
            )
        return None

    def _partial_exit_action(
        self,
        trade: dict[str, Any],
        ltp: float,
        r_multiple: float,
        health: float,
        opposite_score: float,
        details: dict[str, Any],
    ) -> ActiveTradeDecision | None:
        if not bool(getattr(self.settings, "partial_exit_enabled", False)):
            return None
        management = trade.get("management") or {}
        if management.get("partial_exit_done"):
            return None
        trigger = float(getattr(self.settings, "partial_exit_trigger_r", 1.0) or 1.0)
        if r_multiple < trigger:
            return None
        quantity = int(trade.get("quantity") or 0)
        pct = max(1.0, min(95.0, float(getattr(self.settings, "partial_exit_qty_pct", 50.0) or 50.0)))
        partial_quantity = max(1, int(quantity * pct / 100.0))
        if quantity <= 1 or partial_quantity >= quantity:
            return None
        return self._decision(
            ACTION_PARTIAL_EXIT,
            health,
            "Partial profit booked after configured R trigger.",
            r_multiple,
            opposite_score,
            exit_price=ltp,
            partial_quantity=partial_quantity,
            details=details,
        )

    def _breakeven_action(
        self,
        side: str,
        trade: dict[str, Any],
        entry: float,
        current_stop: float,
        ltp: float,
        r_multiple: float,
        health: float,
        opposite_score: float,
        details: dict[str, Any],
    ) -> ActiveTradeDecision | None:
        if not bool(getattr(self.settings, "breakeven_sl_enabled", True)):
            return None
        trigger = float(getattr(self.settings, "breakeven_trigger_r", 1.0) or 1.0)
        if r_multiple < trigger:
            return None
        buffer_points = max(0.0, float(getattr(self.settings, "breakeven_buffer", 0.05) or 0.0))
        new_stop = entry + buffer_points if side == SIDE_LONG else entry - buffer_points
        if side == SIDE_LONG:
            new_stop = min(new_stop, ltp - buffer_points)
        else:
            new_stop = max(new_stop, ltp + buffer_points)
        if not _is_better_stop(side, new_stop, current_stop, float(getattr(self.settings, "min_sl_modification_gap", 0.05) or 0.05)):
            return None
        return self._decision(
            ACTION_MOVE_SL_TO_BREAKEVEN,
            health,
            "Moved stoploss to breakeven after configured R trigger.",
            r_multiple,
            opposite_score,
            new_stoploss=new_stop,
            details=details,
        )

    def _trailing_action(
        self,
        side: str,
        trade: dict[str, Any],
        snapshot: dict[str, Any] | Any | None,
        market_row: dict[str, Any],
        ltp: float,
        current_stop: float,
        risk_per_share: float,
        r_multiple: float,
        health: float,
        opposite_score: float,
        details: dict[str, Any],
    ) -> ActiveTradeDecision | None:
        enabled = bool(getattr(self.settings, "active_trailing_sl_enabled", getattr(self.settings, "trailing_stop_enabled", False)))
        if not enabled:
            return None
        trigger = float(getattr(self.settings, "trail_activation_r", 1.2) or 1.2)
        if r_multiple < trigger:
            return None
        method = str(getattr(self.settings, "trailing_method", "HYBRID") or "HYBRID").upper()
        buffer_points = max(0.05, float(getattr(self.settings, "stoploss_buffer", 0.05) or 0.05))
        candidates: list[float] = []
        ema20 = _number(_get(snapshot, "ema20"), 0.0)
        vwap = _number(_get(snapshot, "vwap"), 0.0)
        candles = market_row.get("candles") or []
        if method in {"EMA20", "HYBRID"} and ema20 > 0:
            candidates.append(ema20)
        if method in {"VWAP", "HYBRID"} and vwap > 0:
            candidates.append(vwap)
        if method in {"SWING", "HYBRID"}:
            swing = _recent_swing(candles, side)
            if swing > 0:
                candidates.append(swing)
        if method in {"ATR", "HYBRID"}:
            atr = _average_range(candles[-14:])
            if atr > 0:
                candidates.append(ltp - atr * 1.2 if side == SIDE_LONG else ltp + atr * 1.2)
        if method in {"FIXED", "HYBRID"}:
            locked_r = max(0.35, r_multiple - 0.65)
            candidates.append(
                _stop_from_r(side, _number(trade.get("entry_price"), ltp), risk_per_share, locked_r)
            )
        if not candidates:
            return None
        raw_stop = max(candidates) if side == SIDE_LONG else min(candidates)
        new_stop = min(raw_stop, ltp - buffer_points) if side == SIDE_LONG else max(raw_stop, ltp + buffer_points)
        if not _is_better_stop(side, new_stop, current_stop, float(getattr(self.settings, "min_sl_modification_gap", 0.05) or 0.05)):
            return None
        return self._decision(
            ACTION_TRAIL_SL,
            health,
            f"Trailing stop tightened using {method} method.",
            r_multiple,
            opposite_score,
            new_stoploss=new_stop,
            details={**details, "trailing_method": method, "trail_candidates": candidates},
        )

    def _dynamic_target_action(
        self,
        side: str,
        trade: dict[str, Any],
        ltp: float,
        current_target: float,
        risk_per_share: float,
        r_multiple: float,
        health: float,
        opposite_score: float,
        details: dict[str, Any],
    ) -> ActiveTradeDecision | None:
        if not bool(getattr(self.settings, "dynamic_target_enabled", True)):
            return None
        if health < float(getattr(self.settings, "dynamic_target_health_threshold", 78.0) or 78.0):
            return None
        if r_multiple < 0.85:
            return None
        distance = abs(current_target - ltp)
        if risk_per_share <= 0 or distance > risk_per_share * 0.35:
            return None
        extension_r = max(0.1, float(getattr(self.settings, "target_extension_r", 0.5) or 0.5))
        new_target = current_target + risk_per_share * extension_r if side == SIDE_LONG else current_target - risk_per_share * extension_r
        return self._decision(
            ACTION_MODIFY_TARGET,
            health,
            "Momentum stayed strong near target, extending target.",
            r_multiple,
            opposite_score,
            new_target=new_target,
            details=details,
        )

    def _tighten_action(
        self,
        side: str,
        entry: float,
        ltp: float,
        current_stop: float,
        risk_per_share: float,
        r_multiple: float,
        health: float,
        opposite_score: float,
        details: dict[str, Any],
    ) -> ActiveTradeDecision | None:
        threshold = float(getattr(self.settings, "tighten_sl_health_threshold", 55.0) or 55.0)
        if health > threshold or r_multiple < 0.25:
            return None
        proposed = _stop_from_r(side, entry, risk_per_share, min(0.25, max(0.0, r_multiple - 0.15)))
        buffer_points = max(0.05, float(getattr(self.settings, "stoploss_buffer", 0.05) or 0.05))
        proposed = min(proposed, ltp - buffer_points) if side == SIDE_LONG else max(proposed, ltp + buffer_points)
        if not _is_better_stop(side, proposed, current_stop, float(getattr(self.settings, "min_sl_modification_gap", 0.05) or 0.05)):
            return None
        return self._decision(
            ACTION_TIGHTEN_SL,
            health,
            "Health weakened, so stoploss is tightened without widening risk.",
            r_multiple,
            opposite_score,
            new_stoploss=proposed,
            details=details,
        )

    def _decision(
        self,
        action: str,
        health: float,
        reason: str,
        r_multiple: float,
        opposite_score: float,
        *,
        new_stoploss: float | None = None,
        new_target: float | None = None,
        exit_price: float | None = None,
        partial_quantity: int = 0,
        invalidation_status: str = "VALID",
        require_user_confirmation: bool = False,
        details: dict[str, Any] | None = None,
    ) -> ActiveTradeDecision:
        return ActiveTradeDecision(
            action=action,
            health_score=round(float(health), 2),
            reason=reason,
            new_stoploss=None if new_stoploss is None else round(float(new_stoploss), 4),
            new_target=None if new_target is None else round(float(new_target), 4),
            exit_price=None if exit_price is None else round(float(exit_price), 4),
            partial_quantity=max(0, int(partial_quantity or 0)),
            r_multiple=round(float(r_multiple), 4),
            opposite_signal_score=round(float(opposite_score), 2),
            invalidation_status=invalidation_status,
            require_user_confirmation=require_user_confirmation,
            details=details or {},
        )


def _condition(condition: Any, positive: float, negative: float, reasons: list[str], positive_reason: str, negative_reason: str) -> float:
    if condition:
        reasons.append(positive_reason)
        return positive
    reasons.append(negative_reason)
    return negative


def _get(source: dict[str, Any] | Any | None, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _first_number(*values: Any) -> float:
    for value in values:
        if value in ("", None):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _latest_bar(row: dict[str, Any]) -> dict[str, Any]:
    candles = row.get("candles") or []
    if candles:
        return candles[-1]
    return row or {}


def _risk_per_share(side: str, entry: float, stop: float) -> float:
    if side == SIDE_LONG:
        return max(0.05, entry - stop)
    if side == SIDE_SHORT:
        return max(0.05, stop - entry)
    return 0.05


def _r_multiple(side: str, entry: float, ltp: float, risk_per_share: float) -> float:
    risk = max(0.05, risk_per_share)
    if side == SIDE_LONG:
        return (ltp - entry) / risk
    if side == SIDE_SHORT:
        return (entry - ltp) / risk
    return 0.0


def _stop_from_r(side: str, entry: float, risk_per_share: float, r_value: float) -> float:
    if side == SIDE_LONG:
        return entry + risk_per_share * r_value
    return entry - risk_per_share * r_value


def _is_better_stop(side: str, new_stop: float, current_stop: float, min_gap: float = 0.05) -> bool:
    gap = max(0.0, float(min_gap or 0.0))
    if side == SIDE_LONG:
        return float(new_stop) > float(current_stop) + gap
    if side == SIDE_SHORT:
        return float(new_stop) < float(current_stop) - gap
    return False


def _recent_swing(candles: list[dict[str, Any]], side: str) -> float:
    recent = candles[-5:] if candles else []
    if not recent:
        return 0.0
    if side == SIDE_LONG:
        return min(_number(row.get("low"), 0.0) for row in recent)
    return max(_number(row.get("high"), 0.0) for row in recent)


def _average_range(candles: list[dict[str, Any]]) -> float:
    ranges = [
        max(0.0, _number(row.get("high"), 0.0) - _number(row.get("low"), 0.0))
        for row in candles
    ]
    ranges = [value for value in ranges if value > 0]
    return sum(ranges) / len(ranges) if ranges else 0.0


def _time_in_trade_minutes(trade: dict[str, Any], now: datetime) -> float:
    try:
        entry_time = datetime.fromisoformat(str(trade.get("entry_time") or ""))
    except ValueError:
        return 0.0
    return max(0.0, (now - entry_time).total_seconds() / 60.0)
