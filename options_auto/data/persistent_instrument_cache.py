from __future__ import annotations

import csv
import os
from datetime import date
from typing import Any


OPTIONS_EXCHANGES = {"NFO", "BFO"}
STOCK_EXCHANGES = {"NSE", "BSE"}
FIELDS = (
    "exchange",
    "tradingsymbol",
    "instrument_token",
    "name",
    "underlying",
    "expiry",
    "strike",
    "instrument_type",
    "lot_size",
    "tick_size",
    "segment",
)


class PersistentInstrumentCache:
    def __init__(self, cache_dir: str | None = None) -> None:
        self.cache_dir = cache_dir or os.path.join(os.getcwd(), "data", "instruments")

    def path_for(self, exchange: str, trade_day: date | None = None) -> str:
        exchange = str(exchange or "").upper()
        trade_day = trade_day or date.today()
        prefix = "options" if exchange in OPTIONS_EXCHANGES else "stocks"
        return os.path.join(self.cache_dir, f"{prefix}_{exchange}_{trade_day.strftime('%Y%m%d')}.csv")

    def load(self, exchange: str, trade_day: date | None = None) -> list[dict[str, Any]]:
        path = self.path_for(exchange, trade_day)
        if not os.path.isfile(path):
            return []
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return [_coerce_row(row) for row in csv.DictReader(handle)]

    def save(self, exchange: str, rows: list[dict[str, Any]], trade_day: date | None = None) -> str:
        os.makedirs(self.cache_dir, exist_ok=True)
        path = self.path_for(exchange, trade_day)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(FIELDS))
            writer.writeheader()
            for row in rows or []:
                writer.writerow(_normalise_row(row, exchange))
        return path

    def get_or_fetch(self, client: Any, exchange: str, fetcher, refresh: bool = False) -> dict[str, Any]:
        path = self.path_for(exchange)
        rows = [] if refresh else self.load(exchange)
        source = "daily_file" if rows else "zerodha_fetch"
        if not rows:
            rows = list(fetcher(client, exchange) or [])
            if rows:
                path = self.save(exchange, rows)
        return {
            "exchange": str(exchange or "").upper(),
            "cache_date": date.today().isoformat(),
            "path": path,
            "source": source,
            "rows": [_coerce_row(row) for row in rows],
            "stale": False,
            "stable_key": "exchange:tradingsymbol",
        }

    def clear(self, exchange: str | None = None) -> None:
        if not os.path.isdir(self.cache_dir):
            return
        for name in os.listdir(self.cache_dir):
            if not name.endswith(".csv"):
                continue
            if exchange and f"_{str(exchange).upper()}_" not in name:
                continue
            try:
                os.remove(os.path.join(self.cache_dir, name))
            except OSError:
                pass


def missing_expired_metadata_blocker(selected_date: Any) -> str:
    return "Expired option metadata unavailable for selected date. Upload archived instrument cache or option candle CSV."


def _normalise_row(row: dict[str, Any], exchange: str) -> dict[str, Any]:
    row = dict(row or {})
    return {field: _text(row.get(field), row.get("option_type") if field == "instrument_type" else "") for field in FIELDS} | {
        "exchange": _text(row.get("exchange"), exchange).upper(),
        "tradingsymbol": _text(row.get("tradingsymbol")).upper(),
        "expiry": _expiry_text(row.get("expiry")),
    }


def _coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row or {})
    for key in ("strike", "lot_size", "instrument_token"):
        if result.get(key) not in ("", None):
            try:
                result[key] = int(float(result[key]))
            except (TypeError, ValueError):
                pass
    if result.get("tick_size") not in ("", None):
        try:
            result["tick_size"] = float(result["tick_size"])
        except (TypeError, ValueError):
            pass
    return result


def _text(value: Any, default: Any = "") -> str:
    return str(value if value not in (None, "") else default)


def _expiry_text(value: Any) -> str:
    if value in ("", None):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]
