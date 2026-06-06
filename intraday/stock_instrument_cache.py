from __future__ import annotations

from typing import Any

from options_auto.data.persistent_instrument_cache import PersistentInstrumentCache


class StockInstrumentCache:
    def __init__(self, cache_dir: str | None = None) -> None:
        self.cache = PersistentInstrumentCache(cache_dir)

    def instruments(self, client: Any, exchange: str = "NSE", refresh: bool = False) -> dict[str, Any]:
        return self.cache.get_or_fetch(client, exchange, _client_instruments, refresh=refresh)

    def clear(self, exchange: str | None = None) -> None:
        self.cache.clear(exchange)


def _client_instruments(client: Any, exchange: str) -> list[dict[str, Any]]:
    if hasattr(client, "instruments"):
        return list(client.instruments(exchange) or [])
    kite = getattr(client, "kite", None)
    if kite and hasattr(kite, "instruments"):
        return list(kite.instruments(exchange) or [])
    return []
