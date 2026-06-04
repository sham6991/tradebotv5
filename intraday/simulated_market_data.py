from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from .candle_feed import depth_from_ltp, interval_minutes, stock_symbol_exchange


def generate_stock_day(stocks: list[Any], trade_date: str, interval: str = "minute", bars: int | None = None) -> dict[str, Any]:
    symbols = [stock_symbol_exchange(stock) for stock in stocks]
    minutes = interval_minutes(interval)
    count = int(bars or max(40, 375 // max(1, minutes)))
    start = datetime.fromisoformat(f"{str(trade_date)[:10]}T09:15:00")
    data = {}
    for offset, (symbol, exchange) in enumerate(symbols):
        base = 900 + offset * 140
        bullish = offset % 2 == 0
        candles = []
        for index in range(count):
            drift = index * (1.25 if bullish else -0.95)
            wave = math.sin(index / 4) * 2.4
            open_price = base + drift + wave
            close = open_price + (1.8 if bullish else -1.4)
            volume = 70000 + index * 1600 + offset * 8000
            if index >= 20 and index % 30 in {0, 1, 2}:
                volume *= 2.2
            candles.append({
                "timestamp": (start + timedelta(minutes=index * minutes)).isoformat(timespec="seconds"),
                "open": round(open_price, 2),
                "high": round(max(open_price, close) + 1.8, 2),
                "low": round(min(open_price, close) - 1.6, 2),
                "close": round(close, 2),
                "volume": round(volume, 2),
            })
        ltp = candles[-1]["close"] if candles else 0.0
        bid_qty = 24000 + offset * 1200 if bullish else 17000 + offset * 900
        ask_qty = 18000 + offset * 1000 if bullish else 25000 + offset * 1200
        data[symbol] = {
            "exchange": exchange,
            "ltp": ltp,
            "candles": candles,
            "full_candles": candles,
            "future_candles": [],
            "depth": depth_from_ltp(ltp, bid_qty=bid_qty, ask_qty=ask_qty),
            "source": "simulated",
            "interval": interval,
            "last_candle_time": candles[-1]["timestamp"] if candles else "",
            "candles_available": len(candles),
        }
    return data
