from __future__ import annotations

from datetime import datetime, time
from typing import Any


INTERVAL_TO_KITE = {
    "1": "minute",
    "1m": "minute",
    "1 min": "minute",
    "1minute": "minute",
    "minute": "minute",
    "2": "2minute",
    "2m": "2minute",
    "2 min": "2minute",
    "2minute": "2minute",
    "3": "3minute",
    "3m": "3minute",
    "3 min": "3minute",
    "3minute": "3minute",
    "5": "5minute",
    "5m": "5minute",
    "5 min": "5minute",
    "5minute": "5minute",
}

INTERVAL_MINUTES = {
    "minute": 1,
    "2minute": 2,
    "3minute": 3,
    "5minute": 5,
}


def normalize_interval(value: Any) -> str:
    return INTERVAL_TO_KITE.get(str(value or "minute").strip().lower(), "minute")


def interval_minutes(value: Any) -> int:
    return INTERVAL_MINUTES.get(normalize_interval(value), 1)


def market_open_close(trade_date: str) -> tuple[datetime, datetime]:
    day = datetime.fromisoformat(str(trade_date)[:10]).date()
    return datetime.combine(day, time(9, 15)), datetime.combine(day, time(15, 30))


def stock_symbol_exchange(stock: Any) -> tuple[str, str]:
    if isinstance(stock, dict):
        symbol = str(stock.get("symbol") or stock.get("tradingsymbol") or "").strip().upper()
        exchange = str(stock.get("exchange") or "NSE").strip().upper() or "NSE"
    else:
        text = str(stock or "").strip().upper()
        if ":" in text:
            exchange, symbol = text.split(":", 1)
        else:
            symbol, exchange = text, "NSE"
    return symbol, exchange


def candle_timestamp(candle: dict[str, Any]) -> str:
    value = candle.get("timestamp") or candle.get("date") or candle.get("datetime") or candle.get("time")
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value or "")


def candle_datetime(candle: dict[str, Any]) -> datetime | None:
    value = candle_timestamp(candle)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def full_candles(row: dict[str, Any]) -> list[dict[str, Any]]:
    candles = list(row.get("full_candles") or [])
    if candles:
        return candles
    return list(row.get("candles") or []) + list(row.get("future_candles") or [])


def depth_from_ltp(ltp: float, bid_qty: int = 10000, ask_qty: int = 10000) -> dict[str, list[dict[str, float]]]:
    return {
        "buy": [{"price": round(float(ltp) - 0.05, 2), "quantity": bid_qty}],
        "sell": [{"price": round(float(ltp) + 0.05, 2), "quantity": ask_qty}],
    }


def market_slice(full_data: dict[str, Any], cursor: int, lookback: int = 0) -> dict[str, Any]:
    rows = {}
    for symbol, row in full_data.items():
        candles = full_candles(row)
        if not candles:
            continue
        end = min(cursor + 1, len(candles))
        start = max(0, end - int(lookback or end))
        visible = candles[start:end]
        if not visible:
            continue
        ltp = float(visible[-1].get("close") or row.get("ltp") or 0)
        rows[symbol] = {
            **row,
            "ltp": ltp,
            "candles": visible,
            "future_candles": [],
            "full_candles": candles,
            "depth": row.get("depth") or depth_from_ltp(ltp),
            "last_candle_time": candle_timestamp(visible[-1]),
            "candles_available": len(visible),
        }
    return rows


def max_candle_count(full_data: dict[str, Any]) -> int:
    counts = [len(full_candles(row)) for row in full_data.values()]
    return max(counts) if counts else 0
