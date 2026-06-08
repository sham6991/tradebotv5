from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from options_auto.core.clock import iso_now


@dataclass
class PaperBroker:
    starting_balance: float = 20000.0
    available_balance: float | None = None
    reserved_balance: float = 0.0
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
        self.reserved_balance = float(self.reserved_balance or 0) + required
        order = {
            "order_id": f"PAPER-{uuid4().hex[:10].upper()}",
            "tradingsymbol": tradingsymbol,
            "transaction_type": "BUY",
            "quantity": int(quantity),
            "price": float(price),
            "status": "OPEN",
            "average_price": 0.0,
            "reserved_amount": required,
            "tag": tag,
            "created_at": iso_now(),
        }
        self.orders.append(order)
        return order

    def fill_limit_buy(self, order_id: str, fill_price: float) -> dict[str, Any]:
        for order in self.orders:
            if order.get("order_id") != order_id:
                continue
            if order.get("status") != "OPEN" or order.get("transaction_type") != "BUY":
                raise ValueError("Paper buy order is not fillable.")
            quantity = int(order["quantity"])
            fill_amount = quantity * float(fill_price)
            reserved = float(order.get("reserved_amount") or 0)
            release = max(0.0, reserved - fill_amount)
            self.reserved_balance = max(0.0, float(self.reserved_balance or 0) - reserved)
            self.available_balance = float(self.available_balance or 0) + release
            order["status"] = "COMPLETE"
            order["average_price"] = float(fill_price)
            order["filled_at"] = iso_now()
            order["released_amount"] = release
            self.ledger.append({
                "timestamp": order["filled_at"],
                "type": "BUY",
                "amount": -fill_amount,
                "balance": self.available_balance,
                "reserved_balance": self.reserved_balance,
                "order_id": order["order_id"],
            })
            return order
        raise ValueError("Paper buy order not found.")

    def cancel_order(self, order_id: str) -> str:
        for order in self.orders:
            if order["order_id"] == order_id and order["status"] not in {"COMPLETE", "CANCELLED"}:
                if order.get("transaction_type") == "BUY":
                    reserved = float(order.get("reserved_amount") or 0)
                    self.reserved_balance = max(0.0, float(self.reserved_balance or 0) - reserved)
                    self.available_balance = float(self.available_balance or 0) + reserved
                order["status"] = "CANCELLED"
                order["cancelled_at"] = iso_now()
                return "CANCELLED"
        return "NOT_REQUIRED"

    def complete_open_sell(self, order_id: str, average_price: float) -> dict[str, Any] | None:
        for order in self.orders:
            if order.get("order_id") != order_id:
                continue
            if order.get("transaction_type") == "SELL" and order.get("status") == "OPEN":
                proceeds = int(order["quantity"]) * float(average_price)
                self.available_balance = float(self.available_balance or 0) + proceeds
                order["status"] = "COMPLETE"
                order["average_price"] = float(average_price)
                order["filled_at"] = iso_now()
                self.ledger.append({
                    "timestamp": order["filled_at"],
                    "type": "SELL",
                    "amount": proceeds,
                    "balance": self.available_balance,
                    "order_id": order["order_id"],
                })
                return order
        return None

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

    def snapshot(self) -> dict[str, Any]:
        charges = sum(abs(float(row.get("amount") or 0)) for row in self.ledger if str(row.get("type") or "").upper() == "CHARGES")
        cash_pnl = float(self.available_balance or 0) + float(self.reserved_balance or 0) - float(self.starting_balance or 0)
        return {
            "opening_balance": self.starting_balance,
            "available_balance": self.available_balance,
            "reserved_balance": self.reserved_balance,
            "realized_pnl": round(cash_pnl, 2),
            "unrealized_pnl": 0.0,
            "charges": round(charges, 2),
            "orders": list(self.orders),
            "ledger": list(self.ledger),
        }
