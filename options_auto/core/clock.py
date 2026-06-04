from __future__ import annotations

from datetime import datetime, timezone


def now_ist_naive() -> datetime:
    return datetime.now()


def iso_now() -> str:
    return now_ist_naive().isoformat(timespec="seconds")


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

