from __future__ import annotations

from datetime import datetime
from typing import Any

from .stock_candle_builder import StockTickCandleBuilder


class StockLiveFeed:
    def __init__(self, interval: str = "minute") -> None:
        self.builder = StockTickCandleBuilder(interval=interval)
        self.running = False
        self.started_at = ""
        self.last_tick_at = ""
        self.last_error = ""
        self.subscribed_symbols: list[str] = []

    def start(self, symbols: list[str]) -> dict[str, Any]:
        self.running = True
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.subscribed_symbols = [str(symbol or "").upper() for symbol in symbols if str(symbol or "").strip()]
        self.last_error = ""
        return self.snapshot()

    def stop(self, reason: str = "") -> dict[str, Any]:
        self.running = False
        self.last_error = reason
        return self.snapshot()

    def on_tick(self, symbol: str, tick: dict[str, Any]) -> dict[str, Any]:
        result = self.builder.add_tick(symbol, tick)
        if result.get("accepted"):
            self.last_tick_at = datetime.now().isoformat(timespec="seconds")
            self.last_error = ""
        else:
            self.last_error = result.get("reason") or "Tick rejected."
        return result

    def candles(self, symbol: str, include_current: bool = True) -> list[dict[str, Any]]:
        return self.builder.rows(symbol, include_current=include_current)

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "last_tick_at": self.last_tick_at,
            "last_error": self.last_error,
            "subscribed_symbols": list(self.subscribed_symbols),
            "builder": self.builder.snapshot(),
        }
