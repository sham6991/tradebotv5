from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


ACTIVE_PROTECTIVE_STATUSES = {"OPEN", "TRIGGER PENDING", "VALIDATION PENDING"}
TERMINAL_PROTECTED_STATES = {"PROTECTIVE_EXIT_ACTIVE", "PROTECTIVE_EXIT_FAILED", "FLAT", "FLAT_CONFIRMED"}


@dataclass(frozen=True)
class ProtectionSla:
    elapsed_seconds: float | None
    sla_seconds: float
    target_confirmed: bool
    stoploss_confirmed: bool
    breached: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 3) if self.elapsed_seconds is not None else None,
            "sla_seconds": self.sla_seconds,
            "target_confirmed": self.target_confirmed,
            "stoploss_confirmed": self.stoploss_confirmed,
            "breached": self.breached,
            "reason": self.reason,
        }


def evaluate_protection_sla(
    fill: dict[str, Any] | None,
    target_order: dict[str, Any] | None,
    stoploss_order: dict[str, Any] | None,
    protected_state: str,
    settings: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> ProtectionSla:
    settings = dict(settings or {})
    fill = dict(fill or {})
    target_order = dict(target_order or {})
    stoploss_order = dict(stoploss_order or {})
    sla_seconds = max(0.0, _number(settings.get("protection_confirm_sla_seconds"), 5.0))
    if not fill or _number(fill.get("filled_quantity")) <= 0 or sla_seconds <= 0:
        return ProtectionSla(None, sla_seconds, False, False, False)
    if str(protected_state or "").upper() in TERMINAL_PROTECTED_STATES:
        return ProtectionSla(None, sla_seconds, _confirmed(target_order), _confirmed(stoploss_order), False)

    started = _dt(
        fill.get("protection_started_at")
        or stoploss_order.get("submitted_at")
        or target_order.get("submitted_at")
        or fill.get("filled_at")
    )
    if not started:
        return ProtectionSla(None, sla_seconds, _confirmed(target_order), _confirmed(stoploss_order), False)

    elapsed = ((now or datetime.now()) - started).total_seconds()
    target_confirmed = _confirmed(target_order)
    stoploss_confirmed = _confirmed(stoploss_order)
    breached = bool(elapsed > sla_seconds and not stoploss_confirmed)
    reason = ""
    if breached:
        reason = f"Protective stoploss was not broker-confirmed within {sla_seconds:g}s after entry fill."
    return ProtectionSla(elapsed, sla_seconds, target_confirmed, stoploss_confirmed, breached, reason)


def _confirmed(order: dict[str, Any]) -> bool:
    status = str((order or {}).get("status") or "").upper()
    return bool((order or {}).get("order_id") and status in ACTIVE_PROTECTIVE_STATUSES)


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
