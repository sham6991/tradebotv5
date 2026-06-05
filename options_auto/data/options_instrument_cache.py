from __future__ import annotations

from datetime import date
from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE


class OptionsInstrumentCache:
    def __init__(self) -> None:
        self._cache: dict[tuple[int, str, str], list[dict[str, Any]]] = {}

    def instruments(self, client: Any, exchange: str) -> list[dict[str, Any]]:
        exchange = str(exchange or "NFO").upper()
        key = (id(client), exchange, date.today().isoformat())
        if key not in self._cache:
            self._cache[key] = _client_instruments(client, exchange)
        return [dict(row) for row in self._cache[key]]

    def find_option_contract(
        self,
        client: Any,
        underlying: str,
        expiry: Any,
        strike: int | float,
        option_type: str,
        exchange: str,
    ) -> dict[str, Any] | None:
        return find_option_contract(
            underlying=underlying,
            expiry=expiry,
            strike=strike,
            option_type=option_type,
            exchange=exchange,
            instruments=self.instruments(client, exchange),
        )

    def clear(self) -> None:
        self._cache.clear()


def find_option_contract(
    underlying: str,
    expiry: Any,
    strike: int | float,
    option_type: str,
    exchange: str,
    instruments: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    underlying = _underlying_key(underlying)
    aliases = _underlying_aliases(underlying)
    expiry_text = _expiry_text(expiry)
    option_type = str(option_type or "").upper()
    exchange = str(exchange or "").upper()
    if option_type not in {SIDE_CE, SIDE_PE}:
        return None
    try:
        strike_value = float(strike)
    except (TypeError, ValueError):
        return None

    matches: list[dict[str, Any]] = []
    for row in instruments or []:
        candidate = dict(row or {})
        row_type = str(candidate.get("instrument_type") or candidate.get("option_type") or "").upper()
        if row_type != option_type:
            continue
        if abs(_number(candidate.get("strike")) - strike_value) > 0.0001:
            continue
        row_exchange = str(candidate.get("exchange") or exchange).upper()
        if exchange and row_exchange and row_exchange != exchange:
            continue
        row_expiry = _expiry_text(candidate.get("expiry"))
        if expiry_text and row_expiry != expiry_text:
            continue
        name = _underlying_key(candidate.get("name") or candidate.get("underlying") or "")
        symbol = str(candidate.get("tradingsymbol") or "").upper()
        if name not in {"", underlying} and name not in aliases and not any(symbol.startswith(alias) for alias in aliases):
            continue
        matches.append(_normalise_contract(candidate, underlying, exchange))
    if not matches:
        return None
    matches.sort(key=lambda item: (_expiry_text(item.get("expiry")) or "9999-12-31", str(item.get("tradingsymbol") or "")))
    return matches[0]


def get_contract_lot_size(contract: dict[str, Any] | None) -> int:
    if not contract:
        return 0
    try:
        lot_size = int(float(contract.get("lot_size")))
    except (TypeError, ValueError):
        return 0
    return lot_size if lot_size > 0 else 0


def _client_instruments(client: Any, exchange: str) -> list[dict[str, Any]]:
    if not client:
        return []
    if hasattr(client, "instruments"):
        return list(client.instruments(exchange) or [])
    kite = getattr(client, "kite", None)
    if kite and hasattr(kite, "instruments"):
        return list(kite.instruments(exchange) or [])
    return []


def _normalise_contract(row: dict[str, Any], underlying: str, exchange: str) -> dict[str, Any]:
    option_type = str(row.get("instrument_type") or row.get("option_type") or "").upper()
    return {
        **row,
        "name": _display_underlying(underlying),
        "underlying": _display_underlying(underlying),
        "exchange": row.get("exchange") or exchange,
        "instrument_type": option_type,
        "option_type": option_type,
        "expiry": _expiry_text(row.get("expiry")),
        "strike": int(_number(row.get("strike"))),
        "lot_size": get_contract_lot_size(row),
        "tick_size": _number(row.get("tick_size"), 0.05) or 0.05,
    }


def _underlying_key(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"NIFTY BANK", "BANK NIFTY"}:
        return "BANKNIFTY"
    return text


def _display_underlying(value: Any) -> str:
    key = _underlying_key(value)
    return "BANKNIFTY" if key == "BANKNIFTY" else key


def _underlying_aliases(underlying: str) -> set[str]:
    key = _underlying_key(underlying)
    aliases = {key}
    if key == "BANKNIFTY":
        aliases.update({"NIFTYBANK", "NIFTY BANK", "BANK NIFTY"})
    return aliases


def _expiry_text(value: Any) -> str:
    if value in ("", None):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
