from __future__ import annotations

from datetime import datetime
from typing import Any

from candle_builder import CandleBuilder
from options_auto.data.live_index_candles import interval_minutes, normalize_interval


class LiveOptionCandleStore:
    def __init__(self, max_candles: int = 240) -> None:
        self.max_candles = max(20, int(max_candles or 240))
        self._states: dict[tuple[str, str], dict[str, Any]] = {}

    def update(self, token: Any, tick: dict[str, Any], interval: str = "3minute") -> dict[str, Any]:
        token_key = str(token or tick.get("instrument_token") or tick.get("token") or tick.get("tradingsymbol") or "")
        if not token_key:
            return {"accepted": False, "reason": "Option token is missing.", "candles": []}
        interval_text = normalize_interval(interval)
        key = (token_key, interval_text)
        state = self._states.get(key)
        if state is None:
            state = {"builder": CandleBuilder(interval_minutes(interval_text)), "candles": [], "last_tick_at": ""}
            self._states[key] = state
        price = _price(tick)
        if price <= 0:
            return {"accepted": False, "reason": "Option tick price is missing.", "candles": list(state["candles"])}
        tick_time = _coerce_datetime(tick.get("timestamp") or tick.get("last_trade_time") or tick.get("exchange_timestamp")) or datetime.now()
        completed = state["builder"].add_tick(key, price, tick_time, volume=tick.get("volume") or tick.get("volume_traded") or tick.get("last_traded_quantity"))
        if completed:
            state["candles"] = _merge(state["candles"], [{**completed, "complete": True}], self.max_candles)
        active = state["builder"].snapshot(key)
        candles = _merge(state["candles"], [{**active, "complete": False}] if active else [], self.max_candles)
        state["last_tick_at"] = tick_time.isoformat(timespec="seconds")
        return {
            "accepted": True,
            "token": token_key,
            "interval": interval_text,
            "candles": candles,
            "latest_candle": candles[-1] if candles else {},
            "completed_candle": state["candles"][-1] if state["candles"] else {},
            "last_tick_at": state["last_tick_at"],
            "tick_count": int((active or {}).get("volume") or 0),
        }

    def candles(self, token: Any, interval: str = "3minute") -> list[dict[str, Any]]:
        state = self._states.get((str(token), normalize_interval(interval))) or {}
        return list(state.get("candles") or [])

    def snapshot(self) -> dict[str, Any]:
        streams = []
        for (token, interval), state in self._states.items():
            streams.append({
                "token": token,
                "interval": interval,
                "candle_count": len(state.get("candles") or []),
                "last_tick_at": state.get("last_tick_at") or "",
            })
        return {"streams": streams}


def _price(tick: dict[str, Any]) -> float:
    for key in ("last_price", "ltp", "price", "close"):
        try:
            value = float(tick.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return 0.0


def _merge(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], max_candles: int) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in list(existing or []) + list(incoming or []):
        when = _coerce_datetime((row or {}).get("datetime"))
        if not when:
            continue
        item = dict(row)
        item["datetime"] = when.isoformat(timespec="seconds")
        rows[item["datetime"]] = item
    return [rows[key] for key in sorted(rows)][-max_candles:]


def _coerce_datetime(value: Any) -> datetime | None:
    if value in ("", None):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None
