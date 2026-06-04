from __future__ import annotations

from datetime import datetime
from typing import Any

from .candle_feed import candle_datetime, full_candles, market_slice, max_candle_count
from .constants import SIDE_LONG, SIDE_SHORT


def run_candle_replay(manager, full_data: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    settings = manager.settings
    if settings is None:
        raise ValueError("Backtest session must be started before replay.")
    required_lookback = max(settings.ema50_period, settings.volume_lookback, settings.rsi_period, 5)
    total_candles = max_candle_count(full_data)
    timeline = []
    best_signals = []
    last_evaluated = None

    for cursor in range(max(0, required_lookback - 1), total_candles):
        replay_slice = market_slice(full_data, cursor, lookback=0)
        if len(replay_slice) != len(full_data):
            continue
        replay_time = _slice_time(replay_slice)
        last_evaluated = manager.evaluate({
            "market_data": replay_slice,
            "market_trend": payload.get("market_trend") or "Neutral",
            "replay_cursor": cursor,
            "replay_time": replay_time.isoformat(timespec="seconds") if replay_time else "",
        })
        signal = last_evaluated.get("last_signal") or last_evaluated.get("pending_signal")
        if signal and signal.get("side") in {SIDE_LONG, SIDE_SHORT}:
            best_signals.append({
                "cursor": cursor,
                "time": replay_time.isoformat(timespec="seconds") if replay_time else "",
                "symbol": signal.get("symbol"),
                "side": signal.get("side"),
                "score": signal.get("score"),
                "entry_price": signal.get("entry_price"),
                "stoploss": signal.get("stoploss"),
                "target": signal.get("target"),
                "risk_reward": signal.get("risk_reward"),
                "decision": signal.get("final_decision"),
                "blockers": signal.get("blockers") or [],
            })
        timeline.append({
            "cursor": cursor,
            "time": replay_time.isoformat(timespec="seconds") if replay_time else "",
            "snapshots": len(last_evaluated.get("snapshots") or []),
            "active_trade": bool(last_evaluated.get("active_trade")),
            "orders": len(last_evaluated.get("order_history") or []),
        })

    final_slice = market_slice(full_data, max(0, total_candles - 1), lookback=0)
    _close_open_trade_at_day_end(manager, final_slice)
    return {
        "evaluated": last_evaluated or manager.status_payload(),
        "timeline": timeline,
        "best_signals": sorted(best_signals, key=lambda row: float(row.get("score") or 0), reverse=True),
        "candle_count": total_candles,
        "symbols_replayed": len(full_data),
    }


def _slice_time(rows: dict[str, Any]) -> datetime | None:
    times = []
    for row in rows.values():
        candles = full_candles({"candles": row.get("candles") or []})
        if candles:
            parsed = candle_datetime(candles[-1])
            if parsed:
                times.append(parsed)
    return max(times) if times else None


def _close_open_trade_at_day_end(manager, final_slice: dict[str, Any]) -> None:
    lifecycle = getattr(manager, "lifecycle", None)
    trade = getattr(lifecycle, "active_trade", None) if lifecycle else None
    if not trade or trade.get("status") != "OPEN":
        return
    row = final_slice.get(trade.get("symbol")) or {}
    candles = row.get("candles") or []
    if not candles:
        return
    last = candles[-1]
    exit_price = float(last.get("close") or trade.get("entry_price") or 0)
    when = candle_datetime(last) or datetime.now()
    if hasattr(lifecycle, "close_active_trade"):
        lifecycle.close_active_trade("DAY_END", exit_price, when)
