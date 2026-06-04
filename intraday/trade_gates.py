from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from .constants import SIDE_LONG, SIDE_SHORT


def event_blackout_blockers(settings, payload: dict[str, Any] | None = None, now: datetime | None = None) -> list[str]:
    if not bool(getattr(settings, "event_blackout_enabled", True)):
        return []
    payload = payload or {}
    now = now or datetime.now()
    blockers: list[str] = []
    if _truthy(payload.get("event_blackout_active") or payload.get("blackout_active")):
        reason = payload.get("event_blackout_reason") or payload.get("blackout_reason") or "Manual event blackout is active."
        blockers.append(f"Event blackout active: {reason}")
    windows = list(getattr(settings, "event_blackout_windows", []) or [])
    windows.extend(payload.get("event_blackouts") or payload.get("blackout_windows") or [])
    for window in windows:
        parsed = _parse_blackout_window(window, now)
        if not parsed:
            continue
        start, end, reason = parsed
        if start <= now <= end:
            blockers.append(f"Event blackout active: {reason}")
    return _dedupe(blockers)


def signal_eligibility_blockers(settings, signal, snapshot: dict[str, Any] | Any | None, instrument: dict[str, Any] | None = None) -> list[str]:
    side = str(getattr(signal, "side", "") or "").upper()
    if side not in {SIDE_LONG, SIDE_SHORT}:
        return []
    snapshot = snapshot or {}
    instrument = instrument or {}
    symbol = str(getattr(signal, "symbol", "") or "").upper()
    blockers: list[str] = []
    blocked_symbols = {str(value or "").upper() for value in getattr(settings, "blocked_symbols", []) or []}
    if symbol in blocked_symbols:
        blockers.append(f"{symbol} is blocked by stock eligibility settings.")
    if bool(getattr(settings, "require_mis_allowed", True)) and _instrument_false(instrument, "mis_allowed"):
        blockers.append(f"{symbol} is not marked MIS-eligible by the instrument source.")
    if bool(getattr(settings, "block_asm_gsm_t2t", True)) and _has_restricted_flag(instrument):
        blockers.append(f"{symbol} is blocked by ASM/GSM/T2T-style eligibility flags.")
    liquidity = _number(_get(snapshot, "liquidity_score"), 0.0)
    min_liquidity = float(getattr(settings, "min_liquidity_score", 35.0) or 0.0)
    if liquidity < min_liquidity:
        blockers.append(f"Liquidity score {liquidity:.1f} is below minimum {min_liquidity:.1f}.")
    spread_pct = _number(_get(snapshot, "spread_pct"), 0.0)
    max_spread = float(getattr(settings, "max_allowed_spread_pct", 0.35) or 0.0)
    if max_spread > 0 and spread_pct > max_spread:
        blockers.append(f"Spread {spread_pct:.3f}% is above maximum {max_spread:.3f}%.")
    trap_score = _number(_get(snapshot, "trap_score"), 0.0)
    max_trap = float(getattr(settings, "max_trap_score_for_entry", 80.0) or 100.0)
    if trap_score >= max_trap:
        blockers.append(f"Trap score {trap_score:.1f} is above maximum {max_trap:.1f}.")
    min_rvol = float(getattr(settings, "minimum_relative_volume_for_entry", 0.0) or 0.0)
    rvol = _number(_get(snapshot, "relative_volume"), 0.0)
    if min_rvol > 0 and rvol < min_rvol:
        blockers.append(f"Relative volume {rvol:.2f} is below minimum {min_rvol:.2f}.")
    return _dedupe(blockers)


def _parse_blackout_window(window: Any, now: datetime) -> tuple[datetime, datetime, str] | None:
    if not isinstance(window, dict):
        return None
    start = _parse_dt_or_time(window.get("start") or window.get("from") or window.get("start_time"), now)
    end = _parse_dt_or_time(window.get("end") or window.get("to") or window.get("end_time"), now)
    if not start or not end:
        return None
    if end < start:
        end = end + timedelta(days=1)
    reason = str(window.get("reason") or window.get("name") or window.get("event") or "Scheduled market event").strip()
    return start, end, reason


def _parse_dt_or_time(value: Any, now: datetime) -> datetime | None:
    if value in ("", None):
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt).time()
            return datetime.combine(now.date(), parsed)
        except ValueError:
            pass
    if isinstance(value, time):
        return datetime.combine(now.date(), value)
    return None


def _instrument_false(instrument: dict[str, Any], key: str) -> bool:
    if key not in instrument:
        return False
    value = instrument.get(key)
    if isinstance(value, bool):
        return not value
    return str(value).strip().lower() in {"0", "false", "no", "n", "blocked", "not_allowed"}


def _has_restricted_flag(instrument: dict[str, Any]) -> bool:
    text = " ".join(str(instrument.get(key, "")) for key in ("segment", "series", "category", "tags", "flags", "group"))
    text = text.upper()
    if any(flag in text.split() for flag in {"ASM", "GSM", "T2T"}):
        return True
    if any(bool(instrument.get(key)) for key in ("asm", "gsm", "t2t", "is_asm", "is_gsm", "is_t2t")):
        return True
    series = str(instrument.get("series") or "").upper()
    return series in {"BE", "BZ", "ST", "T"}


def _get(source: dict[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "active"}


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
