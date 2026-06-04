from __future__ import annotations


def calculate_vwap(candles: list[dict]) -> float:
    total_price_volume = 0.0
    total_volume = 0.0
    for row in candles or []:
        high = float(row.get("high") or row.get("close") or 0)
        low = float(row.get("low") or row.get("close") or 0)
        close = float(row.get("close") or 0)
        volume = float(row.get("volume") or 0)
        typical = (high + low + close) / 3
        total_price_volume += typical * volume
        total_volume += volume
    return total_price_volume / total_volume if total_volume else 0.0
