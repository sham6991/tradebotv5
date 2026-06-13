from __future__ import annotations

import csv
import re
import time
from pathlib import Path
from typing import Any, Callable


TickCallback = Callable[[list[dict[str, Any]]], None]


class CsvReplayZerodhaClient:
    """Small Kite-compatible client for Options Auto websocket replay tests."""

    def __init__(
        self,
        *,
        index_csv: str | Path,
        option_csvs: list[str | Path],
        index_token: int = 256265,
        index_symbol: str = "NIFTY",
        default_exchange: str = "NFO",
        default_lot_size: int = 50,
    ) -> None:
        self.index_token = int(index_token)
        self.index_symbol = str(index_symbol or "NIFTY").upper()
        self.default_exchange = str(default_exchange or "NFO").upper()
        self.default_lot_size = int(default_lot_size or 50)
        self.index_rows = _read_rows(index_csv)
        self.option_streams = [_OptionStream(path, self.default_exchange, self.default_lot_size) for path in option_csvs]
        self.quote_calls: list[list[str]] = []
        self._named_tickers: dict[str, dict[str, Any]] = {}
        self._cursor = 0

    def instruments(self, exchange: str = "NFO") -> list[dict[str, Any]]:
        rows = []
        for stream in self.option_streams:
            row = dict(stream.instrument)
            row["exchange"] = str(exchange or row.get("exchange") or self.default_exchange).upper()
            rows.append(row)
        return rows

    def quote(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        self.quote_calls.append(list(keys or []))
        latest = self._latest_option_quotes()
        result: dict[str, dict[str, Any]] = {}
        for key in keys or []:
            text = str(key or "").upper()
            quote = latest.get(text)
            if quote:
                result[str(key)] = dict(quote)
        return result

    def start_named_ticker(
        self,
        name: str,
        instrument_tokens: list[int],
        on_ticks: TickCallback,
        on_connect: Callable[[Any], None] | None = None,
        on_close: Callable[..., None] | None = None,
        on_error: Callable[..., None] | None = None,
        on_reconnect: Callable[..., None] | None = None,
        on_noreconnect: Callable[..., None] | None = None,
        on_order_update: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        state = {
            "name": str(name or "default"),
            "tokens": [int(token) for token in instrument_tokens or []],
            "on_ticks": on_ticks,
            "on_connect": on_connect,
            "on_close": on_close,
            "on_error": on_error,
            "on_reconnect": on_reconnect,
            "on_noreconnect": on_noreconnect,
            "on_order_update": on_order_update,
            "closed": False,
        }
        self._named_tickers[state["name"]] = state
        if on_connect:
            on_connect({"mode": "full", "tokens": list(state["tokens"])})
        return {"close": lambda: self.stop_named_ticker(state["name"]), "tokens": list(state["tokens"])}

    def stop_named_ticker(self, name: str) -> None:
        ticker = self._named_tickers.pop(str(name or "default"), None)
        if ticker:
            ticker["closed"] = True
            callback = ticker.get("on_close")
            if callback:
                callback(1000, "closed")

    def emit_next(self, name: str = "options_auto", *, include_index: bool = True) -> list[dict[str, Any]]:
        ticks = self._ticks_at(self._cursor, include_index=include_index)
        self._cursor += 1
        self.emit_ticks(ticks, name=name)
        return ticks

    def emit_all(self, name: str = "options_auto", *, delay_seconds: float = 0.0, max_rows: int | None = None) -> int:
        emitted = 0
        limit = max_rows if max_rows is not None else self.row_count
        while emitted < limit and self._cursor < self.row_count:
            self.emit_next(name=name)
            emitted += 1
            if delay_seconds > 0:
                time.sleep(delay_seconds)
        return emitted

    def emit_ticks(self, ticks: list[dict[str, Any]], name: str = "options_auto") -> None:
        ticker = self._named_tickers.get(str(name or "options_auto"))
        if not ticker or ticker.get("closed"):
            return
        subscribed = {int(token) for token in ticker.get("tokens") or []}
        payload = [tick for tick in ticks if int(tick.get("instrument_token") or 0) in subscribed]
        if payload:
            ticker["on_ticks"](payload)

    @property
    def row_count(self) -> int:
        lengths = [len(self.index_rows)] + [len(stream.rows) for stream in self.option_streams]
        return max(lengths or [0])

    def _ticks_at(self, index: int, *, include_index: bool) -> list[dict[str, Any]]:
        ticks = []
        if include_index and self.index_rows:
            row = self.index_rows[min(index, len(self.index_rows) - 1)]
            ticks.append(_tick_from_row(row, self.index_token, self.index_symbol, "NSE", role="INDEX"))
        for stream in self.option_streams:
            if not stream.rows:
                continue
            row = stream.rows[min(index, len(stream.rows) - 1)]
            ticks.append(_tick_from_row(row, stream.instrument["instrument_token"], stream.instrument["tradingsymbol"], stream.instrument["exchange"], role=stream.instrument["instrument_type"]))
        return ticks

    def _latest_option_quotes(self) -> dict[str, dict[str, Any]]:
        index = max(0, min(self._cursor - 1, self.row_count - 1))
        quotes: dict[str, dict[str, Any]] = {}
        for stream in self.option_streams:
            if not stream.rows:
                continue
            row = stream.rows[min(index, len(stream.rows) - 1)]
            tick = _tick_from_row(row, stream.instrument["instrument_token"], stream.instrument["tradingsymbol"], stream.instrument["exchange"], role=stream.instrument["instrument_type"])
            quote = _quote_from_tick(tick, stream.instrument)
            for key in (
                str(stream.instrument["instrument_token"]),
                stream.instrument["tradingsymbol"].upper(),
                f"{stream.instrument['exchange']}:{stream.instrument['tradingsymbol']}".upper(),
            ):
                quotes[key] = quote
        return quotes


class _OptionStream:
    def __init__(self, path: str | Path, exchange: str, lot_size: int) -> None:
        self.path = Path(path)
        self.rows = _read_rows(path)
        first = self.rows[0] if self.rows else {}
        symbol = _text(first, "tradingsymbol", "symbol", "instrument", default=self.path.stem).upper()
        option_type = _text(first, "instrument_type", "option_type", default=("CE" if "CE" in symbol else "PE" if "PE" in symbol else "")).upper()
        self.instrument = {
            "name": _underlying_from_symbol(symbol),
            "tradingsymbol": symbol,
            "instrument_token": int(_number(_value(first, "instrument_token", "token"), _token_from_symbol(symbol))),
            "exchange": _text(first, "exchange", default=exchange).upper(),
            "instrument_type": option_type,
            "option_type": option_type,
            "strike": _number(_value(first, "strike"), _strike_from_symbol(symbol)),
            "expiry": _text(first, "expiry", "option_expiry", default=""),
            "lot_size": int(_number(_value(first, "lot_size", "lot"), lot_size)),
            "tick_size": _number(_value(first, "tick_size"), 0.05),
        }


def _read_rows(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    with target.open("r", newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _tick_from_row(row: dict[str, Any], token: int, symbol: str, exchange: str, *, role: str) -> dict[str, Any]:
    ltp = _number(_value(row, "last_price", "ltp", "close", "price"))
    bid = _number(_value(row, "bid", "best_bid", "bid_price"))
    ask = _number(_value(row, "ask", "best_ask", "ask_price"))
    bid_qty = _number(_value(row, "bid_qty", "buy_quantity", "bid_quantity"))
    ask_qty = _number(_value(row, "ask_qty", "sell_quantity", "ask_quantity"))
    tick = {
        "instrument_token": int(_number(_value(row, "instrument_token", "token"), token)),
        "tradingsymbol": _text(row, "tradingsymbol", "symbol", default=symbol).upper(),
        "exchange": _text(row, "exchange", default=exchange).upper(),
        "last_price": ltp,
        "ltp": ltp,
        "volume": _number(_value(row, "volume", "volume_traded")),
        "volume_traded": _number(_value(row, "volume_traded", "volume")),
        "oi": _number(_value(row, "oi", "open_interest")),
        "timestamp": _text(row, "timestamp", "datetime", "date", default=""),
        "exchange_timestamp": _text(row, "exchange_timestamp", "timestamp", "datetime", "date", default=""),
    }
    if role != "INDEX" and (bid > 0 or ask > 0 or bid_qty > 0 or ask_qty > 0):
        tick["depth"] = {
            "buy": [{"price": bid, "quantity": bid_qty, "orders": int(_number(_value(row, "bid_orders", "buy_orders"), 1))}] if bid > 0 else [],
            "sell": [{"price": ask, "quantity": ask_qty, "orders": int(_number(_value(row, "ask_orders", "sell_orders"), 1))}] if ask > 0 else [],
        }
        tick["buy_quantity"] = bid_qty
        tick["sell_quantity"] = ask_qty
    return tick


def _quote_from_tick(tick: dict[str, Any], instrument: dict[str, Any]) -> dict[str, Any]:
    depth = tick.get("depth") or {}
    buy = (depth.get("buy") or [{}])[0]
    sell = (depth.get("sell") or [{}])[0]
    return {
        "instrument_token": instrument["instrument_token"],
        "tradingsymbol": instrument["tradingsymbol"],
        "exchange": instrument["exchange"],
        "last_price": tick.get("last_price"),
        "ltp": tick.get("last_price"),
        "bid": buy.get("price") or 0,
        "ask": sell.get("price") or 0,
        "bid_qty": buy.get("quantity") or 0,
        "ask_qty": sell.get("quantity") or 0,
        "volume": tick.get("volume") or tick.get("volume_traded") or 0,
        "oi": tick.get("oi") or 0,
        "depth": depth,
        "timestamp": tick.get("timestamp") or tick.get("exchange_timestamp") or "",
        "source": "fake_zerodha_replay",
    }


def _value(row: dict[str, Any], *keys: str) -> Any:
    lower = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key in row and row[key] not in ("", None):
            return row[key]
        value = lower.get(str(key).lower())
        if value not in ("", None):
            return value
    return None


def _text(row: dict[str, Any], *keys: str, default: str = "") -> str:
    value = _value(row, *keys)
    return str(value if value not in (None, "") else default)


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


def _strike_from_symbol(symbol: str) -> float:
    matches = re.findall(r"(\d{4,6})(CE|PE)$", str(symbol or "").upper())
    return float(matches[-1][0]) if matches else 0.0


def _token_from_symbol(symbol: str) -> int:
    text = str(symbol or "").upper()
    checksum = sum((index + 1) * ord(char) for index, char in enumerate(text))
    return checksum % 900000 + 100000


def _underlying_from_symbol(symbol: str) -> str:
    text = str(symbol or "").upper()
    for underlying in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX"):
        if text.startswith(underlying):
            return underlying
    return "NIFTY"
