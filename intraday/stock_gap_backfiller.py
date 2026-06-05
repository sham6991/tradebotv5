from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .candle_feed import candle_datetime, interval_minutes
from .historical_data import fetch_zerodha_stock_candles


def expected_missing_candles(candles: list[dict[str, Any]], interval: str, now: datetime | None = None) -> list[datetime]:
    rows = [row for row in candles or [] if candle_datetime(row)]
    if not rows:
        return []
    minutes = max(1, interval_minutes(interval))
    step = timedelta(minutes=minutes)
    last = candle_datetime(rows[-1])
    if not last:
        return []
    now = now or datetime.now()
    missing = []
    cursor = last + step
    while cursor + step <= now:
        missing.append(cursor)
        cursor += step
    return missing


def merge_stock_candles(existing: list[dict[str, Any]], fetched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_time: dict[str, dict[str, Any]] = {}
    for row in list(existing or []) + list(fetched or []):
        timestamp = str(row.get("timestamp") or row.get("datetime") or row.get("date") or row.get("time") or "")
        if timestamp:
            by_time[timestamp] = dict(row)
    return [by_time[key] for key in sorted(by_time)]


def backfill_missing_stock_candles(
    client: Any,
    stock: dict[str, Any],
    existing: list[dict[str, Any]],
    interval: str,
    from_time: datetime,
    to_time: datetime,
) -> dict[str, Any]:
    rows = fetch_zerodha_stock_candles(client, [stock], from_time, to_time, interval=interval, source="zerodha_gap_backfill")
    symbol = str(stock.get("symbol") or stock.get("tradingsymbol") or "").upper()
    fetched = list((rows.get(symbol) or {}).get("candles") or [])
    merged = merge_stock_candles(existing, fetched)
    return {
        "symbol": symbol,
        "fetched": len(fetched),
        "merged": len(merged),
        "candles": merged,
        "message": f"Backfilled {len(fetched)} missing stock candles for {symbol}.",
    }
