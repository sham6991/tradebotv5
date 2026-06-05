from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
from typing import Any

from candle_builder import CandleBuilder


class LiveIndexCandleStore:
    """Maintains live index candles for Options Auto Paper/Real sessions.

    Historical data is used only to backfill current-day gaps after reconnects.
    The latest in-progress candle is always built from live spot ticks/quotes.
    """

    def __init__(self, max_candles: int = 240):
        self.max_candles = max(20, int(max_candles or 240))
        self._states: dict[tuple[str, str, str], dict[str, Any]] = {}

    def update(
        self,
        *,
        client: Any | None,
        instrument_token: Any,
        underlying: str,
        mode: str,
        interval: str,
        spot: float,
        timestamp: Any = None,
        volume: Any = None,
    ) -> dict[str, Any]:
        interval_text = normalize_interval(interval)
        minutes = interval_minutes(interval_text)
        key = (str(mode or "").upper(), str(underlying or "").upper(), interval_text)
        state = self._states.get(key)
        if state is None:
            state = {
                "builder": CandleBuilder(minutes),
                "candles": [],
                "last_backfill_to": None,
                "last_backfill_error": "",
                "backfill_count": 0,
            }
            self._states[key] = state

        tick_time = _coerce_datetime(timestamp) or datetime.now()
        backfill = self._backfill_gap(state, client, instrument_token, interval_text, tick_time)
        builder: CandleBuilder = state["builder"]
        completed = builder.add_tick(key, spot, tick_time, volume=volume)
        if completed:
            state["candles"] = _merge_candles(state["candles"], [{**completed, "complete": True}], self.max_candles)
        active = builder.snapshot(key)
        live_active = [{**active, "complete": False}] if active else []
        candles = _merge_candles(state["candles"], live_active, self.max_candles)
        warnings = []
        if len(candles) < 3:
            warnings.append("Live candle warmup incomplete; waiting for more current-session candles.")
        if backfill.get("error"):
            warnings.append(f"Zerodha candle gap backfill will retry: {backfill['error']}")
        return {
            "candles": candles,
            "latest_candle": candles[-1] if candles else {},
            "interval": interval_text,
            "interval_minutes": minutes,
            "source": "zerodha_live_tick_candles",
            "backfill": backfill,
            "candle_count": len(candles),
            "warnings": warnings,
            "builder_stats": dict(builder.stats),
        }

    def snapshot(self) -> dict[str, Any]:
        rows = []
        for (mode, underlying, interval), state in self._states.items():
            rows.append({
                "mode": mode,
                "underlying": underlying,
                "interval": interval,
                "candle_count": len(state.get("candles") or []),
                "last_backfill_to": _iso(state.get("last_backfill_to")),
                "last_backfill_error": state.get("last_backfill_error") or "",
                "backfill_count": state.get("backfill_count") or 0,
            })
        return {"streams": rows}

    def stop(self) -> None:
        for state in self._states.values():
            builder = state.get("builder")
            if builder:
                builder.reset()

    def _backfill_gap(self, state: dict[str, Any], client: Any | None, instrument_token: Any, interval: str, tick_time: datetime) -> dict[str, Any]:
        if not client or instrument_token in ("", None):
            return {"attempted": False, "rows": 0, "error": ""}
        from_dt, to_dt = self._backfill_range(state, tick_time)
        if not from_dt or not to_dt or to_dt <= from_dt:
            return {"attempted": False, "rows": 0, "error": ""}
        try:
            rows = _historical_candles(client, instrument_token, from_dt, to_dt, interval)
            normalized = [_normalize_candle(row, tick_time.date()) for row in rows]
            normalized = [row for row in normalized if row]
            state["candles"] = _merge_candles(state["candles"], normalized, self.max_candles)
            state["last_backfill_to"] = to_dt
            state["last_backfill_error"] = ""
            state["backfill_count"] = int(state.get("backfill_count") or 0) + 1
            return {"attempted": True, "rows": len(normalized), "from": _iso(from_dt), "to": _iso(to_dt), "error": ""}
        except Exception as exc:
            state["last_backfill_error"] = str(exc)
            return {"attempted": True, "rows": 0, "from": _iso(from_dt), "to": _iso(to_dt), "error": str(exc)}

    def _backfill_range(self, state: dict[str, Any], tick_time: datetime) -> tuple[datetime | None, datetime | None]:
        last_to = state.get("last_backfill_to")
        candles = list(state.get("candles") or [])
        if candles:
            last_candle_time = _coerce_datetime(candles[-1].get("datetime"))
            from_dt = (last_candle_time or tick_time) - timedelta(minutes=1)
        elif last_to:
            from_dt = last_to
        else:
            from_dt = datetime.combine(tick_time.date(), dt_time(9, 15))
        to_dt = tick_time
        if to_dt.date() != date.today() and to_dt.date() != from_dt.date():
            return None, None
        return from_dt, to_dt


def normalize_interval(value: Any) -> str:
    text = str(value or "3minute").strip().lower().replace("_", "")
    if text in {"minute", "1minute", "1min"}:
        return "minute"
    if text.endswith("min") and text[:-3].isdigit():
        return f"{int(text[:-3])}minute"
    if text.endswith("minute") and text[:-6].isdigit():
        return f"{int(text[:-6])}minute"
    return "3minute"


def interval_minutes(value: Any) -> int:
    text = normalize_interval(value)
    if text == "minute":
        return 1
    number = text.replace("minute", "")
    try:
        return max(1, int(number))
    except (TypeError, ValueError):
        return 3


def _historical_candles(client: Any, instrument_token: Any, from_dt: datetime, to_dt: datetime, interval: str) -> list[dict[str, Any]]:
    if hasattr(client, "historical_candles"):
        rows = client.historical_candles(instrument_token, from_dt, to_dt, interval=interval)
    elif hasattr(client, "historical_data"):
        rows = client.historical_data(int(instrument_token), from_dt, to_dt, interval)
    else:
        kite = getattr(client, "kite", None)
        if not kite or not hasattr(kite, "historical_data"):
            raise AttributeError("Connected Zerodha client does not expose historical candles for gap fill.")
        rows = kite.historical_data(instrument_token=int(instrument_token), from_date=from_dt, to_date=to_dt, interval=interval)
    if hasattr(rows, "to_dict"):
        return list(rows.to_dict("records"))
    return list(rows or [])


def _normalize_candle(row: dict[str, Any], trade_day: date) -> dict[str, Any] | None:
    row = dict(row or {})
    when = _coerce_datetime(row.get("datetime") or row.get("date") or row.get("timestamp"))
    if not when or when.date() != trade_day:
        return None
    open_ = _number(row.get("open"))
    high = _number(row.get("high"))
    low = _number(row.get("low"))
    close = _number(row.get("close"))
    if min(open_, high, low, close) <= 0:
        return None
    return {
        "datetime": when,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": _number(row.get("volume")),
        "complete": True,
    }


def _merge_candles(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], max_candles: int) -> list[dict[str, Any]]:
    by_time: dict[str, dict[str, Any]] = {}
    for row in list(existing or []) + list(incoming or []):
        when = _coerce_datetime((row or {}).get("datetime"))
        if not when:
            continue
        normalized = dict(row)
        normalized["datetime"] = _iso(when)
        by_time[_iso(when)] = normalized
    rows = [by_time[key] for key in sorted(by_time)]
    return rows[-max_candles:]


def _coerce_datetime(value: Any) -> datetime | None:
    if value in ("", None):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _iso(value: Any) -> str:
    when = _coerce_datetime(value)
    return when.isoformat(timespec="seconds") if when else ""
