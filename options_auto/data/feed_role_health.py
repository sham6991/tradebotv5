from __future__ import annotations

from datetime import datetime
from typing import Any


DEFAULT_EXPECTED_ROLES = ("INDEX", "CE", "PE")


def evaluate_expected_roles(
    last_ticks: dict[str, Any],
    settings: dict[str, Any] | None = None,
    *,
    expected_roles: list[str] | tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = dict(settings or {})
    now = now or datetime.now()
    max_age = _number(settings.get("max_tick_age_seconds"), _number(settings.get("max_quote_age_seconds"), 3.0))
    observed = {str(role or "").upper(): timestamp for role, timestamp in dict(last_ticks or {}).items()}
    roles = _roles(expected_roles, observed)
    role_statuses: dict[str, dict[str, Any]] = {}
    missing_roles: list[str] = []
    stale_roles: list[str] = []
    fresh_roles: list[str] = []

    for role in roles:
        timestamp = observed.get(role)
        when = _dt(timestamp)
        age = (now - when).total_seconds() if when else None
        missing = not timestamp
        stale = bool(age is not None and age > max_age)
        fresh = bool(age is not None and age <= max_age)
        if missing:
            missing_roles.append(role)
        if stale:
            stale_roles.append(role)
        if fresh:
            fresh_roles.append(role)
        role_statuses[role] = {
            "last_tick": timestamp or "",
            "age_seconds": round(age, 3) if age is not None else None,
            "fresh": fresh,
            "stale": stale,
            "missing": missing,
        }

    return {
        "role_statuses": role_statuses,
        "missing_roles": missing_roles,
        "stale_labels": stale_roles,
        "fresh_roles": fresh_roles,
        "all_expected_roles_fresh": bool(roles) and not missing_roles and not stale_roles,
        "expected_roles": roles,
        "max_age_seconds": max_age,
    }


def _roles(expected_roles: list[str] | tuple[str, ...] | None, observed: dict[str, Any]) -> list[str]:
    roles = [str(role or "").upper() for role in (expected_roles or []) if str(role or "").strip()]
    if not roles:
        roles = list(DEFAULT_EXPECTED_ROLES) if any(role in observed for role in DEFAULT_EXPECTED_ROLES) else sorted(observed)
    return list(dict.fromkeys(roles))


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
