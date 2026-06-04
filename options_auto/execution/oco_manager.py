from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.core.clock import iso_now


@dataclass
class OCOGroup:
    entry_order_id: str
    target_order_id: str
    stoploss_order_id: str
    quantity: int
    status: str = "OCO_ACTIVE"
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_order_id": self.entry_order_id,
            "target_order_id": self.target_order_id,
            "stoploss_order_id": self.stoploss_order_id,
            "quantity": self.quantity,
            "status": self.status,
            "events": self.events,
        }


class OCOManager:
    def __init__(self, broker: Any | None = None) -> None:
        self.broker = broker
        self.groups: dict[str, OCOGroup] = {}

    def register(self, trade_id: str, entry_order_id: str, target_order_id: str, stoploss_order_id: str, quantity: int) -> OCOGroup:
        if not target_order_id or not stoploss_order_id:
            raise ValueError("OCO requires both target and stoploss order ids.")
        if trade_id in self.groups and self.groups[trade_id].status == "OCO_ACTIVE":
            raise ValueError("Duplicate OCO group is not allowed for an active trade.")
        group = OCOGroup(entry_order_id, target_order_id, stoploss_order_id, int(quantity))
        group.events.append({"timestamp": iso_now(), "event": "REGISTERED"})
        self.groups[trade_id] = group
        return group

    def on_order_update(self, trade_id: str, order_id: str, status: str) -> dict[str, Any]:
        group = self.groups.get(trade_id)
        if not group:
            return {"action": "IGNORED", "reason": "Unknown OCO group."}
        status = str(status or "").upper()
        if status != "COMPLETE":
            return {"action": "WAIT", "status": status}
        if order_id == group.target_order_id:
            cancel_id = group.stoploss_order_id
            group.status = "TARGET_FILLED_CANCEL_SL"
        elif order_id == group.stoploss_order_id:
            cancel_id = group.target_order_id
            group.status = "SL_FILLED_CANCEL_TARGET"
        else:
            return {"action": "IGNORED", "reason": "Order is not part of OCO group."}
        cancel_status = "NOT_AVAILABLE"
        if self.broker and hasattr(self.broker, "cancel_order"):
            cancel_status = self.broker.cancel_order(cancel_id)
        event = {"timestamp": iso_now(), "event": group.status, "filled_order_id": order_id, "cancel_order_id": cancel_id, "cancel_status": cancel_status}
        group.events.append(event)
        return {"action": "CANCEL_PEER", **event}

