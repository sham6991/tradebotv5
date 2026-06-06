from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from options_auto.constants import MODE_BACKTEST, MODE_PAPER, MODE_REAL


INDEX_QUOTE_KEYS = {
    "NIFTY": ("NSE", "NIFTY 50"),
    "SENSEX": ("BSE", "SENSEX"),
}


class OptionsAutoIndexDataProvider:
    def __init__(self, kite_client_provider=None):
        self.kite_client_provider = kite_client_provider or (lambda _mode: None)

    def get_spot(self, underlying: str, mode: str, payload: dict | None = None, index_candles=None) -> dict[str, Any]:
        underlying = str(underlying or "NIFTY").upper()
        mode = str(mode or "").upper()
        payload = dict(payload or {})
        if mode == MODE_BACKTEST:
            return self._backtest_spot(underlying, payload, index_candles)

        client_mode = MODE_REAL if mode == MODE_REAL else MODE_PAPER
        client = self.kite_client_provider("LIVE" if client_mode == MODE_REAL else "PAPER") or self.kite_client_provider(client_mode)
        exchange, symbol = INDEX_QUOTE_KEYS.get(underlying, ("NSE", underlying))
        quote_key = f"{exchange}:{symbol}"
        source = "zerodha_real_data" if client_mode == MODE_REAL else "zerodha_paper_data"
        label = "Real Zerodha" if client_mode == MODE_REAL else "Paper Data Zerodha"
        next_action = f"Check {label} connection and index quote key {quote_key}."
        if not client:
            return self._blocked(underlying, quote_key, f"Connect {label} before starting Options Auto {client_mode.title()}.", next_action)

        quote_result = self._quote(client, [quote_key])
        row = dict(quote_result.get(quote_key) or {})
        if not row:
            fallback_key = self._fallback_quote_key(client, underlying, exchange)
            if fallback_key and fallback_key != quote_key:
                quote_result = self._quote(client, [fallback_key])
                row = dict(quote_result.get(fallback_key) or {})
                quote_key = fallback_key
        spot = _number(row.get("last_price"), row.get("ltp"))
        if spot <= 0:
            return self._blocked(
                underlying,
                quote_key,
                f"{underlying} spot quote unavailable from {label}.",
                f"Check {label} connection and index quote key {quote_key}.",
            )
        timestamp = row.get("timestamp") or row.get("last_trade_time") or row.get("exchange_timestamp") or datetime.now().isoformat(timespec="seconds")
        age_seconds = row.get("age_seconds")
        return {
            "underlying": underlying,
            "spot": spot,
            "spot_source": source,
            "quote_key": quote_key,
            "timestamp": _timestamp_text(timestamp),
            "age_seconds": _number(age_seconds, _age_seconds(timestamp)) if age_seconds not in ("", None) else _age_seconds(timestamp),
            "fresh": True,
            "demo_data": False,
            "blockers": [],
            "warnings": [],
            "next_action": "",
        }

    def _backtest_spot(self, underlying: str, payload: dict[str, Any], index_candles) -> dict[str, Any]:
        manual = payload.get("backtest_spot")
        if manual not in ("", None):
            spot = _number(manual)
            if spot > 0:
                return {
                    "underlying": underlying,
                    "spot": spot,
                    "spot_source": "backtest_manual_spot",
                    "quote_key": "",
                    "timestamp": "",
                    "age_seconds": 0,
                    "fresh": True,
                    "demo_data": False,
                    "blockers": [],
                    "warnings": [],
                    "next_action": "",
                }
        spot = _first_close(index_candles)
        if spot > 0:
            return {
                "underlying": underlying,
                "spot": spot,
                "spot_source": "backtest_first_index_candle",
                "quote_key": "",
                "timestamp": "",
                "age_seconds": 0,
                "fresh": True,
                "demo_data": False,
                "blockers": [],
                "warnings": [],
                "next_action": "",
            }
        return self._blocked(underlying, "", f"{underlying} backtest spot is unavailable.", "Enter Backtest Spot Price or provide index candles.")

    def _quote(self, client: Any, keys: list[str]) -> dict[str, Any]:
        if hasattr(client, "quote"):
            return dict(client.quote(keys) or {})
        kite = getattr(client, "kite", None)
        if kite and hasattr(kite, "quote"):
            return dict(kite.quote(keys) or {})
        return {}

    def _fallback_quote_key(self, client: Any, underlying: str, preferred_exchange: str) -> str:
        for exchange in [preferred_exchange, "NSE", "BSE"]:
            for instrument in self._instruments(client, exchange):
                symbol = str(instrument.get("tradingsymbol") or "").upper()
                name = str(instrument.get("name") or "").upper()
                if symbol in {underlying, "NIFTY 50" if underlying == "NIFTY" else underlying} or name == underlying:
                    return f"{exchange}:{instrument.get('tradingsymbol')}"
        return ""

    def _instruments(self, client: Any, exchange: str) -> list[dict[str, Any]]:
        if hasattr(client, "instruments"):
            return list(client.instruments(exchange) or [])
        kite = getattr(client, "kite", None)
        if kite and hasattr(kite, "instruments"):
            return list(kite.instruments(exchange) or [])
        return []

    def _blocked(self, underlying: str, quote_key: str, blocker: str, next_action: str) -> dict[str, Any]:
        return {
            "underlying": underlying,
            "spot": None,
            "spot_source": "unavailable",
            "quote_key": quote_key,
            "timestamp": "",
            "age_seconds": None,
            "fresh": False,
            "demo_data": False,
            "blockers": [blocker],
            "warnings": [],
            "next_action": next_action,
        }


def nearest_strike(spot: float, strike_step: float) -> float:
    step = float(strike_step or 50)
    return round(float(spot) / step) * step


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


def _first_close(index_candles) -> float:
    if isinstance(index_candles, pd.DataFrame):
        rows = index_candles.to_dict("records")
    else:
        rows = list(index_candles or [])
    for row in rows:
        close = _number((row or {}).get("close"))
        if close > 0:
            return close
    return 0.0


def _timestamp_text(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value or "")


def _age_seconds(value: Any) -> float:
    if not hasattr(value, "tzinfo") and not hasattr(value, "timestamp"):
        return 0.0
    try:
        timestamp = value
        if timestamp.tzinfo is None:
            return max(0.0, (datetime.now() - timestamp).total_seconds())
        return max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds())
    except Exception:
        return 0.0
