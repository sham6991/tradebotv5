from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from options_auto.core.clock import iso_now


@dataclass
class PaperBroker:
    starting_balance: float = 20000.0
    available_balance: float | None = None
    orders: list[dict[str, Any]] = field(default_factory=list)
    ledger: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.available_balance is None:
            self.available_balance = float(self.starting_balance)

    def place_limit_buy(self, tradingsymbol: str, quantity: int, price: float, tag: str = "OPTIONS_AUTO_PAPER") -> dict[str, Any]:
        required = int(quantity) * float(price)
        if required > float(self.available_balance or 0):
            raise ValueError("Insufficient paper balance.")
        self.available_balance = float(self.available_balance or 0) - required
        order = {
            "order_id": f"PAPER-{uuid4().hex[:10].upper()}",
            "tradingsymbol": tradingsymbol,
            "transaction_type": "BUY",
            "quantity": int(quantity),
            "price": float(price),
            "status": "COMPLETE",
            "average_price": float(price),
            "tag": tag,
            "created_at": iso_now(),
        }
        self.orders.append(order)
        self.ledger.append({
            "timestamp": order["created_at"],
            "type": "BUY",
            "amount": -required,
            "balance": self.available_balance,
            "order_id": order["order_id"],
        })
        return order

    def place_limit_sell(self, tradingsymbol: str, quantity: int, price: float, tag: str = "OPTIONS_AUTO_PAPER") -> dict[str, Any]:
        proceeds = int(quantity) * float(price)
        self.available_balance = float(self.available_balance or 0) + proceeds
        order = {
            "order_id": f"PAPER-{uuid4().hex[:10].upper()}",
            "tradingsymbol": tradingsymbol,
            "transaction_type": "SELL",
            "quantity": int(quantity),
            "price": float(price),
            "status": "COMPLETE",
            "average_price": float(price),
            "tag": tag,
            "created_at": iso_now(),
        }
        self.orders.append(order)
        self.ledger.append({
            "timestamp": order["created_at"],
            "type": "SELL",
            "amount": proceeds,
            "balance": self.available_balance,
            "order_id": order["order_id"],
        })
        return order

    def apply_charges(self, amount: float, reason: str, order_id: str = "") -> dict[str, Any]:
        amount = float(amount or 0)
        self.available_balance = float(self.available_balance or 0) - amount
        row = {
            "timestamp": iso_now(),
            "type": "CHARGES",
            "amount": -amount,
            "balance": self.available_balance,
            "order_id": order_id,
            "reason": reason,
        }
        self.ledger.append(row)
        return row

    def cancel_order(self, order_id: str) -> str:
        for order in self.orders:
            if order["order_id"] == order_id and order["status"] not in {"COMPLETE", "CANCELLED"}:
                order["status"] = "CANCELLED"
                return "CANCELLED"
        return "NOT_REQUIRED"

    def snapshot(self) -> dict[str, Any]:
        return {
            "opening_balance": self.starting_balance,
            "available_balance": self.available_balance,
            "orders": self.orders[-100:],
            "ledger": self.ledger[-100:],
        }
