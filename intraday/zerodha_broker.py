from __future__ import annotations

from datetime import datetime

from .execution_safeguards import matching_broker_order, tick_size_from_instrument
from .margin_engine import parse_required_margin
from .order_request import OrderRequest


class ZerodhaBroker:
    def __init__(self, zerodha_client):
        self.zerodha = zerodha_client
        self.api_failures: list[dict] = []
        self.api_failure_counts: dict[str, int] = {}
        self.real_order_pause_reason = ""

    def login(self) -> dict:
        return self._call("profile", self.zerodha.profile)

    def get_funds(self) -> dict:
        return {"available": self._call("funds", self.zerodha.available_margin)}

    def get_instruments(self, exchange: str | None = None) -> list[dict]:
        return self._call("instrument", lambda: self.zerodha.instruments(exchange))

    def get_quote(self, symbols: list[str]) -> dict:
        return self._call("quote", lambda: self.zerodha.kite.quote(symbols))

    def get_ltp(self, symbols: list[str]) -> dict:
        return self._call("quote", lambda: self.zerodha.kite.ltp(symbols))

    def get_market_depth(self, symbols: list[str]) -> dict:
        return self._call("quote", lambda: self.zerodha.kite.quote(symbols))

    def get_historical_candles(self, symbol: str, interval: str, from_time, to_time) -> list[dict]:
        raise NotImplementedError("Historical stock candles require instrument token mapping before use.")

    def place_order(self, order_request: OrderRequest) -> dict:
        order_request.validate(market_orders_enabled=False)
        params = order_request.to_kite_params()
        order_id = self._call("order", lambda: self.zerodha.kite.place_order(**params))
        return {"order_id": order_id, "status": "PLACED", "params": params}

    def place_emergency_order(self, order_request: OrderRequest) -> dict:
        order_request.validate(market_orders_enabled=True)
        params = order_request.to_kite_params()
        params["tag"] = "TBEMERGENCY"
        order_id = self._call("order", lambda: self.zerodha.kite.place_order(**params))
        return {"order_id": order_id, "status": "EMERGENCY_PLACED", "params": params}

    def modify_order(self, order_id: str, modify_request: dict) -> dict:
        payload = {"variety": modify_request.pop("variety", "regular"), "order_id": order_id, **modify_request}
        return self._call("order", lambda: self.zerodha.kite.modify_order(**payload))

    def cancel_order(self, order_id: str) -> dict:
        return self._call("order", lambda: self.zerodha.cancel_order(order_id, variety="regular"))

    def get_orders(self) -> list[dict]:
        return self._call("order_book", self.zerodha.orders)

    def get_order_history(self, order_id: str) -> list[dict]:
        return self._call("order_book", lambda: self.zerodha.kite.order_history(order_id))

    def get_trades(self) -> list[dict]:
        return self._call("order_book", self.zerodha.kite.trades)

    def get_positions(self) -> dict:
        return self._call("position", self.zerodha.kite.positions)

    def calculate_margin(self, order_request: OrderRequest) -> dict:
        params = order_request.to_kite_params()
        if not hasattr(self.zerodha.kite, "order_margins"):
            exc = RuntimeError("Zerodha margin API is unavailable for this client.")
            self._record_api_failure("margin", exc)
            raise exc
        response = self._call("margin", lambda: self.zerodha.kite.order_margins([params]))
        required = parse_required_margin(response)
        if required is None:
            exc = RuntimeError("Zerodha margin API did not return required margin.")
            self._record_api_failure("margin", exc)
            raise exc
        available = float(self._call("funds", self.zerodha.available_margin) or 0)
        return {
            "required": required,
            "actual_required_margin": required,
            "available": available,
            "ok": required <= available,
            "raw_margin_response": response,
        }

    def find_matching_order(self, order_request: OrderRequest, tick_size: float | None = None) -> dict | None:
        orders = self.get_orders()
        return matching_broker_order(order_request, orders, tick_size or tick_size_from_instrument({}))

    def api_health_blockers(self) -> list[str]:
        return [self.real_order_pause_reason] if self.real_order_pause_reason else []

    def _call(self, operation: str, fn):
        try:
            result = fn()
        except Exception as exc:
            self._record_api_failure(operation, exc)
            raise
        self.api_failure_counts[operation] = 0
        return result

    def _record_api_failure(self, operation: str, exc: Exception) -> None:
        count = self.api_failure_counts.get(operation, 0) + 1
        self.api_failure_counts[operation] = count
        failure = {
            "operation": operation,
            "error": str(exc),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "count": count,
        }
        self.api_failures.append(failure)
        if operation in {"quote", "margin", "order", "instrument", "funds", "order_book", "position"}:
            self.real_order_pause_reason = (
                f"Broker API health guard paused real orders after {operation} failure: {exc}"
            )

    def subscribe_ticks(self, symbols: list[str]) -> None:
        return None

    def unsubscribe_ticks(self, symbols: list[str]) -> None:
        return None
