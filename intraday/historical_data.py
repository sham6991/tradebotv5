from __future__ import annotations

from datetime import datetime
from typing import Any

from .candle_feed import depth_from_ltp, market_open_close, normalize_interval, stock_symbol_exchange


def fetch_zerodha_stock_day(zerodha_client, stocks: list[Any], trade_date: str, interval: str = "minute") -> dict[str, Any]:
    from_time, to_time = market_open_close(trade_date)
    return fetch_zerodha_stock_candles(zerodha_client, stocks, from_time, to_time, interval=interval, source="zerodha_historical")


def fetch_zerodha_stock_candles(
    zerodha_client,
    stocks: list[Any],
    from_time: datetime,
    to_time: datetime,
    interval: str = "minute",
    source: str = "zerodha_live_candles",
) -> dict[str, Any]:
    if not zerodha_client:
        return {}
    kite_interval = normalize_interval(interval)
    data = {}
    for stock in stocks:
        symbol, exchange = stock_symbol_exchange(stock)
        instrument = _find_instrument(zerodha_client, exchange, symbol)
        if not instrument:
            continue
        frame = zerodha_client.historical_candles(instrument["instrument_token"], from_time, to_time, interval=kite_interval)
        records = frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame or [])
        candles = []
        for row in records:
            candles.append({
                "timestamp": _clean_time(row.get("date") or row.get("datetime") or row.get("time")),
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": float(row.get("close") or 0),
                "volume": float(row.get("volume") or 0),
            })
        if candles:
            ltp = candles[-1]["close"]
            quote = _live_quote(zerodha_client, exchange, symbol) if "live" in str(source or "").lower() else {}
            quote_data = quote.get("data") or {}
            quote_error = quote.get("error", "")
            live_ltp = _float_or_none(quote_data.get("last_price"))
            depth = quote_data.get("depth") if isinstance(quote_data.get("depth"), dict) else None
            timestamp = _clean_time(quote_data.get("timestamp") or quote_data.get("last_trade_time"))
            data[symbol] = {
                "ltp": live_ltp or ltp,
                "candles": candles,
                "full_candles": candles,
                "future_candles": [],
                "depth": depth or depth_from_ltp(live_ltp or ltp),
                "depth_source": "zerodha_quote" if depth else "synthetic_from_ltp",
                "source": source,
                "interval": kite_interval,
                "instrument_token": instrument.get("instrument_token"),
                "ohlc": quote_data.get("ohlc") or {},
                "lower_circuit_limit": quote_data.get("lower_circuit_limit"),
                "upper_circuit_limit": quote_data.get("upper_circuit_limit"),
                "last_tick_time": timestamp,
                "quote_timestamp": timestamp,
                "quote_error": quote_error,
                "last_candle_time": candles[-1]["timestamp"],
                "candles_available": len(candles),
            }
    return data


def _find_instrument(zerodha_client, exchange: str, symbol: str) -> dict | None:
    try:
        instruments = zerodha_client.instruments(exchange)
    except Exception:
        return None
    for instrument in instruments:
        if str(instrument.get("tradingsymbol") or "").upper() == symbol:
            return instrument
    return None


def _clean_time(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _live_quote(zerodha_client, exchange: str, symbol: str) -> dict[str, Any]:
    try:
        kite = getattr(zerodha_client, "kite", None)
        if not kite or not hasattr(kite, "quote"):
            return {"data": {}, "error": "quote API unavailable"}
        key = f"{exchange}:{symbol}"
        response = kite.quote([key])
        data = response.get(key) if isinstance(response, dict) else None
        return {"data": data or {}, "error": "" if data else "quote not returned"}
    except Exception as exc:
        return {"data": {}, "error": str(exc)}


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
