from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


NO_CONTRACTS = "NO_CONTRACTS"
SELECTING_CONTRACTS = "SELECTING_CONTRACTS"
CONTRACTS_LOCKED = "CONTRACTS_LOCKED"
SCANNING_FOR_SETUP = "SCANNING_FOR_SETUP"
APPROVAL_PENDING = "APPROVAL_PENDING"
ENTRY_PENDING = "ENTRY_PENDING"
TRADE_ACTIVE = "TRADE_ACTIVE"
TRADE_EXITED = "TRADE_EXITED"
COOLDOWN = "COOLDOWN"
RESELECTING_CONTRACTS = "RESELECTING_CONTRACTS"
BLOCKED = "BLOCKED"


@dataclass
class LockedContractManager:
    state: str = NO_CONTRACTS
    lock: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    last_reason: str = ""
    cooldown_until: str = ""

    def begin_selection(self) -> None:
        self.state = SELECTING_CONTRACTS if not self.lock else RESELECTING_CONTRACTS

    def lock_contracts(self, lock: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(lock or {})
        snapshot.setdefault("lock_id", _lock_id())
        snapshot.setdefault("locked_at", datetime.now().isoformat(timespec="seconds"))
        snapshot.setdefault("status", CONTRACTS_LOCKED)
        self.lock = snapshot
        self.state = CONTRACTS_LOCKED
        self.history.append(snapshot)
        self.history = self.history[-20:]
        self.last_reason = ""
        return snapshot

    def current(self, underlying: str | None = None, expiry: Any = None) -> dict[str, Any] | None:
        if not self.lock:
            return None
        if underlying and str(self.lock.get("underlying") or "").upper() != str(underlying or "").upper():
            return None
        expiry_text = _expiry_text(expiry)
        if expiry_text and _expiry_text(self.lock.get("expiry")) != expiry_text:
            return None
        return dict(self.lock)

    def should_reselect(self, settings: dict[str, Any], active_trade: bool = False, now: datetime | None = None) -> bool:
        if not self.lock:
            return True
        if active_trade:
            return False
        now = now or datetime.now()
        if self.state in {TRADE_EXITED, COOLDOWN} and bool(settings.get("reselect_after_exit_cooldown", True)):
            cooldown_until = _parse_dt(self.cooldown_until or self.lock.get("cooldown_until"))
            return not cooldown_until or now >= cooldown_until
        if not bool(settings.get("lock_contracts_until_trade_or_timeout", True)):
            return True
        valid_until = _parse_dt(self.lock.get("valid_until"))
        if valid_until and now >= valid_until:
            return True
        return False

    def mark_scanning(self) -> None:
        if self.lock:
            self.state = SCANNING_FOR_SETUP

    def mark_trade_active(self) -> None:
        if self.lock:
            self.state = TRADE_ACTIVE
            self.lock["status"] = TRADE_ACTIVE

    def mark_trade_exited(self, cooldown_seconds: int | float | str = 0) -> None:
        if self.lock:
            cooldown = _cooldown_seconds(cooldown_seconds)
            exited_at = datetime.now()
            self.cooldown_until = (exited_at + timedelta(seconds=cooldown)).isoformat(timespec="seconds") if cooldown > 0 else ""
            self.state = COOLDOWN if cooldown > 0 else TRADE_EXITED
            self.lock["status"] = TRADE_EXITED
            self.lock["exited_at"] = exited_at.isoformat(timespec="seconds")
            self.lock["cooldown_until"] = self.cooldown_until

    def unlock(self, reason: str = "") -> None:
        self.last_reason = reason
        self.lock = None
        self.state = NO_CONTRACTS
        self.cooldown_until = ""

    def blocked(self, reason: str) -> None:
        self.last_reason = reason
        self.state = BLOCKED

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "lock": dict(self.lock or {}),
            "history": list(self.history),
            "last_reason": self.last_reason,
            "cooldown_until": self.cooldown_until,
        }


def build_valid_until(minutes: int | float | str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    try:
        value = max(1, int(float(minutes)))
    except (TypeError, ValueError):
        value = 60
    return (now + timedelta(minutes=value)).isoformat(timespec="seconds")


def _lock_id() -> str:
    return "OA_LOCK_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _expiry_text(value: Any) -> str:
    if value in ("", None):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _cooldown_seconds(value: Any) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0
