from __future__ import annotations

from typing import Any


AUTO_TAG = "OPTIONS_AUTO"
OPEN_ORDER_STATUSES = {
    "OPEN",
    "TRIGGER PENDING",
    "PENDING",
    "VALIDATION PENDING",
    "PUT ORDER REQ RECEIVED",
    "AMO REQ RECEIVED",
    "OPEN PENDING",
    "MODIFY PENDING",
}
TERMINAL_ORDER_STATUSES = {"COMPLETE", "CANCELLED", "REJECTED", "EXPIRED"}
PROTECTION_ORDER_TYPES = {"SL", "SL-M", "SL-LIMIT", "STOPLOSS", "STOPLOSS_LIMIT"}


class ReconciliationEngine:
    def reconcile(
        self,
        local_orders: list[dict[str, Any]] | None,
        broker_orders: list[dict[str, Any]] | None,
        positions: list[dict[str, Any]] | dict[str, Any] | None = None,
        trade_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        local_orders = list(local_orders or [])
        broker_orders = list(broker_orders or [])
        positions = self._normalise_positions(positions)
        trade_plan = dict(trade_plan or {})
        blockers = []
        broker_by_id = {str(order.get("order_id") or order.get("id") or ""): order for order in broker_orders}
        local_ids = {str(item.get("order_id") or item.get("id") or "") for item in local_orders}
        for order in local_orders:
            order_id = str(order.get("order_id") or "")
            if order_id and order_id not in broker_by_id:
                blockers.append(f"Local order {order_id} is missing in broker order book.")
        unknown_auto_orders = [
            order for order in broker_orders
            if self._is_auto_order(order)
            and str(order.get("order_id") or order.get("id") or "") not in local_ids
        ]
        if unknown_auto_orders:
            blockers.append("Broker has unknown Options Auto tagged orders.")
        unknown_manual_orders = [
            order for order in broker_orders
            if self._is_open_order(order) and not self._is_auto_order(order)
        ]
        if unknown_manual_orders:
            blockers.append("Broker has unknown manual open orders; block new Options Auto entries.")
        open_positions = [position for position in positions if float(position.get("quantity") or position.get("net_quantity") or 0) != 0]
        if open_positions and not broker_orders:
            blockers.append("Open position exists without broker order context.")
        unprotected_positions = self._unprotected_positions(open_positions, broker_orders)
        if unprotected_positions:
            blockers.append("Open position is missing live target/stoploss protection.")
        duplicate_orders = self._duplicate_orders(broker_orders, trade_plan)
        if duplicate_orders:
            blockers.append("Duplicate broker order already exists for the planned Options Auto action.")
        return {
            "ok": not blockers,
            "state": "RECONCILED" if not blockers else "MANUAL_ATTENTION_REQUIRED",
            "blockers": blockers,
            "unknown_auto_orders": unknown_auto_orders,
            "unknown_manual_orders": unknown_manual_orders,
            "open_positions": open_positions,
            "unprotected_positions": unprotected_positions,
            "duplicate_orders": duplicate_orders,
        }

    def _normalise_positions(self, positions: list[dict[str, Any]] | dict[str, Any] | None) -> list[dict[str, Any]]:
        if isinstance(positions, dict):
            rows: list[dict[str, Any]] = []
            for key in ("net", "day", "positions"):
                value = positions.get(key)
                if isinstance(value, list):
                    rows.extend([dict(item) for item in value if isinstance(item, dict)])
            return rows
        return [dict(item) for item in list(positions or []) if isinstance(item, dict)]

    def _is_auto_order(self, order: dict[str, Any]) -> bool:
        return AUTO_TAG in str(order.get("tag") or order.get("tags") or order.get("order_tag") or "").upper()

    def _status(self, order: dict[str, Any]) -> str:
        return str(order.get("status") or "").upper()

    def _is_open_order(self, order: dict[str, Any]) -> bool:
        status = self._status(order)
        if status in TERMINAL_ORDER_STATUSES:
            return False
        return status in OPEN_ORDER_STATUSES or not status

    def _symbol(self, row: dict[str, Any]) -> str:
        return str(row.get("tradingsymbol") or row.get("symbol") or row.get("instrument") or "").upper()

    def _quantity(self, row: dict[str, Any]) -> int:
        try:
            return abs(int(float(row.get("quantity") or row.get("net_quantity") or row.get("filled_quantity") or 0)))
        except (TypeError, ValueError):
            return 0

    def _unprotected_positions(self, open_positions: list[dict[str, Any]], broker_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unprotected = []
        open_auto_sell_orders = [
            order for order in broker_orders
            if self._is_auto_order(order)
            and self._is_open_order(order)
            and str(order.get("transaction_type") or "").upper() == "SELL"
        ]
        for position in open_positions:
            symbol = self._symbol(position)
            if not symbol:
                unprotected.append(position)
                continue
            symbol_orders = [order for order in open_auto_sell_orders if self._symbol(order) == symbol]
            has_target = any(str(order.get("order_type") or "").upper() == "LIMIT" for order in symbol_orders)
            has_stop = any(str(order.get("order_type") or "").upper() in PROTECTION_ORDER_TYPES for order in symbol_orders)
            if not (has_target and has_stop):
                unprotected.append(position)
        return unprotected

    def _duplicate_orders(self, broker_orders: list[dict[str, Any]], trade_plan: dict[str, Any]) -> list[dict[str, Any]]:
        symbol = self._symbol(trade_plan)
        quantity = self._quantity(trade_plan)
        if not symbol or quantity <= 0:
            return []
        duplicates = []
        for order in broker_orders:
            if not self._is_auto_order(order) or not self._is_open_order(order):
                continue
            if self._symbol(order) != symbol:
                continue
            order_qty = self._quantity(order)
            if order_qty and order_qty != quantity:
                continue
            duplicates.append(order)
        return duplicates
