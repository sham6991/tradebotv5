from __future__ import annotations

from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE
from options_auto.data.index_data_provider import nearest_strike


class OptionChainBuilder:
    def build(
        self,
        instruments: list[dict[str, Any]],
        underlying: str,
        spot: float,
        span: int = 4,
        strike_step: float | None = None,
        expiry: Any = None,
    ) -> dict[str, Any]:
        underlying = str(underlying or "NIFTY").upper()
        span = max(0, int(float(span or 0)))
        relevant = [dict(item) for item in instruments or [] if _is_underlying_option(item, underlying)]
        step = float(strike_step or _derive_step(relevant) or (100 if underlying == "SENSEX" else 50))
        atm = nearest_strike(float(spot or 0), step)
        strikes = candidate_strikes(atm, step, span)
        expiry_text = _expiry_text(expiry)
        found = []
        requested = []
        by_key = {}
        for item in relevant:
            item_type = str(item.get("instrument_type") or item.get("option_type") or "").upper()
            strike = _number(item.get("strike"))
            item_expiry = _expiry_text(item.get("expiry"))
            if item_type not in {SIDE_CE, SIDE_PE} or strike not in strikes:
                continue
            if expiry_text and item_expiry != expiry_text:
                continue
            key = (strike, item_type)
            current = by_key.get(key)
            if current is None or _expiry_text(current.get("expiry")) > item_expiry:
                by_key[key] = item
        for strike in strikes:
            for option_type in (SIDE_CE, SIDE_PE):
                requested.append({"strike": strike, "option_type": option_type})
                row = by_key.get((strike, option_type))
                if row:
                    found.append(_normalise_contract(row, underlying))
        return {
            "underlying": underlying,
            "spot": float(spot or 0),
            "atm": atm,
            "strike_step": step,
            "span": span,
            "strikes": strikes,
            "requested_contracts": requested,
            "contracts": found,
            "contracts_requested": len(requested),
            "contracts_found": len(found),
            "missing_contracts": [item for item in requested if not _has_contract(found, item["strike"], item["option_type"])],
        }


def candidate_strikes(atm: float, step: float, span: int) -> list[float]:
    step = float(step or 1)
    span = max(0, int(span))
    return [float(atm + offset * step) for offset in range(-span, span + 1)]


def _normalise_contract(item: dict[str, Any], underlying: str) -> dict[str, Any]:
    option_type = str(item.get("instrument_type") or item.get("option_type") or "").upper()
    return {
        **item,
        "name": str(item.get("name") or item.get("underlying") or underlying).upper(),
        "underlying": underlying,
        "instrument_type": option_type,
        "option_type": option_type,
        "exchange": item.get("exchange") or ("BFO" if underlying == "SENSEX" else "NFO"),
        "strike": _number(item.get("strike")),
        "expiry": _expiry_text(item.get("expiry")),
        "lot_size": int(_number(item.get("lot_size"), 50) or 50),
        "tick_size": _number(item.get("tick_size"), 0.05) or 0.05,
    }


def _is_underlying_option(item: dict[str, Any], underlying: str) -> bool:
    option_type = str(item.get("instrument_type") or item.get("option_type") or "").upper()
    if option_type not in {SIDE_CE, SIDE_PE}:
        return False
    name = str(item.get("name") or item.get("underlying") or "").upper()
    symbol = str(item.get("tradingsymbol") or "").upper()
    segment = str(item.get("segment") or "").upper()
    return (name in {"", underlying} or symbol.startswith(underlying)) and (not segment or segment.endswith("-OPT") or "OPT" in segment)


def _derive_step(instruments: list[dict[str, Any]]) -> float:
    strikes = sorted({_number(item.get("strike")) for item in instruments if _number(item.get("strike")) > 0})
    diffs = sorted({round(strikes[index] - strikes[index - 1], 2) for index in range(1, len(strikes)) if strikes[index] > strikes[index - 1]})
    return diffs[0] if diffs else 0.0


def _has_contract(contracts: list[dict[str, Any]], strike: float, option_type: str) -> bool:
    return any(_number(item.get("strike")) == float(strike) and str(item.get("option_type") or "").upper() == option_type for item in contracts)


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
