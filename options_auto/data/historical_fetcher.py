from __future__ import annotations

from datetime import datetime
from typing import Any

from options_auto.data.cache_manager import JsonCacheManager


class HistoricalFetcher:
    def __init__(self, provider: Any | None = None, cache: JsonCacheManager | None = None):
        self.provider = provider
        self.cache = cache

    def fetch(self, instrument_token: str, from_dt: str, to_dt: str, interval: str = "3minute") -> dict[str, Any]:
        key = f"hist_{instrument_token}_{from_dt}_{to_dt}_{interval}"
        if self.cache:
            cached = self.cache.get(key, max_age_seconds=86400)
            if cached:
                return {**cached, "cache_hit": True}
        candles = []
        if self.provider and hasattr(self.provider, "historical_data"):
            candles = self.provider.historical_data(instrument_token, from_dt, to_dt, interval)
        payload = {
            "instrument_token": instrument_token,
            "from": from_dt,
            "to": to_dt,
            "interval": interval,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "candles": list(candles or []),
            "cache_hit": False,
        }
        if self.cache:
            self.cache.set(key, payload)
        return payload

