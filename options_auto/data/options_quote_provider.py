from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from options_auto.data.live_quote_provider import LiveQuoteProvider


class OptionsQuoteProvider:
    def __init__(self, client: Any | None = None, source: str = "zerodha_quote_snapshot"):
        self.client = client
        self.source = source or "zerodha_snapshot_quote"
        self.normalizer = LiveQuoteProvider()

    def quote_candidates(self, candidates: list[dict[str, Any]], settings: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        pairs = [(quote_key_for(item), item) for item in candidates or [] if quote_key_for(item)]
        keys = [key for key, _candidate in pairs]
        max_batch = max(1, int(_number(settings.get("max_full_quote_batch_size"), 500)))
        raw: dict[str, Any] = {}
        errors: list[str] = []
        for batch in _chunks(keys, max_batch):
            batch_result = self._quote(batch)
            raw.update(batch_result.get("rows") or {})
            errors.extend(batch_result.get("errors") or [])
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
            timestamp = _timestamp(row)
            age_seconds = _age_seconds(row)
            normalized.update({
                "source": self.source,
                "quote_source": "zerodha_snapshot_quote",
                "quote_key": key,
                "exchange": candidate.get("exchange"),
                "tradingsymbol": candidate.get("tradingsymbol"),
                "instrument_token": candidate.get("instrument_token") or candidate.get("token"),
                "tick_size": candidate.get("tick_size") or row.get("tick_size") or 0.05,
                "demo_data": False,
                "timestamp": timestamp,
                "age_seconds": age_seconds,
                "stale": age_seconds > _number(settings.get("max_quote_age_seconds"), settings.get("quote_stale_seconds") or 3),
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
        if errors:
            warnings.append("Zerodha quote snapshot failed; new entries should pause until the next healthy quote.")
        return {
            "quotes": quotes,
            "missing_quote_keys": missing,
            "errors": errors,
            "warnings": warnings,
            "requested_quote_keys": keys,
            "valid_quote_count": len({item.get("quote_key") for item in quotes.values() if item.get("quote_key")}),
            "quote_source": "zerodha_snapshot_quote",
            "data_mode": "QUOTE_SNAPSHOT_POLLING",
            "blocked": bool(errors),
        }

    def _quote(self, keys: list[str]) -> dict[str, Any]:
        if not self.client or not keys:
            return {"rows": {}, "errors": []}
        try:
            if hasattr(self.client, "quote"):
                return {"rows": dict(self.client.quote(keys) or {}), "errors": []}
            kite = getattr(self.client, "kite", None)
            if kite and hasattr(kite, "quote"):
                return {"rows": dict(kite.quote(keys) or {}), "errors": []}
            return {"rows": {}, "errors": ["Connected Zerodha client does not expose quote()."]}
        except Exception as exc:
            return {"rows": {}, "errors": [f"Zerodha quote API failed: {exc}"]}


def quote_key_for(candidate: dict[str, Any]) -> str:
    exchange = str(candidate.get("exchange") or "NFO").upper()
    symbol = str(candidate.get("tradingsymbol") or "").upper()
    return f"{exchange}:{symbol}" if exchange and symbol else ""


def _timestamp(row: dict[str, Any]) -> str:
    value = row.get("timestamp") or row.get("last_trade_time") or row.get("exchange_timestamp")
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value or datetime.now().isoformat(timespec="seconds"))


def _age_seconds(row: dict[str, Any]) -> float:
    if row.get("age_seconds") not in ("", None):
        return _number(row.get("age_seconds"), 9999.0)
    value = row.get("timestamp") or row.get("last_trade_time") or row.get("exchange_timestamp")
    if value in ("", None):
        return 0.0
    try:
        when = value if hasattr(value, "timestamp") else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if when.tzinfo is None:
            return max(0.0, (datetime.now() - when).total_seconds())
        return max(0.0, (datetime.now(timezone.utc) - when).total_seconds())
    except Exception:
        return 0.0


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


def _chunks(rows: list[str], size: int):
    for index in range(0, len(rows), size):
        yield rows[index : index + size]
