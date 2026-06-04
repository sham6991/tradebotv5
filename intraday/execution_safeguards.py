from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any

from .candle_feed import candle_datetime, interval_minutes
from .constants import MODE_REAL
from .order_request import OrderRequest


TERMINAL_BROKER_STATUSES = {"COMPLETE", "CANCELLED", "REJECTED"}
LIVE_DATA_SOURCES = {"real_zerodha_live", "zerodha_live", "zerodha_quote", "kite_quote"}


def tick_size_from_instrument(instrument: dict[str, Any] | None) -> float:
    try:
        tick = float((instrument or {}).get("tick_size") or 0.05)
    except (TypeError, ValueError):
        tick = 0.05
    return tick if tick > 0 else 0.05


def round_price_to_tick(value: float | int | str | None, tick_size: float | int | str | None) -> float:
    return float(_round_decimal(value, tick_size, ROUND_HALF_UP))


def floor_price_to_tick(value: float | int | str | None, tick_size: float | int | str | None) -> float:
    return float(_round_decimal(value, tick_size, ROUND_FLOOR))


def ceil_price_to_tick(value: float | int | str | None, tick_size: float | int | str | None) -> float:
    return float(_round_decimal(value, tick_size, ROUND_CEILING))


def normalize_order_request_prices(request: OrderRequest, tick_size: float | int | str | None) -> OrderRequest:
    tick = _tick_decimal(tick_size)
    price = request.price
    trigger = request.trigger_price
    if price not in ("", None):
        price = round_price_to_tick(price, tick)
    if trigger not in ("", None):
        trigger = round_price_to_tick(trigger, tick)
    if request.order_type == "SL" and price not in ("", None) and trigger not in ("", None):
        trigger_value = float(trigger)
        price_value = float(price)
        tick_value = float(tick)
        if request.transaction_type == "SELL" and trigger_value - price_value < tick_value:
            price = floor_price_to_tick(trigger_value - tick_value, tick)
        elif request.transaction_type == "BUY" and price_value - trigger_value < tick_value:
            price = ceil_price_to_tick(trigger_value + tick_value, tick)
    return replace(request, price=price, trigger_price=trigger)


def validate_stoploss_limit_relationship(
    request: OrderRequest,
    tick_size: float | int | str | None,
    min_buffer_ticks: int = 1,
) -> list[str]:
    if request.order_type != "SL":
        return []
    blockers = []
    tick = float(_tick_decimal(tick_size))
    min_gap = max(1, int(min_buffer_ticks or 1)) * tick
    try:
        price = float(request.price)
        trigger = float(request.trigger_price)
    except (TypeError, ValueError):
        return ["SL-LIMIT order requires both trigger and limit price."]
    tolerance = tick / 1000.0
    if request.transaction_type == "SELL" and trigger - price + tolerance < min_gap:
        blockers.append("SELL SL-LIMIT requires limit price below trigger by at least one tick.")
    if request.transaction_type == "BUY" and price - trigger + tolerance < min_gap:
        blockers.append("BUY SL-LIMIT requires limit price above trigger by at least one tick.")
    return blockers


def order_fingerprint(request: OrderRequest) -> dict[str, Any]:
    params = request.to_kite_params()
    return {
        "tag": str(params.get("tag") or ""),
        "exchange": str(params.get("exchange") or "").upper(),
        "tradingsymbol": str(params.get("tradingsymbol") or "").upper(),
        "transaction_type": str(params.get("transaction_type") or "").upper(),
        "product": str(params.get("product") or "").upper(),
        "order_type": str(params.get("order_type") or "").upper(),
        "quantity": int(params.get("quantity") or 0),
        "price": _optional_float(params.get("price")),
        "trigger_price": _optional_float(params.get("trigger_price")),
    }


def matching_broker_order(request: OrderRequest, orders: list[dict[str, Any]], tick_size: float | int | str | None) -> dict[str, Any] | None:
    expected = order_fingerprint(request)
    tick = float(_tick_decimal(tick_size))
    for order in orders or []:
        status = str(order.get("status") or "").upper()
        if status in TERMINAL_BROKER_STATUSES:
            continue
        if str(order.get("tag") or "").upper() != expected["tag"].upper():
            continue
        if str(order.get("exchange") or "").upper() != expected["exchange"]:
            continue
        if str(order.get("tradingsymbol") or "").upper() != expected["tradingsymbol"]:
            continue
        if str(order.get("transaction_type") or "").upper() != expected["transaction_type"]:
            continue
        if str(order.get("product") or "").upper() != expected["product"]:
            continue
        if str(order.get("order_type") or "").upper() != expected["order_type"]:
            continue
        if int(float(order.get("quantity") or 0)) != expected["quantity"]:
            continue
        if not _same_price(_optional_float(order.get("price")), expected["price"], tick):
            continue
        if not _same_price(_optional_float(order.get("trigger_price")), expected["trigger_price"], tick):
            continue
        return dict(order)
    return None


def real_execution_blockers(
    signal,
    market_row: dict[str, Any] | None,
    settings,
    broker=None,
    now: datetime | None = None,
) -> list[str]:
    if getattr(settings, "mode", "") != MODE_REAL:
        return []
    now = now or datetime.now()
    row = market_row or {}
    blockers: list[str] = []
    blockers.extend(_api_health_blockers(broker))
    blockers.extend(_source_blockers(row))
    blockers.extend(_freshness_blockers(row, settings, now))
    blockers.extend(_circuit_blockers(signal, row))
    return _dedupe(blockers)


