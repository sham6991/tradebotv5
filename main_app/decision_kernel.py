from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from main_app.direction_engine import DirectionDecision, DirectionEngine
from main_app.instrument_resolver import InstrumentResolution
from main_app.market_phase_engine import MarketPhaseEngine
from main_app.strategy_plugins import StrategyRegistry, StrategySignal


@dataclass
class TradePlan:
    underlying_id: str
    exchange: str
    tradingsymbol: str
    side: str
    product: str
    quantity: int
    lots: int
    lot_size: int
    entry_order_type: str
    entry_limit: float
    stoploss_order_type: str
    stoploss_trigger: float
    stoploss_limit: float
    target_order_type: str
    target_limit: float


@dataclass
class KernelDecision:
    approved: bool
    final_decision: str
    direction: DirectionDecision
    plugin_signal: StrategySignal | None
    trade_plan: TradePlan | None
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


class DecisionKernel:
    def __init__(self, registry: StrategyRegistry | None = None):
        self.registry = registry or StrategyRegistry()
        self.direction_engine = DirectionEngine()
        self.phase_engine = MarketPhaseEngine()

    def evaluate(
        self,
        *,
        underlying_id: str,
        resolution: InstrumentResolution,
        spot_candles: list[dict[str, Any]],
        futures_candles: list[dict[str, Any]],
        ce_candles: list[dict[str, Any]],
        pe_candles: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> KernelDecision:
        phase = self.phase_engine.snapshot(
            spot_candles,
            previous_close=float(settings.get("previous_close") or 0),
            today_open=float(settings.get("today_open") or 0),
            now=_parse_datetime(settings.get("timestamp") or settings.get("now")),
            square_off_time=str(settings.get("square_off_time") or "15:20"),
        )
        direction = self.direction_engine.decide(
            underlying_id,
            spot_candles,
            futures_candles,
            phase,
            previous_close=float(settings.get("previous_close") or 0),
            today_open=float(settings.get("today_open") or 0),
            manual_bias=str(settings.get("manual_bias") or ""),
            risk_mode=str(settings.get("risk_mode") or "BALANCED"),
        )
        blockers = list(resolution.blockers) + list(direction.blockers)
        side = _side_from_allowed(direction.allowed_side)
        plugin_signal = None
        plan = None
        if side:
            plugin = self.registry.get(str(settings.get("entry_logic") or "FAST_OHLCV"))
            option_candles = ce_candles if side == "CE" else pe_candles
            plugin_signal = plugin.evaluate(side, option_candles, settings)
            blockers.extend(plugin_signal.hard_vetoes)
            required = _required_confidence(direction, settings)
            if plugin_signal.confidence < required:
                blockers.append(f"{plugin_signal.plugin_id} confidence {plugin_signal.confidence:.0f} below required {required:.0f}.")
            if not blockers:
                plan = _build_trade_plan(resolution, side, plugin_signal, settings)
                if plan is None:
                    blockers.append(f"{side} contract is unavailable.")
        approved = not blockers and plan is not None
        return KernelDecision(
            approved=approved,
            final_decision="APPROVED" if approved else "NO_TRADE",
            direction=direction,
            plugin_signal=plugin_signal,
            trade_plan=plan,
            blockers=list(dict.fromkeys(blockers)),
            warnings=list(resolution.warnings),
            debug={"required_confidence": _required_confidence(direction, settings)},
        )


def _side_from_allowed(allowed_side: str) -> str:
    if str(allowed_side).startswith("CE"):
        return "CE"
    if str(allowed_side).startswith("PE"):
        return "PE"
    return ""


def _required_confidence(direction: DirectionDecision, settings: dict[str, Any]) -> float:
    base = float(settings.get("required_confidence") or 70)
    if direction.phase == "MIDDAY_COMPRESSION":
        base += 8
    if direction.allowed_side.endswith("STRICT"):
        base += 5
    risk = str(settings.get("risk_mode") or "BALANCED").upper()
    if risk == "CONSERVATIVE":
        base += 5
    return base


def _build_trade_plan(resolution: InstrumentResolution, side: str, signal: StrategySignal, settings: dict[str, Any]) -> TradePlan | None:
    contract = resolution.ce if side == "CE" else resolution.pe
    if not contract:
        return None
    lots = int(settings.get("lots") or settings.get("lot_size") or 1)
    lot_size = int(contract.get("lot_size") or resolution.lot_size or 1)
    quantity = lots * lot_size
    entry = _round_to_tick(signal.entry_price, resolution.tick_size)
    stop_trigger = _round_to_tick(entry - signal.stoploss_points, resolution.tick_size)
    buffer_points = float(settings.get("stoploss_limit_buffer_points") or 2)
    stop_limit = _round_to_tick(stop_trigger - buffer_points, resolution.tick_size)
    target = _round_to_tick(entry + ((entry - stop_trigger) * signal.target_r), resolution.tick_size)
    if stop_trigger <= 0 or stop_limit <= 0 or stop_limit >= stop_trigger:
        return None
    return TradePlan(
        underlying_id=resolution.underlying_id,
        exchange=str(contract.get("exchange") or ""),
        tradingsymbol=str(contract.get("tradingsymbol") or ""),
        side=side,
        product="NRML",
        quantity=quantity,
        lots=lots,
        lot_size=lot_size,
        entry_order_type="LIMIT",
        entry_limit=entry,
        stoploss_order_type="SL",
        stoploss_trigger=stop_trigger,
        stoploss_limit=stop_limit,
        target_order_type="LIMIT",
        target_limit=target,
    )


def _round_to_tick(value: float, tick_size: float) -> float:
    tick = float(tick_size or 0.05)
    return round(round(float(value or 0) / tick) * tick, 2)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
