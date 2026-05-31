from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .utils import parse_datetime, safe_float


def validate_market_data(raw_data: dict[str, Any], manual_overrides: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    warnings: list[str] = []
    missing: list[str] = []
    stale: list[str] = []
    manual_overrides = manual_overrides or []

    indian = raw_data.get("indian_market") or {}
    global_data = raw_data.get("global_market") or {}
    flow = raw_data.get("institutional_flow") or {}

    for name in ("NIFTY 50", "BANK NIFTY"):
        row = indian.get(name) or {}
        if row.get("status") != "OK" and row.get("status") != "PARTIAL":
            missing.append(name)
        if safe_float(row.get("value")) in (None, 0):
            warnings.append(f"{name} LTP is missing or zero.")
        if safe_float(row.get("previous_close")) in (None, 0):
            warnings.append(f"{name} previous close is missing.")
        warnings.extend(_timestamp_warnings(name, row.get("timestamp"), hours=8))
        if row.get("ltp_source") == "historical_fallback":
            warnings.append(f"{name} is using historical fallback instead of live Zerodha LTP.")
        if _is_stale(row.get("timestamp"), hours=8) or row.get("stale"):
            stale.append(name)

    available_global = 0
    stale_global = 0
    for name, row in global_data.items():
        if row.get("status") in {"OK", "PARTIAL", "STALE"} and safe_float(row.get("value")) is not None:
            available_global += 1
        else:
            missing.append(name)
        warnings.extend(_timestamp_warnings(name, row.get("timestamp"), hours=24))
        if row.get("fetch_mode") == "history_fallback":
            warnings.append(f"{name} uses yfinance daily history fallback because fast_info was incomplete.")
        if row.get("cache_age_minutes") is not None:
            warnings.append(f"{name} uses stale cached data age {row.get('cache_age_minutes')} minutes.")
        if row.get("stale") or row.get("status") == "STALE" or _is_stale(row.get("timestamp"), hours=24):
            stale_global += 1

    if flow.get("status") not in {"OK", "PARTIAL"}:
        warnings.append("NSE FII/DII data is unavailable.")
    if flow.get("status") == "PARTIAL":
        warnings.append("NSE FII/DII parser returned a partial result.")
    if not flow.get("data_date"):
        warnings.append("NSE FII/DII data date is missing.")
    elif _older_than_expected_previous_trading_day(flow.get("data_date")):
        warnings.append("NSE FII/DII data is older than the expected previous trading day.")

    for item in manual_overrides:
        if item.get("applied") is False:
            warnings.append(f"Manual override ignored for unrecognized field {item.get('field_name')}.")
        else:
            warnings.append(f"Manual override used for {item.get('field_name')}.")

    reliability = "Good"
    if missing[:2] or available_global < max(8, int(len(global_data) * 0.55)) or flow.get("status") == "FAILED":
        reliability = "Poor"
    elif (
        missing
        or stale
        or stale_global > 2
        or flow.get("fetch_mode") in {"manual_entry", "manual_override"}
        or flow.get("status") == "PARTIAL"
        or any((indian.get(name) or {}).get("ltp_source") == "historical_fallback" for name in ("NIFTY 50", "BANK NIFTY"))
    ):
        reliability = "Partial"

    return {
        "warnings": warnings,
        "missing_values": missing,
        "stale_values": stale,
        "global_available_count": available_global,
        "global_total_count": len(global_data),
        "global_stale_count": stale_global,
        "manual_overrides": manual_overrides,
        "data_reliability": reliability,
    }


def _is_stale(value: Any, hours: int) -> bool:
    parsed = parse_datetime(value)
    if not parsed:
        return True
    if parsed > datetime.now() + timedelta(minutes=5):
        return True
    return datetime.now() - parsed > timedelta(hours=hours)


def _timestamp_warnings(name: str, value: Any, hours: int) -> list[str]:
    parsed = parse_datetime(value)
    if not parsed:
        return [f"{name} timestamp is missing or invalid."]
    if parsed > datetime.now() + timedelta(minutes=5):
        return [f"{name} timestamp is in the future."]
    if datetime.now() - parsed > timedelta(hours=hours):
        return [f"{name} timestamp is stale by more than {hours} hours."]
    return []


def _older_than_expected_previous_trading_day(value: Any) -> bool:
    parsed = parse_datetime(value)
    if not parsed:
        return True
    age_days = (datetime.now().date() - parsed.date()).days
    return age_days > 5
