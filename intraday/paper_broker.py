from __future__ import annotations

from datetime import datetime

from .constants import ORDER_STATUS_PENDING, ORDER_STATUS_REJECTED
from .order_request import OrderRequest


class PaperBroker:
    def __init__(self, starting_balance: float = 100000.0, account_store=None, estimated_leverage: float = 5.0):
        self.account_store = account_store
        self.estimated_leverage = max(1.0, float(estimated_leverage or 1))
        self.starting_balance = float(starting_balance)
        self.cash = float(starting_balance)
        self.orders: list[dict] = []
        self.trades: list[dict] = []
        self.positions: dict[str, dict] = {}
        self._order_counter = 0

    def login(self) -> dict:
        return {"connected": True, "mode": "PAPER"}

    def get_funds(self) -> dict:
        if self.account_store:
            return self.account_store.snapshot()
        return {"available": self.cash, "starting_balance": self.starting_balance, "used_margin": 0.0, "equity": self.cash}

    def get_instruments(self, exchange: str | None = None) -> list[dict]:
        return []

    def get_quote(self, symbols: list[str]) -> dict:
        return {}

    def get_ltp(self, symbols: list[str]) -> dict:
        return {}

    def get_market_depth(self, symbols: list[str]) -> dict:
        return {}

    def get_historical_candles(self, symbol: str, interval: str, from_time, to_time) -> list[dict]:
        return []

    def place_order(self, order_request: OrderRequest) -> dict:
        order_request.validate(market_orders_enabled=False)
        self._order_counter += 1
        order_id = f"PB{self._order_counter:06d}"
        params = order_request.to_kite_params()
        margin = self.calculate_margin(order_request)
        if not margin.get("ok", False):
            return {
                "order_id": order_id,
                "status": ORDER_STATUS_REJECTED,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "params": params,
                "error": "Insufficient paper funds",
                "margin": margin,
            }
        if params["order_type"] == "LIMIT" and self.account_store:
            self.account_store.reserve_margin(margin.get("required") or 0)
        status = ORDER_STATUS_PENDING
        row = {
            "order_id": order_id,
            "status": status,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "params": params,
        }
        self.orders.append(row)
        return row

    def _record_trade(self, order_id: str, params: dict) -> None:
        price = float(params.get("price") or 0)
        quantity = int(params.get("quantity") or 0)
        self.trades.append({
            "order_id": order_id,
            "symbol": params.get("tradingsymbol"),
            "transaction_type": params.get("transaction_type"),
            "quantity": quantity,
            "price": price,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })

    def modify_order(self, order_id: str, modify_request: dict) -> dict:
        for order in self.orders:
            if order["order_id"] == order_id:
                order["params"].update(modify_request)
                return {"order_id": order_id, "status": order["status"], "params": order["params"]}
        return {"order_id": order_id, "status": "NOT_FOUND"}

    def cancel_order(self, order_id: str) -> dict:
        for order in self.orders:
            if order["order_id"] == order_id:
                if order["status"] == ORDER_STATUS_PENDING and self.account_store:
                    params = order.get("params") or {}
                    margin = float(params.get("price") or 0) * int(params.get("quantity") or 0) / self.estimated_leverage
                    self.account_store.release_margin(margin)
                order["status"] = "CANCELLED"
                return {"order_id": order_id, "status": "CANCELLED"}
        return {"order_id": order_id, "status": "NOT_FOUND"}

    def get_orders(self) -> list[dict]:
        return list(self.orders)

    def get_order_history(self, order_id: str) -> list[dict]:
        return [order for order in self.orders if order["order_id"] == order_id]

    def get_trades(self) -> list[dict]:
        return list(self.trades)

    def get_positions(self) -> dict:
        return dict(self.positions)

    def calculate_margin(self, order_request: OrderRequest) -> dict:
        notional = float(order_request.price or 0) * int(order_request.quantity or 0)
        required = notional / self.estimated_leverage
        available = self.account_store.snapshot()["available"] if self.account_store else self.cash
        return {
            "required": required,
            "estimated_margin": required,
            "actual_required_margin": required,
            "trade_value": notional,
            "available": available,
            "estimated_leverage": self.estimated_leverage,
            "ok": required <= available,
        }

    def subscribe_ticks(self, symbols: list[str]) -> None:
        return None

    def unsubscribe_ticks(self, symbols: list[str]) -> None:
        return None
