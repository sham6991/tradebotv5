from __future__ import annotations

from typing import Any

from options_auto.intelligence.entry_timing_engine import round_to_tick


VALID_OPTION_EXCHANGES = {"NFO", "BFO"}
VALID_PRODUCTS = {"NRML", "MIS"}


def build_real_entry_order_request(
    selected: dict[str, Any] | None,
    trade_plan: dict[str, Any] | None,
    settings: dict[str, Any] | None,
    preflight: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    selected = dict(selected or {})
    trade_plan = dict(trade_plan or {})
    settings = dict(settings or {})
    preflight = dict(preflight or {})
    blockers: list[str] = []

    symbol = trade_plan.get("tradingsymbol") or selected.get("tradingsymbol")
    exchange = str(trade_plan.get("exchange") or selected.get("exchange") or _default_option_exchange(symbol)).upper()
    token = selected.get("instrument_token") or selected.get("token") or trade_plan.get("instrument_token")
    lot_size = int(_number(trade_plan.get("lot_size"), selected.get("lot_size")))
    quantity = int(_number(trade_plan.get("quantity"), selected.get("quantity")))
    tick = _number(trade_plan.get("tick_size"), selected.get("tick_size") or 0.05)
    entry = round_to_tick(_number(trade_plan.get("entry_price")), tick)
    product = str(trade_plan.get("product") or settings.get("order_product") or "NRML").upper()
    available_margin = _available_margin(preflight)
    freeze_quantity = _freeze_quantity(selected, trade_plan, settings)

    if not symbol:
        blockers.append("Selected contract tradingsymbol is missing.")
    if not token:
        blockers.append("Selected contract instrument token is missing.")
    if exchange not in VALID_OPTION_EXCHANGES:
        blockers.append("Selected contract exchange must be NFO or BFO.")
    if lot_size <= 0:
        blockers.append("Selected contract lot size is invalid.")
    if quantity <= 0:
        blockers.append("Real order quantity is invalid.")
    if lot_size > 0 and quantity % lot_size != 0:
        blockers.append("Real order quantity must be a multiple of lot size.")
    if freeze_quantity > 0 and quantity > freeze_quantity:
        blockers.append(f"Real order quantity exceeds broker freeze quantity ({freeze_quantity}).")
    if tick <= 0:
        blockers.append("Selected contract tick size is invalid.")
    if entry <= 0:
        blockers.append("Real order entry price is invalid.")
    if product not in VALID_PRODUCTS:
        blockers.append("Real order product must be NRML or MIS.")
    if available_margin is None:
        blockers.append("Available margin is unavailable for final real-order check.")
    elif entry > 0 and quantity > 0 and available_margin < _required_cash(entry, quantity, trade_plan, selected):
        blockers.append("Available margin is insufficient for the entry order value.")

    order_request = {
        "tradingsymbol": symbol,
        "exchange": exchange,
        "instrument_token": token,
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "quantity": quantity,
        "price": entry,
        "product": product,
        "validity": "DAY",
        "variety": "regular",
        "tag": "OPTIONS_AUTO",
    }
    return order_request, list(dict.fromkeys(blockers))


def _available_margin(preflight: dict[str, Any]) -> float | None:
    checks = dict((preflight.get("evidence") or {}).get("checks") or {})
    if checks.get("available_margin") in ("", None):
        return None
    return _number(checks.get("available_margin"))


def _required_cash(entry: float, quantity: int, trade_plan: dict[str, Any], selected: dict[str, Any]) -> float:
    order_value = float(entry) * int(quantity)
    estimates = [
        _number(trade_plan.get("required_cash")),
        _number(trade_plan.get("margin_required")),
        _number(trade_plan.get("margin_required_estimate")),
        _number(selected.get("margin_required")),
        _number(selected.get("margin_required_estimate")),
    ]
    return max([order_value, *[value for value in estimates if value > 0]])


def _freeze_quantity(selected: dict[str, Any], trade_plan: dict[str, Any], settings: dict[str, Any]) -> int:
    for source in (trade_plan, selected, settings):
        value = _number(
            source.get("freeze_quantity"),
            source.get("max_order_quantity") or source.get("max_real_order_freeze_quantity") or 0,
        )
        if value > 0:
            return int(value)
    return 0


def _default_option_exchange(symbol: Any) -> str:
    return "BFO" if "SENSEX" in str(symbol or "").upper() else "NFO"


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
