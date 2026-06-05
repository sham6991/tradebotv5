from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .candle_feed import interval_minutes


class StockTickCandleBuilder:
    def __init__(self, interval: str = "minute") -> None:
        self.interval = interval
        self.candles: dict[str, list[dict[str, Any]]] = {}
        self.current: dict[str, dict[str, Any]] = {}
        self.stats = {"received_ticks": 0, "accepted_ticks": 0, "invalid_ticks": 0}

    def add_tick(self, symbol: str, tick: dict[str, Any], interval: str | None = None) -> dict[str, Any]:
        self.stats["received_ticks"] += 1
        symbol = str(symbol or tick.get("symbol") or tick.get("tradingsymbol") or "").upper()
        ltp = _number(tick.get("last_price"), tick.get("ltp"))
        timestamp = _parse_time(tick.get("timestamp") or tick.get("exchange_timestamp") or tick.get("last_trade_time")) or datetime.now()
        if not symbol or ltp <= 0:
            self.stats["invalid_ticks"] += 1
            return {"accepted": False, "reason": "Missing symbol or LTP."}
        bucket = _bucket_start(timestamp, interval or self.interval)
        current = self.current.get(symbol)
        completed = None
        if current and current.get("timestamp") != bucket.isoformat(timespec="seconds"):
            completed = current
            self.candles.setdefault(symbol, []).append(current)
            self.candles[symbol] = self.candles[symbol][-400:]
            current = None
        if not current:
            current = {
                "timestamp": bucket.isoformat(timespec="seconds"),
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": _number(tick.get("volume"), tick.get("volume_traded")),
            }
        else:
            current["high"] = max(float(current.get("high") or ltp), ltp)
            current["low"] = min(float(current.get("low") or ltp), ltp)
            current["close"] = ltp
            current["volume"] = max(float(current.get("volume") or 0), _number(tick.get("volume"), tick.get("volume_traded")))
        self.current[symbol] = current
        self.stats["accepted_ticks"] += 1
        return {"accepted": True, "symbol": symbol, "current_candle": dict(current), "completed_candle": completed}

    def rows(self, symbol: str, include_current: bool = True) -> list[dict[str, Any]]:
        symbol = str(symbol or "").upper()
        rows = list(self.candles.get(symbol) or [])
        if include_current and self.current.get(symbol):
            rows.append(dict(self.current[symbol]))
        return rows

    def snapshot(self) -> dict[str, Any]:
        return {"interval": self.interval, "symbols": sorted(set(self.candles) | set(self.current)), "stats": dict(self.stats)}


def _bucket_start(timestamp: datetime, interval: str) -> datetime:
    minutes = max(1, interval_minutes(interval))
    base_minute = (timestamp.minute // minutes) * minutes
    return timestamp.replace(minute=base_minute, second=0, microsecond=0)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
