from __future__ import annotations

from dataclasses import dataclass

from .constants import (
    MODE_BACKTEST,
    MODE_PAPER,
    MODE_REAL,
    MODE_REPLAY,
    MODE_STATE_BACKTEST_ACTIVE,
    MODE_STATE_ERROR_LOCKED,
    MODE_STATE_NO_SESSION,
    MODE_STATE_PAPER_LOGGED_IN,
    MODE_STATE_PAPER_SESSION_RUNNING,
    MODE_STATE_REAL_LOGGED_IN,
    MODE_STATE_REAL_SESSION_RUNNING,
    MODE_STATE_SESSION_LOCKED,
)


@dataclass(frozen=True)
class ModePermissions:
    paper_trade_allowed: bool = False
    real_trade_allowed: bool = False
    backtest_allowed: bool = False
    simulated_order_allowed: bool = False

    def to_dict(self) -> dict:
        return {
            "paper_trade_allowed": self.paper_trade_allowed,
            "real_trade_allowed": self.real_trade_allowed,
            "backtest_allowed": self.backtest_allowed,
            "simulated_order_allowed": self.simulated_order_allowed,
        }


class SessionModeManager:
    """Central mode gate for paper, real, and backtest/replay isolation."""

    def __init__(self) -> None:
        self.state = MODE_STATE_NO_SESSION
        self.active_mode = ""
        self.lock_reason = ""

    def set_login(self, mode: str) -> None:
        mode = self._normalize_mode(mode)
        if mode in {MODE_BACKTEST, MODE_REPLAY}:
            self.state = MODE_STATE_BACKTEST_ACTIVE
            self.active_mode = mode
        elif mode == MODE_REAL:
            self.state = MODE_STATE_REAL_LOGGED_IN
            self.active_mode = MODE_REAL
        elif mode == MODE_PAPER:
            self.state = MODE_STATE_PAPER_LOGGED_IN
            self.active_mode = MODE_PAPER
        else:
            self.error_lock(f"Unknown session mode: {mode}")
            return
        self.lock_reason = ""

    def start_session(self, mode: str) -> None:
        mode = self._normalize_mode(mode)
        self.set_login(mode)
        if mode == MODE_REAL:
            self.state = MODE_STATE_REAL_SESSION_RUNNING
        elif mode == MODE_PAPER:
            self.state = MODE_STATE_PAPER_SESSION_RUNNING
        elif mode in {MODE_BACKTEST, MODE_REPLAY}:
            self.state = MODE_STATE_BACKTEST_ACTIVE
        else:
            self.error_lock(f"Unknown session mode: {mode}")
        self.active_mode = mode

    def lock_session(self, reason: str = "Session settings are locked.") -> None:
        self.state = MODE_STATE_SESSION_LOCKED
        self.lock_reason = reason

    def stop(self) -> None:
        self.state = MODE_STATE_NO_SESSION
        self.active_mode = ""
        self.lock_reason = ""

    def error_lock(self, reason: str) -> None:
        self.state = MODE_STATE_ERROR_LOCKED
        self.lock_reason = reason

    def permissions(self) -> ModePermissions:
        if self.state in {MODE_STATE_ERROR_LOCKED, MODE_STATE_NO_SESSION, MODE_STATE_SESSION_LOCKED}:
            return ModePermissions()
        if self.state in {MODE_STATE_PAPER_LOGGED_IN, MODE_STATE_PAPER_SESSION_RUNNING}:
            return ModePermissions(
                paper_trade_allowed=True,
                real_trade_allowed=False,
                backtest_allowed=True,
                simulated_order_allowed=True,
            )
        if self.state in {MODE_STATE_REAL_LOGGED_IN, MODE_STATE_REAL_SESSION_RUNNING}:
            return ModePermissions(
                paper_trade_allowed=False,
                real_trade_allowed=True,
                backtest_allowed=True,
                simulated_order_allowed=False,
            )
        if self.state == MODE_STATE_BACKTEST_ACTIVE:
            return ModePermissions(
                paper_trade_allowed=False,
                real_trade_allowed=False,
                backtest_allowed=True,
                simulated_order_allowed=True,
            )
        return ModePermissions()

    def blocker_for(self, requested_mode: str) -> str:
        requested_mode = self._normalize_mode(requested_mode)
        if self.state == MODE_STATE_ERROR_LOCKED:
            return self.lock_reason or "Mode state is unclear; trading is blocked."
        if requested_mode in {MODE_BACKTEST, MODE_REPLAY}:
            return ""
        if self.active_mode and self.active_mode != requested_mode:
            return (
                "Mode blocked for safety. Current active login/session is: "
                f"{self.active_mode}. Please stop/logout current session before switching."
            )
        return ""

    def assert_order_allowed(self, mode: str) -> None:
        mode = self._normalize_mode(mode)
        permissions = self.permissions()
        if mode == MODE_PAPER and permissions.paper_trade_allowed:
            return
        if mode == MODE_REAL and permissions.real_trade_allowed:
            return
        if mode in {MODE_BACKTEST, MODE_REPLAY} and permissions.simulated_order_allowed:
            return
        raise ValueError(
            "Mode blocked for safety. Current active login/session is: "
            f"{self.active_mode or self.state}. Please stop/logout current session before switching."
        )

    def to_dict(self) -> dict:
        permissions = self.permissions()
        return {
            "state": self.state,
            "active_mode": self.active_mode,
            "lock_reason": self.lock_reason,
            "permissions": permissions.to_dict(),
            "banner": self.banner(),
        }

    def banner(self) -> str:
        if self.active_mode == MODE_REAL:
            return "REAL MONEY MODE ACTIVE"
        if self.active_mode == MODE_PAPER:
            return "PAPER MODE ACTIVE"
        if self.active_mode in {MODE_BACKTEST, MODE_REPLAY}:
            return "BACKTEST / REPLAY ONLY - NO LIVE ORDERS"
        if self.state == MODE_STATE_ERROR_LOCKED:
            return "MODE ERROR LOCKED - TRADING BLOCKED"
        return "NO INTRADAY SESSION"

    def _normalize_mode(self, mode: str) -> str:
        value = str(mode or "").strip().upper().replace(" ", "_").replace("/", "_")
        if value in {"LIVE", "REAL_MONEY", "REAL_TRADING", "INTRADAY_STOCKS_REAL"}:
            return MODE_REAL
        if value in {"PAPER_TRADING", "INTRADAY_STOCKS_PAPER"}:
            return MODE_PAPER
        if value in {"BACKTEST_REPLAY", "PAPER_BACKTEST", "BACKTEST"}:
            return MODE_BACKTEST
        if value == "REPLAY":
            return MODE_REPLAY
        return value
