from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


LIVE_MODES = {"PAPER", "REAL"}
NON_LIVE_MODES = {"BACKTEST", "SHADOW", "DEBUG"}


@dataclass(frozen=True)
class QuoteFreshness:
    age_seconds: float | None
    known: bool
    max_age_seconds: float
    stale: bool
    unknown_blocks: bool

    @property
    def blockers(self) -> list[str]:
        if self.unknown_blocks and not self.known:
            return ["Quote age is unknown."]
        if self.stale:
            return ["Quote stale."]
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "age_seconds": self.age_seconds,
            "known": self.known,
            "max_age_seconds": self.max_age_seconds,
            "stale": self.stale,
            "unknown_blocks": self.unknown_blocks,
            "blockers": self.blockers,
        }


def evaluate_quote_freshness(
    quote: dict[str, Any] | None,
    settings: dict[str, Any] | None = None,
    *,
    now_epoch: float | None = None,
) -> QuoteFreshness:
    quote = dict(quote or {})
    settings = dict(settings or {})
    max_age = _number(settings.get("quote_stale_seconds"), _number(settings.get("max_quote_age_seconds"), 3.0))
    age = quote_age_seconds(quote, now_epoch=now_epoch)
    mode = str(settings.get("mode") or "").upper()
    explicit_unknown_policy = settings.get("unknown_quote_age_blocks_live_entries")
    live_mode = mode in LIVE_MODES
    allow_non_live = mode in NON_LIVE_MODES or bool(settings.get("allow_demo_data"))
    unknown_blocks = bool(
        age is None
        and live_mode
        and not allow_non_live
        and _bool(explicit_unknown_policy, True)
    )
    stale = bool(age is not None and age > max_age)
    return QuoteFreshness(
        age_seconds=round(float(age), 3) if age is not None else None,
        known=age is not None,
        max_age_seconds=max_age,
        stale=stale,
        unknown_blocks=unknown_blocks,
    )


def quote_age_seconds(quote: dict[str, Any] | None, *, now_epoch: float | None = None) -> float | None:
    quote = dict(quote or {})
    if quote.get("age_seconds") not in ("", None):
        age = _number(quote.get("age_seconds"), -1.0)
        return max(0.0, age) if age >= 0 else None
    now_epoch = _number(now_epoch, datetime.now().timestamp())
    for key in ("timestamp_epoch", "last_updated_epoch"):
        timestamp = quote.get(key)
        if timestamp in ("", None):
            continue
        return max(0.0, now_epoch - _number(timestamp, now_epoch))
    for key in ("timestamp", "last_trade_time", "exchange_timestamp"):
        when = _dt(quote.get(key))
        if when:
            return max(0.0, now_epoch - when.timestamp())
    return None


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


def _bool(value: Any, default: bool) -> bool:
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
