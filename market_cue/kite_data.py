from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from typing import Any

from .models import CueValue
from .utils import INSTRUMENT_CACHE_PATH, ensure_market_cue_dir, iso_now, percent_change, safe_float


INDEX_SYMBOLS = {
    "NIFTY 50": "NSE:NIFTY 50",
    "BANK NIFTY": "NSE:NIFTY BANK",
    "India VIX": "NSE:INDIA VIX",
}


def fetch_kite_index_data(client: Any) -> dict[str, dict[str, Any]]:
    if not client:
        return {
            name: CueValue(name=name, source="Zerodha Kite", symbol=symbol, status="FAILED", warning="Virtual/Paper Zerodha data is not connected.").as_dict()
            | {"ltp_source": "unavailable"}
            for name, symbol in INDEX_SYMBOLS.items()
        }
    kite = getattr(client, "kite", client)
    results: dict[str, dict[str, Any]] = {}
    try:
        quotes = kite.quote(list(INDEX_SYMBOLS.values()))
    except Exception as exc:
        return _fetch_with_fallback_historical(client, str(exc))

    for name, symbol in INDEX_SYMBOLS.items():
        quote = quotes.get(symbol) or {}
        ohlc = quote.get("ohlc") or {}
        last_price = safe_float(quote.get("last_price"))
        previous_close = safe_float(ohlc.get("close"))
        row = CueValue(
            name=name,
            source="Zerodha Kite",
            symbol=symbol,
            value=last_price,
            previous_close=previous_close,
            percent_change=percent_change(last_price, previous_close),
            timestamp=str(quote.get("timestamp") or iso_now()),
            status="OK" if last_price is not None else "UNAVAILABLE",
            warning="" if last_price is not None else "Kite quote did not include last price.",
            raw={
                "open": safe_float(ohlc.get("open")),
                "high": safe_float(ohlc.get("high")),
                "low": safe_float(ohlc.get("low")),
            },
        ).as_dict()
        row["ltp_source"] = "live_quote"
        results[name] = row
    return results


def _fetch_with_fallback_historical(client: Any, error: str) -> dict[str, dict[str, Any]]:
    rows = {
        name: CueValue(name=name, source="Zerodha Kite", symbol=symbol, status="FAILED", warning=f"Kite quote failed: {error}").as_dict()
        for name, symbol in INDEX_SYMBOLS.items()
    }
    for name in ("NIFTY 50", "BANK NIFTY"):
        try:
            token = token_for_index(client, name)
            frame = client.historical_candles(token, datetime.now() - timedelta(days=7), datetime.now(), interval="day")
            if frame is None or frame.empty:
                continue
            last = frame.iloc[-1]
            previous = frame.iloc[-2] if len(frame) > 1 else last
            rows[name].update({
                "value": safe_float(last.get("close")),
                "previous_close": safe_float(previous.get("close")),
                "percent_change": percent_change(last.get("close"), previous.get("close")),
                "timestamp": iso_now(),
                "status": "PARTIAL",
                "warning": "Live Kite quote unavailable; latest daily historical close used as fallback.",
                "ltp_source": "historical_fallback",
            })
        except Exception as exc:
            rows[name]["warning"] = f"{rows[name]['warning']}; historical fallback failed: {exc}"
    return rows


def token_for_index(client: Any, name: str) -> int:
    if str(name).upper() == "NIFTY 50" and hasattr(client, "get_nifty50_token"):
        return int(client.get_nifty50_token())
    instruments = cached_instruments(client, "NSE")
    wanted = {"NIFTY 50": "NIFTY 50", "BANK NIFTY": "NIFTY BANK", "India VIX": "INDIA VIX"}[name]
    for instrument in instruments:
        if str(instrument.get("tradingsymbol", "")).upper() == wanted:
            return int(instrument["instrument_token"])
    fallback = {"NIFTY 50": 256265, "BANK NIFTY": 260105, "India VIX": 264969}
    return fallback[name]


def cached_instruments(client: Any, exchange: str = "NSE") -> list[dict[str, Any]]:
    ensure_market_cue_dir()
    today = date.today().isoformat()
    cache = _read_cache()
    key = str(exchange or "NSE").upper()
    if cache.get("date") == today and key in cache.get("exchanges", {}):
        return cache["exchanges"][key]
    instruments = client.instruments(key)
    cache.setdefault("exchanges", {})[key] = instruments
    cache["date"] = today
    _write_cache(cache)
    return instruments


def _read_cache() -> dict[str, Any]:
    if not os.path.exists(INSTRUMENT_CACHE_PATH):
        return {"date": "", "exchanges": {}}
    try:
        with open(INSTRUMENT_CACHE_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"date": "", "exchanges": {}}


def _write_cache(data: dict[str, Any]) -> None:
    with open(INSTRUMENT_CACHE_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, default=str)