def _api_health_blockers(broker) -> list[str]:
    if broker is None or not hasattr(broker, "api_health_blockers"):
        return []
    try:
        return list(broker.api_health_blockers() or [])
    except Exception:
        return ["Broker API health status is unavailable; real orders are paused."]


def _source_blockers(row: dict[str, Any]) -> list[str]:
    source = str(row.get("source") or "").strip().lower()
    if not source:
        return ["Real order blocked: live Zerodha data source is missing."]
    if source in {"provided", "simulated"} or "simulated" in source or "provided" in source:
        return ["Real order blocked: simulated/provided data cannot drive real orders."]
    if "zerodha" not in source and source not in LIVE_DATA_SOURCES:
        return ["Real order blocked: data source is not a live Zerodha source."]
    if row.get("quote_error"):
        return [f"Real order blocked: live quote/depth fetch failed ({row.get('quote_error')})."]
    return []


def _freshness_blockers(row: dict[str, Any], settings, now: datetime) -> list[str]:
    blockers = []
    candle_time = _row_time(row, "last_candle_time") or _latest_candle_time(row)
    if not candle_time:
        blockers.append("Real order blocked: latest candle timestamp is unavailable.")
    else:
        max_candle_age = max(180, interval_minutes(getattr(settings, "candle_interval", "minute")) * 120 + 30)
        if now - candle_time > timedelta(seconds=max_candle_age):
            blockers.append("Real order blocked: latest candle is stale.")

    tick_time = _row_time(row, "last_tick_time") or _row_time(row, "quote_timestamp")
    depth = row.get("depth") if isinstance(row.get("depth"), dict) else {}
    has_depth = bool(depth.get("buy") and depth.get("sell"))
    if not has_depth:
        blockers.append("Real order blocked: live market depth is unavailable.")
    if str(row.get("depth_source") or "").lower() != "zerodha_quote":
        blockers.append("Real order blocked: depth must come from a live Zerodha quote.")
    if not tick_time:
        blockers.append("Real order blocked: live tick/depth timestamp is unavailable.")
    elif now - tick_time > timedelta(seconds=20):
        blockers.append("Real order blocked: live tick/depth data is stale.")
    return blockers


def _circuit_blockers(signal, row: dict[str, Any]) -> list[str]:
    blockers = []
    lower = _first_float(row, "lower_circuit_limit", "lowerCircuitLimit", "lower_price_band")
    upper = _first_float(row, "upper_circuit_limit", "upperCircuitLimit", "upper_price_band")
    planned = [
        ("entry", getattr(signal, "entry_price", 0.0)),
        ("stoploss", getattr(signal, "stoploss", 0.0)),
        ("target", getattr(signal, "target", 0.0)),
    ]
    if lower and upper:
        for label, value in planned:
            price = float(value or 0.0)
            if price <= 0:
                continue
            if price <= lower or price >= upper:
                blockers.append(f"Real order blocked: {label} price is outside exchange circuit limits.")
                continue
            if _near_band(price, lower, upper):
                blockers.append(f"Real order blocked: {label} price is too close to circuit/price-band limits.")

    ltp = float(row.get("ltp") or getattr(signal, "entry_price", 0.0) or 0.0)
    day_open = _day_open(row)
    if ltp and day_open:
        move_pct = abs(ltp - day_open) / max(abs(day_open), 0.01) * 100.0
        if move_pct >= 7.5:
            blockers.append("Real order blocked: stock is moving abnormally versus day open.")
    return blockers


def _day_open(row: dict[str, Any]) -> float:
    ohlc = row.get("ohlc") if isinstance(row.get("ohlc"), dict) else {}
    value = _optional_float(row.get("day_open") or row.get("open") or ohlc.get("open"))
    if value:
        return value
    candles = row.get("full_candles") or row.get("candles") or []
    if candles:
        return _optional_float(candles[0].get("open")) or 0.0
    return 0.0


def _near_band(price: float, lower: float, upper: float) -> bool:
    band = max(upper - lower, 0.01)
    min_distance = max(0.20, band * 0.01)
    return (price - lower) <= min_distance or (upper - price) <= min_distance


def _row_time(row: dict[str, Any], key: str) -> datetime | None:
    value = row.get(key)
    if hasattr(value, "isoformat"):
        return value.replace(tzinfo=None) if hasattr(value, "tzinfo") else value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _latest_candle_time(row: dict[str, Any]) -> datetime | None:
    candles = row.get("candles") or []
    if not candles:
        return None
    return candle_datetime(candles[-1])


def _first_float(row: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = _optional_float(row.get(key))
        if value:
            return value
    return 0.0


def _optional_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same_price(left: float | None, right: float | None, tick: float) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) <= max(float(tick), 0.01) / 2.0


def _round_decimal(value: float | int | str | None, tick_size: float | int | str | None, rounding) -> Decimal:
    if value in ("", None):
        return Decimal("0")
    tick = _tick_decimal(tick_size)
    raw = Decimal(str(value))
    units = (raw / tick).quantize(Decimal("1"), rounding=rounding)
    quantized = units * tick
    return quantized.quantize(tick)


def _tick_decimal(tick_size: float | int | str | None) -> Decimal:
    try:
        tick = Decimal(str(tick_size or "0.05"))
    except Exception:
        tick = Decimal("0.05")
    return tick if tick > 0 else Decimal("0.05")


def _dedupe(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
