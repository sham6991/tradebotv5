from __future__ import annotations

import json
import os
from datetime import date
from typing import Any


class StockInstrumentCache:
    def __init__(self, cache_dir: str | None = None) -> None:
        self.cache_dir = cache_dir or os.path.join("data", "instrument_cache")

    def instruments(self, client: Any, exchange: str = "NSE", refresh: bool = False) -> dict[str, Any]:
        exchange = str(exchange or "NSE").upper()
        path = self._path(exchange)
        if not refresh and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle) or {}
                if payload.get("trading_date") == date.today().isoformat():
                    return payload
            except (OSError, ValueError, TypeError):
                pass
        rows = _client_instruments(client, exchange)
        payload = {"exchange": exchange, "trading_date": date.today().isoformat(), "instruments": rows}
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        return payload

    def clear(self, exchange: str | None = None) -> None:
        if exchange:
            path = self._path(str(exchange).upper())
            if os.path.exists(path):
                os.remove(path)
            return
        if not os.path.isdir(self.cache_dir):
            return
        for name in os.listdir(self.cache_dir):
            if name.endswith(".json"):
                os.remove(os.path.join(self.cache_dir, name))

    def _path(self, exchange: str) -> str:
        return os.path.join(self.cache_dir, f"{exchange.lower()}_instruments.json")


def _client_instruments(client: Any, exchange: str) -> list[dict[str, Any]]:
    if hasattr(client, "instruments"):
        return list(client.instruments(exchange) or [])
    kite = getattr(client, "kite", None)
    if kite and hasattr(kite, "instruments"):
        return list(kite.instruments(exchange) or [])
    return []
