from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from main_app.underlyings import UnderlyingSpec, get_underlying_spec


@dataclass
class InstrumentResolution:
    underlying_id: str
    spot_quote_key: str
    future: dict[str, Any] | None = None
    ce: dict[str, Any] | None = None
    pe: dict[str, Any] | None = None
    option_expiry: str = ""
    lot_size: int = 0
    tick_size: float = 0.0
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying_id": self.underlying_id,
            "spot_quote_key": self.spot_quote_key,
            "future": self.future or {},
            "ce": self.ce or {},
            "pe": self.pe or {},
            "option_expiry": self.option_expiry,
            "lot_size": self.lot_size,
            "tick_size": self.tick_size,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


class InstrumentResolver:
    def __init__(self, instruments: Iterable[dict[str, Any]] | None = None, today: date | None = None):
        self.instruments = [dict(row or {}) for row in list(instruments or [])]
        self.today = today or date.today()

    def resolve(
        self,
        underlying: str,
        spot_ltp: float,
        *,
        strike_offset: int = 0,
        allow_price_only_when_futures_unavailable: bool = False,
    ) -> InstrumentResolution:
        spec = get_underlying_spec(underlying)
        blockers: list[str] = []
        warnings: list[str] = []
        future = self._nearest_future(spec)
        if not future:
            message = (
                f"{spec.underlying_id} futures/options not found in current instrument master. "
                f"Cannot use futures volume confirmation for {spec.underlying_id}."
            )
            if allow_price_only_when_futures_unavailable:
                warnings.append(message)
            else:
                blockers.append(message)
        ce, pe = self._nearest_options(spec, spot_ltp, strike_offset)
        if not ce or not pe:
            blockers.append(f"{spec.underlying_id} CE/PE options not found in current instrument master.")
        lot_size = int((ce or pe or future or {}).get("lot_size") or spec.default_lot_size)
        tick_size = float((ce or pe or future or {}).get("tick_size") or 0.05)
        return InstrumentResolution(
            underlying_id=spec.underlying_id,
            spot_quote_key=spec.spot_quote_key,
            future=_stable_instrument(future) if future else None,
            ce=_stable_instrument(ce) if ce else None,
            pe=_stable_instrument(pe) if pe else None,
            option_expiry=str((ce or pe or {}).get("expiry") or ""),
            lot_size=lot_size,
            tick_size=tick_size,
            blockers=blockers,
            warnings=warnings,
        )

    def _nearest_future(self, spec: UnderlyingSpec) -> dict[str, Any] | None:
        candidates = [
            row for row in self.instruments
            if _instrument_type(row) == "FUT"
            and _expiry_date(row) is not None
            and _expiry_date(row) >= self.today
            and _exchange_or_segment(row) in set(spec.future_exchange_candidates)
            and _matches_alias(row, spec.derivative_aliases)
        ]
        candidates.sort(key=lambda row: (_expiry_date(row) or date.max, str(row.get("tradingsymbol") or "")))
        return candidates[0] if candidates else None

    def _nearest_options(self, spec: UnderlyingSpec, spot_ltp: float, strike_offset: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        atm = round(float(spot_ltp or 0) / spec.strike_step) * spec.strike_step
        wanted_strike = atm + int(strike_offset or 0) * spec.strike_step
        rows = [
            row for row in self.instruments
            if _instrument_type(row) in {"CE", "PE"}
            and _expiry_date(row) is not None
            and _expiry_date(row) >= self.today
            and _exchange_or_segment(row) in set(spec.option_exchange_candidates)
            and _matches_alias(row, spec.derivative_aliases)
        ]
        rows.sort(key=lambda row: (
            _expiry_date(row) or date.max,
            abs(float(row.get("strike") or 0) - wanted_strike),
            str(row.get("tradingsymbol") or ""),
        ))
        ce = next((row for row in rows if _instrument_type(row) == "CE"), None)
        pe = next((row for row in rows if _instrument_type(row) == "PE"), None)
        return ce, pe


def _stable_instrument(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    exchange = str(row.get("exchange") or "").upper()
    symbol = str(row.get("tradingsymbol") or "")
    return {
        "exchange": exchange,
        "tradingsymbol": symbol,
        "quote_key": f"{exchange}:{symbol}" if exchange and symbol else "",
        "name": row.get("name") or "",
        "expiry": str(row.get("expiry") or ""),
        "strike": row.get("strike") or 0,
        "tick_size": float(row.get("tick_size") or 0.05),
        "lot_size": int(row.get("lot_size") or 1),
        "instrument_type": _instrument_type(row),
        "segment": row.get("segment") or "",
        "instrument_token_runtime": row.get("instrument_token") or "",
    }


def _instrument_type(row: dict[str, Any]) -> str:
    return str(row.get("instrument_type") or "").upper()


def _exchange_or_segment(row: dict[str, Any]) -> str:
    values = {str(row.get("exchange") or "").upper(), str(row.get("segment") or "").upper()}
    return next((value for value in values if value), "")


def _matches_alias(row: dict[str, Any], aliases: tuple[str, ...]) -> bool:
    haystack = f"{row.get('name') or ''} {row.get('tradingsymbol') or ''}".upper()
    return any(alias.upper() in haystack for alias in aliases)


def _expiry_date(row: dict[str, Any]) -> date | None:
    value = row.get("expiry")
    if isinstance(value, date):
        return value
    text = str(value or "")[:10]
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None
