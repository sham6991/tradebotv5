from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from main_app.decision_kernel import TradePlan
from main_app.execution.brokers import BrokerBase


@dataclass
class LifecycleState:
    state: str = "IDLE"
    entry_order_id: str = ""
    stoploss_order_id: str = ""
    target_order_id: str = ""
    protection_status: str = "NONE"
    reconciliation_status: str = "OK"
    events: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


class OrderLifecycleEngine:
    def __init__(self, broker: BrokerBase):
        self.broker = broker
        self.state = LifecycleState()

    def submit_entry(self, plan: TradePlan) -> LifecycleState:
        self._validate_plan(plan)
        order = self.broker.place_limit_buy(plan.tradingsymbol, plan.exchange, plan.quantity, plan.entry_limit, product="NRML")
        self.state.entry_order_id = order.get("order_id", "")
        self.state.state = "ENTRY_OPEN"
        self._event("ENTRY_LIMIT_PLACED", order)
        return self.state

    def on_entry_filled(self, plan: TradePlan, average_price: float | None = None, filled_quantity: int | None = None) -> LifecycleState:
        quantity = int(filled_quantity or plan.quantity)
        if quantity <= 0:
            self.state.state = "ENTRY_NOT_FILLED"
            self.state.blockers.append("Entry fill quantity is zero.")
            return self.state
        sl = self.broker.place_sl_limit_sell(
            plan.tradingsymbol,
            plan.exchange,
            quantity,
            plan.stoploss_trigger,
            plan.stoploss_limit,
            product="NRML",
        )
        self.state.stoploss_order_id = sl.get("order_id", "")
        self._event("STOPLOSS_SL_LIMIT_PLACED", sl)
        if not self._verify_stoploss_active(sl):
            self.state.state = "PROTECTION_FAILED"
            self.state.protection_status = "FAILED"
            self.state.blockers.append("Stoploss could not be verified active. Target was not placed.")
            return self.state
        self.state.protection_status = "STOPLOSS_ACTIVE"
        target = self.broker.place_limit_sell(plan.tradingsymbol, plan.exchange, quantity, plan.target_limit, product="NRML")
        self.state.target_order_id = target.get("order_id", "")
        self.state.state = "OCO_ACTIVE"
        self._event("TARGET_LIMIT_PLACED", target)
        return self.state

    def on_target_filled(self) -> LifecycleState:
        if self.state.stoploss_order_id:
            self.broker.cancel_order(self.state.stoploss_order_id)
        self.state.state = "FLAT_CONFIRMED"
        self.state.protection_status = "TARGET_FILLED_SL_CANCELLED"
        self._event("TARGET_FILLED", {"cancelled_stoploss": self.state.stoploss_order_id})
        return self.state

    def on_stoploss_filled(self) -> LifecycleState:
        if self.state.target_order_id:
            self.broker.cancel_order(self.state.target_order_id)
        self.state.state = "FLAT_CONFIRMED"
        self.state.protection_status = "SL_FILLED_TARGET_CANCELLED"
        self._event("STOPLOSS_FILLED", {"cancelled_target": self.state.target_order_id})
        return self.state

    def on_both_exit_orders_filled(self) -> LifecycleState:
        self.state.state = "MANUAL_RECONCILIATION_REQUIRED"
        self.state.reconciliation_status = "CRITICAL_DOUBLE_FILL"
        self.state.blockers.append("Both target and stoploss appear filled. Verify broker net position manually.")
        self._event("MANUAL_RECONCILIATION_REQUIRED", {})
        return self.state

    def _validate_plan(self, plan: TradePlan) -> None:
        if plan.entry_order_type != "LIMIT" or plan.stoploss_order_type != "SL" or plan.target_order_type != "LIMIT":
            raise ValueError("Main App lifecycle requires BUY LIMIT, SELL SL-LIMIT, and SELL LIMIT.")
        if plan.product != "NRML":
            raise ValueError("Main App lifecycle requires NRML product.")
        if int(plan.quantity) != int(plan.lots) * int(plan.lot_size):
            raise ValueError("Quantity must equal user-defined lots times instrument lot size.")

    def _verify_stoploss_active(self, order: dict[str, Any]) -> bool:
        status = str(order.get("status") or "").upper()
        return status in {"OPEN", "TRIGGER_PENDING", "MODIFIED"}

    def _event(self, event: str, payload: dict[str, Any]) -> None:
        self.state.events.append({"event": event, "payload": dict(payload or {})})
