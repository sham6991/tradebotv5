from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.core.clock import iso_now
from options_auto.core.mode_guard import ModeGuard


@dataclass
class OptionsAutoSessionState:
    mode_guard: ModeGuard
    status: str = "IDLE"
    started_at: str = field(default_factory=iso_now)
    last_decision: dict[str, Any] = field(default_factory=dict)
    active_trades: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    decision_log: list[dict[str, Any]] = field(default_factory=list)
    rejected_log: list[dict[str, Any]] = field(default_factory=list)
    safety_events: list[dict[str, Any]] = field(default_factory=list)

    def record_decision(self, decision: dict[str, Any]) -> None:
        self.last_decision = dict(decision)
        self.decision_log.append(dict(decision))

    def record_rejection(self, reason: str, context: dict[str, Any] | None = None) -> None:
        self.rejected_log.append({"timestamp": iso_now(), "reason": reason, "context": dict(context or {})})

    def record_safety_event(self, reason: str, context: dict[str, Any] | None = None) -> None:
        self.safety_events.append({"timestamp": iso_now(), "reason": reason, "context": dict(context or {})})

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at,
            "mode_guard": self.mode_guard.to_dict(),
            "last_decision": self.last_decision,
            "active_trades": self.active_trades,
            "orders": self.orders[-100:],
            "decision_log": self.decision_log[-100:],
            "rejected_log": self.rejected_log[-100:],
            "safety_events": self.safety_events[-100:],
        }

