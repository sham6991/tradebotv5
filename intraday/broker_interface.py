from __future__ import annotations

from typing import Protocol

from .order_request import OrderRequest


class BrokerInterface(Protocol):
    def login(self) -> dict:
        ...

    def get_funds(self) -> dict:
        ...

    def get_instruments(self, exchange: str | None = None) -> list[dict]:
        ...

    def get_quote(self, symbols: list[str]) -> dict:
        ...

    def get_ltp(self, symbols: list[str]) -> dict:
        ...

    def get_market_depth(self, symbols: list[str]) -> dict:
        ...

    def get_historical_candles(self, symbol: str, interval: str, from_time, to_time) -> list[dict]:
        ...

    def place_order(self, order_request: OrderRequest) -> dict:
        ...

    def modify_order(self, order_id: str, modify_request: dict) -> dict:
        ...

    def cancel_order(self, order_id: str) -> dict:
        ...

    def get_orders(self) -> list[dict]:
        ...

    def get_order_history(self, order_id: str) -> list[dict]:
        ...

    def get_trades(self) -> list[dict]:
        ...

    def get_positions(self) -> dict:
        ...

    def calculate_margin(self, order_request: OrderRequest) -> dict:
        ...

    def subscribe_ticks(self, symbols: list[str]) -> None:
        ...

    def unsubscribe_ticks(self, symbols: list[str]) -> None:
        ...
