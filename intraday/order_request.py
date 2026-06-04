from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .constants import EXCHANGE_NSE, ORDER_LIMIT_ONLY, SIDE_LONG, SIDE_SHORT


def _clean_tag(value: str) -> str:
    cleaned = "".join(ch for ch in str(value or "") if ch.isalnum())
    return (cleaned or "TBINTRADAY")[:20]


@dataclass(frozen=True)
class OrderRequest:
    exchange: str
    tradingsymbol: str
    transaction_type: str
    quantity: int
    product: str = "MIS"
    order_type: str = "LIMIT"
    variety: str = "regular"
    price: float | None = None
    trigger_price: float | None = None
    validity: str = "DAY"
    tag: str = "TBINTRADAY"

    def validate(self, market_orders_enabled: bool = False) -> None:
        if not self.tradingsymbol:
            raise ValueError("Tradingsymbol is required.")
        if self.exchange not in {"NSE", "BSE"}:
            raise ValueError("Intraday equity orders must use NSE or BSE.")
        if self.transaction_type not in {"BUY", "SELL"}:
            raise ValueError("Transaction type must be BUY or SELL.")
        if int(self.quantity) <= 0:
            raise ValueError("Quantity must be greater than zero.")
        if self.product != "MIS":
            raise ValueError("Intraday stocks terminal only sends MIS equity orders.")
        if self.order_type == "MARKET" and not market_orders_enabled:
            raise ValueError("Market orders are disabled for this locked session.")
        if self.order_type in {"LIMIT", "SL"} and self.price in ("", None):
            raise ValueError(f"{self.order_type} order requires a limit price.")
        if self.order_type == "SL" and self.trigger_price in ("", None):
            raise ValueError("SL order requires a trigger price.")

    def to_kite_params(self) -> dict[str, Any]:
        payload = {
            "variety": self.variety,
            "exchange": self.exchange,
            "tradingsymbol": self.tradingsymbol,
            "transaction_type": self.transaction_type,
            "quantity": int(self.quantity),
            "product": self.product,
            "order_type": self.order_type,
            "validity": self.validity,
            "tag": _clean_tag(self.tag),
        }
        if self.price not in ("", None):
            payload["price"] = float(self.price)
        if self.trigger_price not in ("", None):
            payload["trigger_price"] = float(self.trigger_price)
        return payload

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tag"] = _clean_tag(self.tag)
        return data


def session_tag(session_id: str, suffix: str) -> str:
    compact = "".join(ch for ch in str(session_id or "") if ch.isalnum())
    return _clean_tag(f"TB{compact[-8:]}{suffix}")


def entry_order(
    symbol: str,
    side: str,
    quantity: int,
    entry_price: float,
    exchange: str = EXCHANGE_NSE,
    session_id: str = "",
    order_mode: str = ORDER_LIMIT_ONLY,
) -> OrderRequest:
    side = str(side or "").upper()
    transaction = "BUY" if side == SIDE_LONG else "SELL"
    order_type = "MARKET" if str(order_mode).upper() == "MARKET_ALLOWED" and entry_price in ("", None, 0) else "LIMIT"
    return OrderRequest(
        exchange=exchange,
        tradingsymbol=str(symbol).upper(),
        transaction_type=transaction,
        quantity=quantity,
        order_type=order_type,
        price=None if order_type == "MARKET" else float(entry_price),
        tag=session_tag(session_id or datetime.now().strftime("%H%M%S"), "ENT"),
    )


def stoploss_order(
    symbol: str,
    position_side: str,
    quantity: int,
    trigger_price: float,
    limit_price: float,
    exchange: str = EXCHANGE_NSE,
    session_id: str = "",
) -> OrderRequest:
    position_side = str(position_side or "").upper()
    transaction = "SELL" if position_side == SIDE_LONG else "BUY"
    return OrderRequest(
        exchange=exchange,
        tradingsymbol=str(symbol).upper(),
        transaction_type=transaction,
        quantity=quantity,
        order_type="SL",
        trigger_price=float(trigger_price),
        price=float(limit_price),
        tag=session_tag(session_id or datetime.now().strftime("%H%M%S"), "SL"),
    )


def target_order(
    symbol: str,
    position_side: str,
    quantity: int,
    target_price: float,
    exchange: str = EXCHANGE_NSE,
    session_id: str = "",
) -> OrderRequest:
    position_side = str(position_side or "").upper()
    transaction = "SELL" if position_side == SIDE_LONG else "BUY"
    return OrderRequest(
        exchange=exchange,
        tradingsymbol=str(symbol).upper(),
        transaction_type=transaction,
        quantity=quantity,
        order_type="LIMIT",
        price=float(target_price),
        tag=session_tag(session_id or datetime.now().strftime("%H%M%S"), "TGT"),
    )


def emergency_market_order(
    symbol: str,
    net_quantity: int,
    exchange: str = EXCHANGE_NSE,
    session_id: str = "",
) -> OrderRequest:
    quantity = abs(int(net_quantity or 0))
    if quantity <= 0:
        raise ValueError("Emergency square-off quantity must be non-zero.")
    transaction = "SELL" if int(net_quantity or 0) > 0 else "BUY"
    return OrderRequest(
        exchange=exchange,
        tradingsymbol=str(symbol).upper(),
        transaction_type=transaction,
        quantity=quantity,
        order_type="MARKET",
        price=None,
        tag=session_tag(session_id or datetime.now().strftime("%H%M%S"), "EMG"),
    )
