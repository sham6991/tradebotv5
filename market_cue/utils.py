from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
MARKET_CUE_DIR = os.path.join(DATA_DIR, "market_cue")
DB_PATH = os.path.join(MARKET_CUE_DIR, "market_cue.sqlite3")
INSTRUMENT_CACHE_PATH = os.path.join(MARKET_CUE_DIR, "kite_instruments_cache.json")


def ensure_market_cue_dir() -> None:
    os.makedirs(MARKET_CUE_DIR, exist_ok=True)


def now_ist_naive() -> datetime:
    return datetime.now()


def iso_now() -> str:
    return now_ist_naive().strftime("%Y-%m-%d %H:%M:%S")


def to_json(data: Any) -> str:
    return json.dumps(data, default=json_default, ensure_ascii=True)


def from_json(text: str | None, fallback: Any = None) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return fallback


def json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def safe_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").replace("INR", "").replace("Rs.", "").strip()
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def percent_change(last_price: Any, previous_close: Any) -> float | None:
    last = safe_float(last_price)
    previous = safe_float(previous_close)
    if last is None or previous in (None, 0):
        return None
    return ((last - previous) / previous) * 100


def round_to(value: float | None, step: int) -> int | None:
    if value is None:
        return None
    return int(round(float(value) / step) * step)


def normalize_status(value: str | None) -> str:
    text = str(value or "").strip().upper()
    return text if text in {"OK", "PARTIAL", "FAILED", "STALE", "UNAVAILABLE"} else "PARTIAL"


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text[:19] if "T" in fmt else text[:11], fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def fresh_date_string(value: Any) -> str | None:
    parsed = parse_datetime(value)
    if not parsed:
        return None
    return parsed.strftime("%Y-%m-%d")
