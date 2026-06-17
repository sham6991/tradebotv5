from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class StrategySignal:
    plugin_id: str
    side: str
    confidence: float
    entry_price: float
    stoploss_points: float
    target_r: float
    hard_vetoes: list[str] = field(default_factory=list)
    soft_vetoes: list[str] = field(default_factory=list)
    reason: str = ""
    debug: dict[str, Any] = field(default_factory=dict)


class StrategyPlugin(Protocol):
    plugin_id: str

    def evaluate(self, side: str, option_candles: list[dict[str, Any]], context: dict[str, Any]) -> StrategySignal: ...


class FastOHLCVPlugin:
    plugin_id = "FAST_OHLCV"

    def evaluate(self, side: str, option_candles: list[dict[str, Any]], context: dict[str, Any]) -> StrategySignal:
        if side not in {"CE", "PE"}:
            return StrategySignal(self.plugin_id, side, 0, 0, 0, 0, ["No allowed side."], reason="Bias window does not allow a side.")
        if not option_candles:
            return StrategySignal(self.plugin_id, side, 0, 0, 0, 0, ["Option OHLCV missing."], reason="No option candles.")
        latest = option_candles[-1]
        high = float(latest.get("high") or 0)
        low = float(latest.get("low") or 0)
        close = float(latest.get("close") or 0)
        open_ = float(latest.get("open") or close)
        volume = float(latest.get("volume") or 0)
        candle_range = max(0.0, high - low)
        body_percent = 100 * abs(close - open_) / candle_range if candle_range > 0 else 0.0
        close_position = 100 * (close - low) / candle_range if candle_range > 0 else 50.0
        avg_range = _avg_range(option_candles[-10:])
        confidence = 0.0
        confidence += 35 if close > open_ else 0
        confidence += min(25, body_percent / 2)
        confidence += 20 if close_position >= 60 else 0
        confidence += 20 if volume > 0 else 0
        stop_points = _clamp(avg_range * float(context.get("stop_atr_multiplier") or 0.9), float(context.get("minimum_stoploss_points") or 5), float(context.get("maximum_stoploss_points") or 25))
        return StrategySignal(
            plugin_id=self.plugin_id,
            side=side,
            confidence=round(confidence, 2),
            entry_price=close,
            stoploss_points=round(stop_points, 2),
            target_r=float(context.get("target_r") or 1.5),
            hard_vetoes=[],
            soft_vetoes=[],
            reason=f"FAST_OHLCV confidence {confidence:.0f}.",
            debug={"body_percent": body_percent, "close_position": close_position, "avg_range_10": avg_range},
        )


class StrategyRegistry:
    def __init__(self):
        self._plugins: dict[str, StrategyPlugin] = {}
        self.register(FastOHLCVPlugin())

    def register(self, plugin: StrategyPlugin) -> None:
        plugin_id = str(plugin.plugin_id).upper()
        self._plugins[plugin_id] = plugin

    def get(self, plugin_id: str) -> StrategyPlugin:
        key = str(plugin_id or "FAST_OHLCV").upper()
        if key not in self._plugins:
            raise KeyError(f"Strategy plugin {plugin_id!r} is not registered.")
        return self._plugins[key]

    def ids(self) -> list[str]:
        return sorted(self._plugins)


def _avg_range(rows: list[dict[str, Any]]) -> float:
    ranges = [max(0.0, float(row.get("high") or 0) - float(row.get("low") or 0)) for row in rows]
    ranges = [value for value in ranges if value > 0]
    return sum(ranges) / len(ranges) if ranges else 5.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
