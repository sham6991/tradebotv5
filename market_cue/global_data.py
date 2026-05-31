from __future__ import annotations

from typing import Any

from .database import MarketCueDatabase
from .models import CueValue, YFINANCE_SYMBOLS
from .utils import iso_now, percent_change, safe_float


def fetch_global_cues(db: MarketCueDatabase | None = None) -> dict[str, dict[str, Any]]:
    db = db or MarketCueDatabase()
    try:
        import yfinance as yf
    except ImportError:
        return {
            name: _cached_or_failed(db, name, symbol, "yfinance is not installed.")
            for name, symbol in YFINANCE_SYMBOLS.items()
        }

    results: dict[str, dict[str, Any]] = {}
    for name, symbol in YFINANCE_SYMBOLS.items():
        try:
            ticker = yf.Ticker(symbol)
            info = getattr(ticker, "fast_info", {}) or {}
            last_price = _info_get(info, "last_price", "lastPrice")
            previous_close = _info_get(info, "previous_close", "previousClose", "regularMarketPreviousClose")
            timestamp = _info_get(info, "last_trade_time", "lastTradeTime", "regular_market_time", "regularMarketTime") or iso_now()
            if last_price is None or previous_close is None:
                row = _history_fallback_row(ticker, name, symbol)
                if row:
                    results[name] = row
                    db.cache_value("yfinance", symbol, row)
                    continue
            change = percent_change(last_price, previous_close)
            status = "OK" if last_price is not None else "UNAVAILABLE"
            warning = "" if change is not None else "Previous close unavailable; percent change not calculated."
            row = CueValue(
                name=name,
                source="yfinance",
                symbol=symbol,
                value=safe_float(last_price),
                previous_close=safe_float(previous_close),
                percent_change=change,
                timestamp=str(timestamp),
                status=status,
                warning=warning if status == "OK" else "Value unavailable from yfinance.",
                raw={"symbol": symbol, "fetch_mode": "fast_info"},
            ).as_dict()
            row["fetch_mode"] = "fast_info"
            results[name] = row
            if row["status"] == "OK":
                db.cache_value("yfinance", symbol, row)
        except Exception as exc:
            results[name] = _cached_or_failed(db, name, symbol, str(exc))
    return results


def _info_get(info: Any, *keys: str) -> Any:
    for key in keys:
        try:
            if hasattr(info, "get"):
                value = info.get(key)
            else:
                value = getattr(info, key)
        except Exception:
            value = None
        if value is not None:
            return value
    return None


def _history_fallback_row(ticker: Any, name: str, symbol: str) -> dict[str, Any] | None:
    try:
        history = ticker.history(period="5d", interval="1d")
    except Exception:
        return None
    if history is None or getattr(history, "empty", True) or "Close" not in history:
        return None
    closes = history["Close"].dropna()
    if len(closes) < 2:
        return None
    last_price = safe_float(closes.iloc[-1])
    previous_close = safe_float(closes.iloc[-2])
    timestamp = str(closes.index[-1]) if len(closes.index) else iso_now()
    row = CueValue(
        name=name,
        source="yfinance",
        symbol=symbol,
        value=last_price,
        previous_close=previous_close,
        percent_change=percent_change(last_price, previous_close),
        timestamp=timestamp,
        status="PARTIAL",
        warning="fast_info was incomplete; yfinance daily history fallback was used.",
        raw={"symbol": symbol, "fetch_mode": "history_fallback"},
    ).as_dict()
    row["fetch_mode"] = "history_fallback"
    return row


def _cached_or_failed(db: MarketCueDatabase, name: str, symbol: str, error: str) -> dict[str, Any]:
    cached = db.latest_cache("yfinance", symbol, max_age_hours=36)
    if cached:
        cached["name"] = name
        cached["source"] = "yfinance"
        cached["warning"] = f"Fresh fetch failed ({error}); stale cache shown."
        return cached
    return CueValue(
        name=name,
        source="yfinance",
        symbol=symbol,
        status="FAILED",
        warning=error,
    ).as_dict()
