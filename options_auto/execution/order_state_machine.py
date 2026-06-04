from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.core.clock import iso_now


ORDER_STATES = {
    "SIGNAL_FOUND",
    "APPROVAL_PENDING",
    "ENTRY_ORDER_PLACING",
    "ENTRY_ORDER_OPEN",
    "ENTRY_PARTIAL_FILLED",
    "ENTRY_FILLED",
    "PROTECTION_PENDING",
    "TARGET_OPEN",
    "SL_OPEN",
    "OCO_ACTIVE",
    "POSITION_ACTIVE",
    "TARGET_FILLED",
    "SL_FILLED",
    "EXIT_RECONCILING",
    "CLOSED",
    "CANCELLED",
    "ERROR_MANUAL_ATTENTION",
}

ALLOWED_TRANSITIONS = {
    "SIGNAL_FOUND": {"APPROVAL_PENDING", "ENTRY_ORDER_PLACING", "CANCELLED"},
    "APPROVAL_PENDING": {"ENTRY_ORDER_PLACING", "CANCELLED"},
    "ENTRY_ORDER_PLACING": {"ENTRY_ORDER_OPEN", "ENTRY_PARTIAL_FILLED", "ENTRY_FILLED", "ERROR_MANUAL_ATTENTION"},
    "ENTRY_ORDER_OPEN": {"ENTRY_PARTIAL_FILLED", "ENTRY_FILLED", "CANCELLED", "ERROR_MANUAL_ATTENTION"},
    "ENTRY_PARTIAL_FILLED": {"ENTRY_FILLED", "PROTECTION_PENDING", "ERROR_MANUAL_ATTENTION"},
    "ENTRY_FILLED": {"PROTECTION_PENDING", "ERROR_MANUAL_ATTENTION"},
    "PROTECTION_PENDING": {"TARGET_OPEN", "SL_OPEN", "OCO_ACTIVE", "ERROR_MANUAL_ATTENTION"},
    "TARGET_OPEN": {"SL_OPEN", "OCO_ACTIVE", "ERROR_MANUAL_ATTENTION"},
    "SL_OPEN": {"TARGET_OPEN", "OCO_ACTIVE", "ERROR_MANUAL_ATTENTION"},
    "OCO_ACTIVE": {"POSITION_ACTIVE", "TARGET_FILLED", "SL_FILLED", "ERROR_MANUAL_ATTENTION"},
    "POSITION_ACTIVE": {"TARGET_FILLED", "SL_FILLED", "EXIT_RECONCILING", "ERROR_MANUAL_ATTENTION"},
    "TARGET_FILLED": {"EXIT_RECONCILING"},
    "SL_FILLED": {"EXIT_RECONCILING"},
    "EXIT_RECONCILING": {"CLOSED", "ERROR_MANUAL_ATTENTION"},
    "CANCELLED": set(),
    "CLOSED": set(),
    "ERROR_MANUAL_ATTENTION": {"EXIT_RECONCILING", "CLOSED"},
}


@dataclass
class OrderStateMachine:
    state: str = "SIGNAL_FOUND"
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.state not in ORDER_STATES:
            raise ValueError(f"Unknown order state: {self.state}")
        self.history.append({"timestamp": iso_now(), "from": "", "to": self.state, "reason": "initial"})

    def transition(self, new_state: str, reason: str = "", **context: Any) -> str:
        new_state = str(new_state).upper()
        if new_state not in ORDER_STATES:
            raise ValueError(f"Unknown order state: {new_state}")
        allowed = ALLOWED_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(f"Invalid Options Auto order transition {self.state} -> {new_state}.")
        previous = self.state
        self.state = new_state
        self.history.append({"timestamp": iso_now(), "from": previous, "to": new_state, "reason": reason, "context": context})
        return self.state

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "history": self.history}

