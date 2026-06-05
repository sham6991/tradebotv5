from __future__ import annotations

from datetime import datetime
from typing import Any

from options_auto.data.live_quote_provider import LiveQuoteProvider


class OptionsQuoteProvider:
    def __init__(self, client: Any | None = None, source: str = "zerodha_quote_snapshot"):
        self.client = client
        self.source = source
        self.normalizer = LiveQuoteProvider()

    def quote_candidates(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        pairs = [(quote_key_for(item), item) for item in candidates or [] if quote_key_for(item)]
        keys = [key for key, _candidate in pairs]
        raw = self._quote(keys)
        quotes: dict[str, dict[str, Any]] = {}
        missing = []
        for key, candidate in pairs:
            row = dict(raw.get(key) or {})
            if not row:
                missing.append(key)
                continue
            row.setdefault("source", self.source)
            row.setdefault("quote_key", key)
            row.setdefault("demo_data", False)
            normalized = self.normalizer.normalize_quote(key, row)
            normalized.update({
                "source": self.source,
                "quote_key": key,
                "exchange": candidate.get("exchange"),
                "tradingsymbol": candidate.get("tradingsymbol"),
                "instrument_token": candidate.get("instrument_token") or candidate.get("token"),
                "tick_size": candidate.get("tick_size") or row.get("tick_size") or 0.05,
                "demo_data": False,
                "timestamp": _timestamp(row),
            })
            for extra_key in (
                "premium_return_1",
                "premium_return_3",
                "relative_volume",
                "option_vwap",
                "option_atr14",
                "atr14",
                "momentum_score",
                "iv",
            ):
                if row.get(extra_key) not in ("", None):
                    normalized[extra_key] = row.get(extra_key)
            token = str(candidate.get("instrument_token") or candidate.get("token") or "")
            symbol = str(candidate.get("tradingsymbol") or "").upper()
            quotes[key] = normalized
            if token:
                quotes[token] = normalized
            if symbol:
                quotes[symbol] = normalized
        warnings = []
        if missing:
            warnings.append(f"{len(missing)} requested quote key{'s were' if len(missing) != 1 else ' was'} not returned by Zerodha.")
        return {
            "quotes": quotes,
            "missing_quote_keys": missing,
            "errors": [],
            "warnings": warnings,
            "requested_quote_keys": keys,
            "valid_quote_count": len({item.get("quote_key") for item in quotes.values() if item.get("quote_key")}),
        }

    def _quote(self, keys: list[str]) -> dict[str, Any]:
        if not self.client or not keys:
            return {}
        if hasattr(self.client, "quote"):
            return dict(self.client.quote(keys) or {})
        kite = getattr(self.client, "kite", None)
        if kite and hasattr(kite, "quote"):
            return dict(kite.quote(keys) or {})
        return {}


def quote_key_for(candidate: dict[str, Any]) -> str:
    exchange = str(candidate.get("exchange") or "NFO").upper()
    symbol = str(candidate.get("tradingsymbol") or "").upper()
    return f"{exchange}:{symbol}" if exchange and symbol else ""


def _timestamp(row: dict[str, Any]) -> str:
    value = row.get("timestamp") or row.get("last_trade_time") or row.get("exchange_timestamp")
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value or datetime.now().isoformat(timespec="seconds"))
