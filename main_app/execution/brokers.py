from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


FORBIDDEN_ORDER_TYPES = {"MARKET", "SL-M", "SLM"}


class BrokerBase:
    def place_limit_buy(self, symbol: str, exchange: str, quantity: int, price: float, product: str = "NRML") -> dict[str, Any]:
        raise NotImplementedError

    def place_sl_limit_sell(self, symbol: str, exchange: str, quantity: int, trigger_price: float, limit_price: float, product: str = "NRML") -> dict[str, Any]:
        raise NotImplementedError

    def place_limit_sell(self, symbol: str, exchange: str, quantity: int, price: float, product: str = "NRML") -> dict[str, Any]:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def modify_limit_order(self, order_id: str, price: float) -> dict[str, Any]:
        raise NotImplementedError

    def modify_sl_limit_order(self, order_id: str, trigger_price: float, limit_price: float) -> dict[str, Any]:
        raise NotImplementedError

    def get_order(self, order_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_orders(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_trades(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_position(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    def available_balance(self) -> float:
        raise NotImplementedError


@dataclass
class PaperBroker(BrokerBase):
    opening_balance: float = 100000.0
    orders: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    charges: list[dict[str, Any]] = field(default_factory=list)
    used_margin: float = 0.0
    charges_per_order: float = 20.0

    def __post_init__(self) -> None:
        self.cash = float(self.opening_balance)
        self._ledger("OPENING_BALANCE", credit=self.opening_balance, remarks="paper account opened")

    def place_limit_buy(self, symbol: str, exchange: str, quantity: int, price: float, product: str = "NRML") -> dict[str, Any]:
        _validate_policy("BUY", "LIMIT", product, quantity)
        turnover = int(quantity) * float(price)
        charges = self._charges("ENTRY", turnover)
        if turnover + charges > self.cash:
            return self._order(symbol, exchange, "BUY", quantity, "LIMIT", price, 0, product, status="REJECTED", status_message="Insufficient paper cash.")
        self.cash -= turnover + charges
        self.used_margin += turnover
        order = self._order(symbol, exchange, "BUY", quantity, "LIMIT", price, 0, product, status="OPEN")
        self._ledger("ENTRY_DEBIT", reference_id=order["order_id"], debit=turnover, charges=charges, remarks="BUY LIMIT placed")
        return order

    def place_sl_limit_sell(self, symbol: str, exchange: str, quantity: int, trigger_price: float, limit_price: float, product: str = "NRML") -> dict[str, Any]:
        _validate_policy("SELL", "SL", product, quantity)
        if float(limit_price) >= float(trigger_price):
            raise ValueError("SL-LIMIT sell limit must be below trigger for long option protection.")
        return self._order(symbol, exchange, "SELL", quantity, "SL", limit_price, trigger_price, product, status="TRIGGER_PENDING")

    def place_limit_sell(self, symbol: str, exchange: str, quantity: int, price: float, product: str = "NRML") -> dict[str, Any]:
        _validate_policy("SELL", "LIMIT", product, quantity)
        return self._order(symbol, exchange, "SELL", quantity, "LIMIT", price, 0, product, status="OPEN")

    def fill_order(self, order_id: str, average_price: float) -> dict[str, Any]:
        order = self.get_order(order_id)
        if not order:
            raise ValueError("Paper order not found.")
        order["status"] = "COMPLETE"
        order["average_price"] = float(average_price)
        order["filled_quantity"] = int(order["quantity"])
        order["pending_quantity"] = 0
        order["updated_at"] = _now()
        trade = {
            "trade_id": f"PT-{uuid4().hex[:10].upper()}",
            "order_id": order_id,
            "exchange": order["exchange"],
            "tradingsymbol": order["tradingsymbol"],
            "transaction_type": order["transaction_type"],
            "quantity": int(order["quantity"]),
            "average_price": float(average_price),
            "trade_time": _now(),
            "charges_total": self._charges(order["transaction_type"], int(order["quantity"]) * float(average_price)),
            "decision_id": order.get("decision_id", ""),
        }
        self.trades.append(trade)
        self._apply_position(order, average_price)
        return order

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order and order["status"] not in {"COMPLETE", "CANCELLED"}:
            order["status"] = "CANCELLED"
            order["updated_at"] = _now()
        return order or {}

    def modify_limit_order(self, order_id: str, price: float) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order:
            order["price"] = float(price)
            order["status"] = "MODIFIED"
            order["updated_at"] = _now()
        return order or {}

    def modify_sl_limit_order(self, order_id: str, trigger_price: float, limit_price: float) -> dict[str, Any]:
        order = self.get_order(order_id)
        if order:
            order["trigger_price"] = float(trigger_price)
            order["price"] = float(limit_price)
            order["status"] = "MODIFIED"
            order["updated_at"] = _now()
        return order or {}

    def get_order(self, order_id: str) -> dict[str, Any]:
        return next((order for order in self.orders if order.get("order_id") == order_id), {})

    def get_orders(self) -> list[dict[str, Any]]:
        return [dict(order) for order in self.orders]

    def get_trades(self) -> list[dict[str, Any]]:
        return [dict(trade) for trade in self.trades]

    def get_position(self, symbol: str) -> dict[str, Any]:
        return dict(self.positions.get(symbol, {"tradingsymbol": symbol, "quantity": 0, "average_price": 0.0}))

    def available_balance(self) -> float:
        return float(self.cash)

    def _order(self, symbol: str, exchange: str, transaction_type: str, quantity: int, order_type: str, price: float, trigger_price: float, product: str, status: str, status_message: str = "") -> dict[str, Any]:
        order = {
            "order_id": f"PO-{uuid4().hex[:10].upper()}",
            "parent_order_id": "",
            "exchange": exchange,
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
            "product": product,
            "order_type": order_type,
            "quantity": int(quantity),
            "price": float(price or 0),
            "trigger_price": float(trigger_price or 0),
            "status": status,
            "status_message": status_message,
            "average_price": 0.0,
            "filled_quantity": 0,
            "pending_quantity": int(quantity),
            "cancelled_quantity": 0,
            "placed_at": _now(),
            "updated_at": _now(),
            "tag": "MAIN_APP",
            "strategy_id": "",
            "decision_id": "",
        }
        self.orders.append(order)
        return order

    def _apply_position(self, order: dict[str, Any], average_price: float) -> None:
        symbol = order["tradingsymbol"]
        qty = int(order["quantity"]) if order["transaction_type"] == "BUY" else -int(order["quantity"])
        position = self.positions.get(symbol, {"tradingsymbol": symbol, "quantity": 0, "average_price": 0.0})
        new_qty = int(position["quantity"]) + qty
        position["quantity"] = new_qty
        position["average_price"] = float(average_price) if new_qty else 0.0
        self.positions[symbol] = position
        if order["transaction_type"] == "SELL":
            turnover = int(order["quantity"]) * float(average_price)
            charges = self._charges("EXIT", turnover)
            self.cash += turnover - charges
            self.used_margin = max(0.0, self.used_margin - turnover)
            self._ledger("EXIT_CREDIT", reference_id=order["order_id"], credit=turnover, charges=charges, remarks="SELL exit filled")

    def _charges(self, kind: str, turnover: float) -> float:
        charges = float(self.charges_per_order)
        self.charges.append({"timestamp": _now(), "kind": kind, "turnover": float(turnover), "charges": charges})
        return charges

    def _ledger(self, event_type: str, reference_id: str = "", debit: float = 0.0, credit: float = 0.0, charges: float = 0.0, remarks: str = "") -> None:
        self.ledger.append({
            "ledger_id": f"PL-{uuid4().hex[:10].upper()}",
            "timestamp": _now(),
            "event_type": event_type,
            "reference_id": reference_id,
            "opening_balance": self.opening_balance,
            "debit": round(float(debit), 2),
            "credit": round(float(credit), 2),
            "charges": round(float(charges), 2),
            "realized_pnl": round(self.cash + self.used_margin - self.opening_balance, 2),
            "unrealized_pnl": 0.0,
            "closing_balance": round(self.cash + self.used_margin, 2),
            "available_cash": round(self.cash, 2),
            "used_margin": round(self.used_margin, 2),
            "remarks": remarks,
        })


class BacktestBroker(PaperBroker):
    pass


class LiveZerodhaBroker(BrokerBase):
    def __init__(self, kite_client: Any):
        self.kite = kite_client

    def place_limit_buy(self, symbol: str, exchange: str, quantity: int, price: float, product: str = "NRML") -> dict[str, Any]:
        _validate_policy("BUY", "LIMIT", product, quantity)
        return self._place(symbol, exchange, "BUY", quantity, "LIMIT", price=price, product=product)

    def place_sl_limit_sell(self, symbol: str, exchange: str, quantity: int, trigger_price: float, limit_price: float, product: str = "NRML") -> dict[str, Any]:
        _validate_policy("SELL", "SL", product, quantity)
        return self._place(symbol, exchange, "SELL", quantity, "SL", price=limit_price, trigger_price=trigger_price, product=product)

    def place_limit_sell(self, symbol: str, exchange: str, quantity: int, price: float, product: str = "NRML") -> dict[str, Any]:
        _validate_policy("SELL", "LIMIT", product, quantity)
        return self._place(symbol, exchange, "SELL", quantity, "LIMIT", price=price, product=product)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return {"status": self.kite.cancel_order(order_id), "order_id": order_id}

    def modify_limit_order(self, order_id: str, price: float) -> dict[str, Any]:
        return {"order_id": order_id, "price": price, "status": "MODIFY_REQUESTED"}

    def modify_sl_limit_order(self, order_id: str, trigger_price: float, limit_price: float) -> dict[str, Any]:
        return {"order_id": order_id, "trigger_price": trigger_price, "price": limit_price, "status": "MODIFY_REQUESTED"}

    def get_order(self, order_id: str) -> dict[str, Any]:
        return next((order for order in self.get_orders() if str(order.get("order_id")) == str(order_id)), {})

    def get_orders(self) -> list[dict[str, Any]]:
        return list(self.kite.orders())

    def get_trades(self) -> list[dict[str, Any]]:
        return list(self.kite.trades())

    def get_position(self, symbol: str) -> dict[str, Any]:
        positions = self.kite.positions() or {}
        rows = list(positions.get("net") or []) + list(positions.get("day") or [])
        return next((row for row in rows if row.get("tradingsymbol") == symbol), {})

    def available_balance(self) -> float:
        margins = self.kite.margins() or {}
        return float(((margins.get("equity") or {}).get("available") or {}).get("cash") or 0)

    def _place(self, symbol: str, exchange: str, transaction_type: str, quantity: int, order_type: str, price: float = 0.0, trigger_price: float = 0.0, product: str = "NRML") -> dict[str, Any]:
        order_id = self.kite.place_order(
            variety="regular",
            exchange=exchange,
            tradingsymbol=symbol,
            transaction_type=transaction_type,
            quantity=int(quantity),
            product=product,
            order_type=order_type,
            price=float(price or 0),
            trigger_price=float(trigger_price or 0),
        )
        return {"order_id": order_id, "status": "OPEN", "tradingsymbol": symbol, "order_type": order_type, "product": product}


def _validate_policy(transaction_type: str, order_type: str, product: str, quantity: int) -> None:
    order_type = str(order_type or "").upper()
    product = str(product or "").upper()
    if order_type in FORBIDDEN_ORDER_TYPES:
        raise ValueError("Main App is LIMIT-only. MARKET and SL-M are forbidden.")
    if order_type not in {"LIMIT", "SL"}:
        raise ValueError("Main App allows only LIMIT and SL-LIMIT orders.")
    if product != "NRML":
        raise ValueError("Main App product must be NRML.")
    if int(quantity or 0) <= 0:
        raise ValueError("Quantity must be positive and derived from exact user lots.")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
