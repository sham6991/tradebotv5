from __future__ import annotations

from typing import Any


class KiteDataProvider:
    """Read-only Kite adapter for Options Auto data.

    Order methods intentionally do not exist here.
    """

    def __init__(self, kite_client: Any | None = None):
        self.kite_client = kite_client

    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]:
        if not self.kite_client:
            return []
        if hasattr(self.kite_client, "instruments"):
            return list(self.kite_client.instruments(exchange) if exchange else self.kite_client.instruments())
        kite = getattr(self.kite_client, "kite", None)
        if kite and hasattr(kite, "instruments"):
            return list(kite.instruments(exchange) if exchange else kite.instruments())
        return []

    def quote(self, symbols: list[str]) -> dict[str, Any]:
        if not self.kite_client:
            return {}
        if hasattr(self.kite_client, "quote"):
            return dict(self.kite_client.quote(symbols))
        kite = getattr(self.kite_client, "kite", None)
        if kite and hasattr(kite, "quote"):
            return dict(kite.quote(symbols))
        return {}

