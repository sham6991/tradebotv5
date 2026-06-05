from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .candle_feed import candle_datetime, interval_minutes
from .constants import MODE_PAPER, MODE_REAL
from .data_source_policy import IntradayDataSource
from .stock_gap_backfiller import expected_missing_candles


def evaluate_stock_data_readiness(settings: Any, market_data: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    mode = str(getattr(settings, "mode", "") or "").upper()
    interval = getattr(settings, "candle_interval", "minute")
    blockers: list[str] = []
    warnings: list[str] = []
    rows = []
    for symbol, row in dict(market_data or {}).items():
        row = dict(row or {})
        candles = list(row.get("candles") or [])
        source = str(row.get("source") or row.get("data_source") or "").lower()
        source_status = str(row.get("source_status") or "OK").upper()
        last_candle = candle_datetime(candles[-1]) if candles else None
        missing = expected_missing_candles(candles, interval, now=now)
        stale = _is_stale(last_candle, interval, now) if source in {IntradayDataSource.ZERODHA_PAPER, IntradayDataSource.ZERODHA_REAL} else False
        symbol_blockers = []
        if source_status == "ERROR" or row.get("source_error"):
            symbol_blockers.append(row.get("source_error") or f"{symbol} data source is unavailable.")
        if not candles:
            symbol_blockers.append(f"{symbol} has no completed candles.")
        if mode in {MODE_PAPER, MODE_REAL} and source == IntradayDataSource.SIMULATED_FALLBACK and not getattr(settings, "allow_simulated_fallback", False):
            symbol_blockers.append(f"{symbol} simulated fallback is not allowed in live mode.")
        if stale:
            symbol_blockers.append(f"{symbol} live stock candles are stale.")
        if missing:
            warnings.append(f"{symbol} has {len(missing)} missing stock candle interval(s); backfill required before new entries.")
        blockers.extend(symbol_blockers)
        rows.append({
            "symbol": str(symbol).upper(),
            "source": source,
            "source_status": source_status,
            "candles_available": len(candles),
            "last_completed_candle": last_candle.isoformat(timespec="seconds") if last_candle else "",
            "missing_candles": len(missing),
            "stale": stale,
            "blockers": symbol_blockers,
        })
    return {
        "status": "BLOCKED" if blockers else "DEGRADED" if warnings else "OK",
        "new_entries_allowed": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "symbols": rows,
        "profile_used": getattr(settings, "strategy_profile", "BALANCED"),
        "profile_thresholds": getattr(settings, "profile_policy", {}),
    }


def _is_stale(last_candle: datetime | None, interval: str, now: datetime) -> bool:
    if not last_candle:
        return True
    allowed = timedelta(minutes=max(1, interval_minutes(interval)) * 2, seconds=30)
    return now - last_candle > allowed
