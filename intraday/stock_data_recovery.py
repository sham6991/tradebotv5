from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .candle_feed import candle_datetime, interval_minutes
from .stock_data_readiness import evaluate_stock_data_readiness
from .stock_gap_backfiller import backfill_missing_stock_candles, expected_missing_candles


def recover_stock_data_gaps(client: Any, settings: Any, market_data: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    interval = getattr(settings, "candle_interval", "minute")
    recovered = dict(market_data or {})
    backfills = []
    minutes = max(1, interval_minutes(interval))
    for stock in getattr(settings, "stocks", []) or []:
        symbol = str(getattr(stock, "symbol", "") or "").upper()
        exchange = str(getattr(stock, "exchange", "NSE") or "NSE").upper()
        row = dict(recovered.get(symbol) or {})
        candles = list(row.get("candles") or [])
        missing = expected_missing_candles(candles, interval, now=now)
        if not missing or not client:
            continue
        start = missing[0]
        end = now + timedelta(minutes=minutes)
        result = backfill_missing_stock_candles(
            client,
            {"symbol": symbol, "exchange": exchange},
            candles,
            interval,
            start,
            end,
        )
        row["candles"] = result["candles"]
        row["full_candles"] = result["candles"]
        if result["candles"]:
            last = result["candles"][-1]
            row["last_candle_time"] = str(last.get("timestamp") or last.get("datetime") or "")
        row["backfill_status"] = result["message"]
        recovered[symbol] = row
        backfills.append(result)
    readiness = evaluate_stock_data_readiness(settings, recovered, now=now)
    return {"market_data": recovered, "backfills": backfills, "readiness": readiness}


def latest_candle_time(candles: list[dict[str, Any]]) -> datetime | None:
    rows = list(candles or [])
    return candle_datetime(rows[-1]) if rows else None
