from __future__ import annotations

from .constants import SIDE_LONG, SIDE_SHORT


def stoploss_limit_prices(side: str, stoploss: float, buffer: float) -> dict:
    side = str(side or "").upper()
    trigger = round(float(stoploss), 2)
    buffer = max(0.05, float(buffer or 0.05))
    if side == SIDE_LONG:
        limit = round(max(0.05, trigger - buffer), 2)
    elif side == SIDE_SHORT:
        limit = round(trigger + buffer, 2)
    else:
        raise ValueError("Side must be LONG or SHORT for stoploss pricing.")
    return {"trigger_price": trigger, "limit_price": limit}


def target_transaction(side: str) -> str:
    return "SELL" if str(side or "").upper() == SIDE_LONG else "BUY"


def stoploss_transaction(side: str) -> str:
    return "SELL" if str(side or "").upper() == SIDE_LONG else "BUY"
