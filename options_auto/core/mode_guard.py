from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from options_auto.constants import (
    MODE_BACKTEST,
    MODE_PAPER,
    MODE_REAL,
    MODE_SHADOW,
    REAL_EXECUTION_DISABLED_REASON,
    SUPPORTED_MODES,
)
from options_auto.core.clock import iso_now


def normalize_mode(value: Any) -> str:
    mode = str(value or MODE_PAPER).strip().upper().replace(" ", "_")
    aliases = {
        "LIVE": MODE_REAL,
        "REAL_MONEY": MODE_REAL,
        "REAL_TRADING": MODE_REAL,
        "PAPER_TRADING": MODE_PAPER,
        "VIRTUAL": MODE_PAPER,
        "DRY_RUN": MODE_SHADOW,
    }
    mode = aliases.get(mode, mode)
    if mode not in SUPPORTED_MODES:
        raise ValueError("Options Auto mode must be BACKTEST, SHADOW, PAPER, or REAL.")
    return mode


@dataclass
class ModeDecision:
    action: str
    allowed: bool
    reason: str
    mode: str
    timestamp: str = field(default_factory=iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "allowed": self.allowed,
            "reason": self.reason,
            "mode": self.mode,
            "timestamp": self.timestamp,
        }


@dataclass
class ModeGuard:
    mode: str = MODE_PAPER
    session_id: str = field(default_factory=lambda: f"OA-{uuid4().hex[:12].upper()}")
    user_login_time: str = field(default_factory=iso_now)
    kite_profile: dict[str, Any] = field(default_factory=dict)
    real_mode_confirmed: bool = False
    real_orders_enabled: bool = False
    audit_log: list[ModeDecision] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.mode = normalize_mode(self.mode)

    def _record(self, action: str, allowed: bool, reason: str) -> bool:
        self.audit_log.append(ModeDecision(action=action, allowed=allowed, reason=reason, mode=self.mode))
        return allowed

    def can_backtest(self) -> bool:
        return self._record("can_backtest", True, "Backtest is non-order mode.")

    def can_shadow(self) -> bool:
        return self._record("can_shadow", True, "Shadow mode never places broker orders.")

    def can_paper_trade(self) -> bool:
        allowed = self.mode == MODE_PAPER
        return self._record(
            "can_paper_trade",
            allowed,
            "Paper mode is active." if allowed else "Paper trading is isolated from real/backtest mode.",
        )

    def can_real_trade(self) -> bool:
        allowed = self.mode == MODE_REAL and self.real_mode_confirmed
        if self.mode != MODE_REAL:
            reason = "Real trading requires Real Money mode."
        elif not self.real_mode_confirmed:
            reason = "Real trading requires explicit real-mode confirmation."
        else:
            reason = "Real mode is confirmed."
        return self._record("can_real_trade", allowed, reason)

    def assert_paper_allowed(self) -> None:
        if not self.can_paper_trade():
            raise PermissionError(self.audit_log[-1].reason)

    def assert_real_allowed(self) -> None:
        if not self.can_real_trade():
            raise PermissionError(self.audit_log[-1].reason)

    def assert_no_real_order_in_paper(self) -> None:
        if self.mode == MODE_PAPER:
            self._record("assert_no_real_order_in_paper", False, "Paper mode cannot call real order APIs.")
            raise PermissionError("Paper mode cannot call real order APIs.")
        self._record("assert_no_real_order_in_paper", True, "Mode is not paper.")

    def assert_real_order_allowed(self) -> None:
        self.assert_real_allowed()
        if not self.real_orders_enabled:
            self._record("assert_real_order_allowed", False, REAL_EXECUTION_DISABLED_REASON)
            raise PermissionError(REAL_EXECUTION_DISABLED_REASON)
        self._record("assert_real_order_allowed", True, "Real order execution is explicitly enabled.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "session_id": self.session_id,
            "user_login_time": self.user_login_time,
            "kite_user_id": self.kite_profile.get("user_id") or self.kite_profile.get("client_id") or "",
            "can_backtest": self.mode in {MODE_BACKTEST, MODE_PAPER, MODE_REAL, MODE_SHADOW},
            "can_shadow": True,
            "can_paper_trade": self.mode == MODE_PAPER,
            "can_real_trade": self.mode == MODE_REAL and self.real_mode_confirmed,
            "real_orders_enabled": self.real_orders_enabled,
            "audit_log": [item.to_dict() for item in self.audit_log[-100:]],
        }

