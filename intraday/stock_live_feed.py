from __future__ import annotations

import time
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
        self.subscribed_tokens: list[int] = []
        self.symbol_by_token: dict[int, str] = {}
        self.token_by_symbol: dict[str, int] = {}
        self.latest_ticks: dict[str, dict[str, Any]] = {}
        self.tick_epoch_by_symbol: dict[str, float] = {}
        self.websocket_connected = False
        self.websocket_started_at = ""
        self.websocket_last_event = ""
        self.websocket_last_error = ""

    def start(self, symbols: list[str]) -> dict[str, Any]:
        self.running = True
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.subscribed_symbols = [str(symbol or "").upper() for symbol in symbols if str(symbol or "").strip()]
        self.last_error = ""
        return self.snapshot()

    def configure_instruments(self, instruments: list[dict[str, Any]]) -> dict[str, Any]:
        self.symbol_by_token = {}
        self.token_by_symbol = {}
        tokens: list[int] = []
        for row in instruments or []:
            token = _int(row.get("instrument_token"), row.get("token"))
            symbol = str(row.get("symbol") or row.get("tradingsymbol") or "").strip().upper()
            if token <= 0 or not symbol:
                continue
            self.symbol_by_token[token] = symbol
            self.token_by_symbol[symbol] = token
            tokens.append(token)
        self.subscribed_tokens = list(dict.fromkeys(tokens))
        return self.snapshot()

    def stop(self, reason: str = "") -> dict[str, Any]:
        self.running = False
        self.last_error = reason
        self.websocket_connected = False
        self.websocket_last_event = "stopped"
        if reason:
            self.websocket_last_error = reason
        return self.snapshot()

    def on_tick(self, symbol: str, tick: dict[str, Any]) -> dict[str, Any]:
        result = self.builder.add_tick(symbol, tick)
        if result.get("accepted"):
            symbol = str(result.get("symbol") or symbol or "").upper()
            received_epoch = time.time()
            received_at = datetime.now().isoformat(timespec="seconds")
            latest = {
                **dict(tick or {}),
                "symbol": symbol,
                "received_at": received_at,
                "received_epoch": received_epoch,
                "age_seconds": 0.0,
            }
            self.latest_ticks[symbol] = latest
            self.tick_epoch_by_symbol[symbol] = received_epoch
            self.last_tick_at = received_at
            self.last_error = ""
        else:
            self.last_error = result.get("reason") or "Tick rejected."
        return result

    def on_tick_by_token(self, tick: dict[str, Any]) -> dict[str, Any]:
        token = _int((tick or {}).get("instrument_token"), (tick or {}).get("token"))
        symbol = str((tick or {}).get("symbol") or (tick or {}).get("tradingsymbol") or self.symbol_by_token.get(token) or "").upper()
        if not symbol:
            self.last_error = f"Tick token {token or ''} is not mapped to a selected stock."
            return {"accepted": False, "reason": self.last_error}
        return self.on_tick(symbol, {**dict(tick or {}), "symbol": symbol, "instrument_token": token or (tick or {}).get("instrument_token")})

    def seed_candles(self, symbol: str, candles: list[dict[str, Any]]) -> int:
        symbol = str(symbol or "").upper()
        rows = [dict(row or {}) for row in candles or [] if row]
        if not symbol or not rows:
            return 0
        self.builder.candles[symbol] = _dedupe_candles(rows)[-400:]
        return len(self.builder.candles[symbol])

    def candles(self, symbol: str, include_current: bool = True) -> list[dict[str, Any]]:
        return _dedupe_candles(self.builder.rows(symbol, include_current=include_current))

    def latest_tick(self, symbol: str) -> dict[str, Any]:
        symbol = str(symbol or "").upper()
        tick = dict(self.latest_ticks.get(symbol) or {})
        if tick:
            tick["age_seconds"] = self.tick_age_seconds(symbol)
        return tick

    def tick_age_seconds(self, symbol: str, now_epoch: float | None = None) -> float:
        symbol = str(symbol or "").upper()
        epoch = self.tick_epoch_by_symbol.get(symbol)
        if not epoch:
            return 999999.0
        return max(0.0, float(now_epoch or time.time()) - float(epoch))

    def mark_websocket_connected(self, connected: bool, reason: str = "") -> dict[str, Any]:
        self.websocket_connected = bool(connected)
        self.websocket_last_event = "connected" if connected else "disconnected"
        if connected:
            self.websocket_started_at = self.websocket_started_at or datetime.now().isoformat(timespec="seconds")
            self.websocket_last_error = ""
        elif reason:
            self.websocket_last_error = str(reason)
        return self.snapshot()

    def mark_websocket_error(self, reason: str) -> dict[str, Any]:
        self.websocket_connected = False
        self.websocket_last_event = "error"
        self.websocket_last_error = str(reason or "")
        self.last_error = self.websocket_last_error
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "last_tick_at": self.last_tick_at,
            "last_error": self.last_error,
            "subscribed_symbols": list(self.subscribed_symbols),
            "subscribed_tokens": list(self.subscribed_tokens),
            "websocket_connected": self.websocket_connected,
            "websocket_started_at": self.websocket_started_at,
            "websocket_last_event": self.websocket_last_event,
            "websocket_last_error": self.websocket_last_error,
            "latest_ticks": {
                symbol: {
                    "last_price": tick.get("last_price") or tick.get("ltp"),
                    "received_at": tick.get("received_at"),
                    "age_seconds": self.tick_age_seconds(symbol),
                    "instrument_token": tick.get("instrument_token"),
                }
                for symbol, tick in sorted(self.latest_ticks.items())
            },
            "builder": self.builder.snapshot(),
        }


def _int(value: Any, default: Any = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        try:
            return int(float(default))
        except (TypeError, ValueError):
            return 0


def _dedupe_candles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_time: dict[str, dict[str, Any]] = {}
    no_time: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row or {})
        timestamp = str(item.get("timestamp") or item.get("datetime") or item.get("date") or item.get("time") or "")
        if timestamp:
            by_time[timestamp] = item
        else:
            no_time.append(item)
    return [by_time[key] for key in sorted(by_time)] + no_time
