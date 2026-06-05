from __future__ import annotations

from typing import Any

from options_auto.constants import ORDER_TYPE_LIMIT, ORDER_TYPE_SL
from options_auto.core.mode_guard import ModeGuard
from options_auto.execution.kite_api_manager import KiteApiManager


class KiteOrderAdapter:
    """Guarded real-order adapter.

    This adapter is intentionally thin and requires ModeGuard approval before
    every broker mutation.
    """

    def __init__(self, api: KiteApiManager, mode_guard: ModeGuard):
        self.api = api
        self.mode_guard = mode_guard

    def place_entry_buy_limit(self, tradingsymbol: str, quantity: int, price: float, exchange: str, product: str = "NRML", tag: str = "OPTIONS_AUTO") -> dict[str, Any]:
        self.mode_guard.assert_real_order_allowed()
        return self.api.call(
            "place_entry_buy_limit",
            lambda: self.api.client.place_limit_order(
                tradingsymbol=tradingsymbol,
                transaction_type="BUY",
                quantity=int(quantity),
                price=float(price),
                exchange=exchange,
                product=product,
                variety="regular",
                validity="DAY",
                tag=tag,
            ),
            priority="ENTRY",
        )

    def place_entry_limit(self, tradingsymbol: str, quantity: int, price: float, exchange: str, product: str = "NRML", tag: str = "OPTIONS_AUTO") -> dict[str, Any]:
        return self.place_entry_buy_limit(tradingsymbol, quantity, price, exchange, product, tag)

    def place_target_sell_limit(self, tradingsymbol: str, quantity: int, price: float, exchange: str, product: str = "NRML", tag: str = "OPTIONS_AUTO") -> dict[str, Any]:
        self.mode_guard.assert_real_order_allowed()
        return self.api.call(
            "place_target_sell_limit",
            lambda: self.api.client.place_limit_order(
                tradingsymbol=tradingsymbol,
                transaction_type="SELL",
                quantity=int(quantity),
                price=float(price),
                exchange=exchange,
                product=product,
                variety="regular",
                validity="DAY",
                tag=tag,
            ),
            priority="PROTECTION",
        )

    def place_target_limit(self, tradingsymbol: str, quantity: int, price: float, exchange: str, product: str = "NRML", tag: str = "OPTIONS_AUTO") -> dict[str, Any]:
        return self.place_target_sell_limit(tradingsymbol, quantity, price, exchange, product, tag)

    def place_stoploss_sell_sl_limit(self, tradingsymbol: str, quantity: int, trigger_price: float, price: float, exchange: str, product: str = "NRML", tag: str = "OPTIONS_AUTO") -> dict[str, Any]:
        self.mode_guard.assert_real_order_allowed()
        return self.api.call(
            "place_stoploss_sell_sl_limit",
            lambda: self.api.client.place_stoploss_limit_order(
                tradingsymbol=tradingsymbol,
                transaction_type="SELL",
                quantity=int(quantity),
                trigger_price=float(trigger_price),
                price=float(price),
                exchange=exchange,
                product=product,
                variety="regular",
                validity="DAY",
                tag=tag,
            ),
            priority="PROTECTION",
        )

    def place_stoploss_limit(self, tradingsymbol: str, quantity: int, trigger_price: float, price: float, exchange: str, product: str = "NRML", tag: str = "OPTIONS_AUTO") -> dict[str, Any]:
        return self.place_stoploss_sell_sl_limit(tradingsymbol, quantity, trigger_price, price, exchange, product, tag)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.mode_guard.assert_real_order_allowed()
        return self.api.call("cancel_order", lambda: self.api.client.cancel_order(order_id), priority="OCO_CANCEL")

    def modify_order(self, order_id: str, **changes: Any) -> dict[str, Any]:
        self.mode_guard.assert_real_order_allowed()
        return self.api.call("modify_order", lambda: self.api.client.modify_order(order_id=order_id, **changes), priority="PROTECTION")

    def fetch_orderbook(self) -> dict[str, Any]:
        self.mode_guard.assert_real_allowed()
        return self.api.call("fetch_orderbook", lambda: self._call_client(("orders", "orderbook")), priority="RECONCILIATION")

    def fetch_positions(self) -> dict[str, Any]:
        self.mode_guard.assert_real_allowed()
        return self.api.call("fetch_positions", lambda: self._call_client(("positions",)), priority="RECONCILIATION")

    def fetch_trades(self) -> dict[str, Any]:
        self.mode_guard.assert_real_allowed()
        return self.api.call("fetch_trades", lambda: self._call_client(("trades",)), priority="RECONCILIATION")

    def fetch_margins(self) -> dict[str, Any]:
        self.mode_guard.assert_real_allowed()
        if hasattr(self.api.client, "available_margin"):
            return self.api.call("fetch_margins", lambda: self.api.client.available_margin(), priority="RECONCILIATION")
        return self.api.call("fetch_margins", lambda: self._call_client(("margins",)), priority="RECONCILIATION")

    def supported_order_types(self) -> dict[str, str]:
        return {"entry": ORDER_TYPE_LIMIT, "target": ORDER_TYPE_LIMIT, "stoploss": ORDER_TYPE_SL}

    def _call_client(self, names: tuple[str, ...]) -> Any:
        for name in names:
            if hasattr(self.api.client, name):
                return getattr(self.api.client, name)()
            kite = getattr(self.api.client, "kite", None)
            if kite and hasattr(kite, name):
                return getattr(kite, name)()
        raise AttributeError(f"Kite client does not expose any of: {', '.join(names)}")
