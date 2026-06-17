from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UnderlyingSpec:
    underlying_id: str
    display_name: str
    spot_quote_key: str
    derivative_aliases: tuple[str, ...]
    future_exchange_candidates: tuple[str, ...]
    option_exchange_candidates: tuple[str, ...]
    index_exchange: str
    strike_step: int
    default_lot_size: int
    option_symbol_rules: dict[str, Any]
    future_symbol_rules: dict[str, Any]


UNDERLYING_SPECS: dict[str, UnderlyingSpec] = {
    "NIFTY": UnderlyingSpec(
        underlying_id="NIFTY",
        display_name="NIFTY 50",
        spot_quote_key="NSE:NIFTY 50",
        derivative_aliases=("NIFTY", "NIFTY 50"),
        future_exchange_candidates=("NFO", "NFO-FUT"),
        option_exchange_candidates=("NFO", "NFO-OPT"),
        index_exchange="NSE",
        strike_step=50,
        default_lot_size=75,
        option_symbol_rules={"match_alias": True, "types": ("CE", "PE")},
        future_symbol_rules={"match_alias": True, "type": "FUT"},
    ),
    "SENSEX": UnderlyingSpec(
        underlying_id="SENSEX",
        display_name="SENSEX",
        spot_quote_key="BSE:SENSEX",
        derivative_aliases=("SENSEX", "BSE SENSEX"),
        future_exchange_candidates=("BFO", "BFO-FUT", "BSE", "BSE-FUT"),
        option_exchange_candidates=("BFO", "BFO-OPT", "BSE", "BSE-OPT"),
        index_exchange="BSE",
        strike_step=100,
        default_lot_size=10,
        option_symbol_rules={"match_alias": True, "types": ("CE", "PE")},
        future_symbol_rules={"match_alias": True, "type": "FUT"},
    ),
}


def normalize_underlying_id(value: Any) -> str:
    text = str(value or "NIFTY").strip().upper().replace(" ", "_")
    if text in {"NIFTY50", "NIFTY_50"}:
        return "NIFTY"
    if text in {"BSE_SENSEX"}:
        return "SENSEX"
    return text if text in UNDERLYING_SPECS else "NIFTY"


def get_underlying_spec(value: Any) -> UnderlyingSpec:
    return UNDERLYING_SPECS[normalize_underlying_id(value)]
