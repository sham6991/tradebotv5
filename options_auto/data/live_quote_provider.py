from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from options_auto.data.market_depth_controller import MarketDepthController
from options_auto.indicators.technicals import bid_ask_spread_pct, market_depth_imbalance


class LiveQuoteProvider:
    def __init__(self, provider: Any | None = None):
        self.provider = provider
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.depth_controller = MarketDepthController()

    def update(self, symbol: str, quote: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.normalize_quote(symbol, quote)
        self.snapshots[str(symbol)] = snapshot
        return snapshot

    def quote(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        raw = {}
        if self.provider and hasattr(self.provider, "quote"):
            raw = self.provider.quote(symbols)
        return {symbol: self.update(symbol, raw.get(symbol, {})) for symbol in symbols}

    def normalize_quote(self, symbol: str, quote: dict[str, Any]) -> dict[str, Any]:
        quote = dict(quote or {})
        quote_key = str(quote.get("quote_key") or symbol or "").upper()
        exchange, tradingsymbol = _split_quote_key(quote_key)
        exchange = str(quote.get("exchange") or exchange or "").upper()
        tradingsymbol = str(quote.get("tradingsymbol") or quote.get("symbol") or tradingsymbol or "").upper()
        ltp = quote.get("last_price") or quote.get("ltp") or 0
        bid = quote.get("bid") or quote.get("best_bid") or _depth_price(quote, "buy")
        ask = quote.get("ask") or quote.get("best_ask") or _depth_price(quote, "sell")
        bid_qty = quote.get("bid_qty") or _depth_qty(quote, "buy")
        ask_qty = quote.get("ask_qty") or _depth_qty(quote, "sell")
        total_buy = quote.get("total_buy_quantity") or quote.get("buy_quantity") or bid_qty
        total_sell = quote.get("total_sell_quantity") or quote.get("sell_quantity") or ask_qty
        oi = quote.get("oi")
        if oi in ("", None):
            oi = quote.get("open_interest") or quote.get("openInterest")
        timestamp, timestamp_source = _quote_timestamp(quote)
        timestamp_text = _timestamp_text(timestamp)
        received_epoch = _float(quote.get("received_epoch"), datetime.now().timestamp())
        received_at = _timestamp_text(quote.get("received_at")) or datetime.fromtimestamp(received_epoch).isoformat(timespec="seconds")
        timestamp_epoch = _timestamp_epoch(timestamp)
        source = str(quote.get("quote_source") or quote.get("source") or quote.get("data_source") or "kite_quote")
        websocket_source = "websocket" in source.lower() or bool(quote.get("websocket"))
        if timestamp_epoch <= 0 and websocket_source:
            timestamp_epoch = received_epoch
            timestamp_text = received_at
            timestamp_source = "local_received_at"
        age_known = timestamp_epoch > 0
        age_seconds = quote.get("age_seconds")
        if age_seconds in ("", None):
            age_seconds = max(0.0, datetime.now().timestamp() - timestamp_epoch) if age_known else None
        depth = quote.get("depth") if isinstance(quote.get("depth"), dict) else {}
        buy_depth = list(depth.get("buy") or [])
        sell_depth = list(depth.get("sell") or [])
        ohlc = dict(quote.get("ohlc") or {})
        normalized = {
            "symbol": tradingsymbol or symbol,
            "quote_key": quote_key,
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "instrument_token": quote.get("instrument_token") or quote.get("token") or "",
            "token": quote.get("token") or quote.get("instrument_token") or "",
            "mode": quote.get("mode") or quote.get("data_mode") or "",
            "tradable": bool(quote.get("tradable", True)),
            "ltp": float(ltp or 0),
            "last_price": float(ltp or 0),
            "ohlc": ohlc,
            "open": quote.get("open") or ohlc.get("open"),
            "high": quote.get("high") or ohlc.get("high"),
            "low": quote.get("low") or ohlc.get("low"),
            "close": quote.get("close") or ohlc.get("close"),
            "bid": float(bid or 0),
            "ask": float(ask or 0),
            "bid_qty": float(bid_qty or 0),
            "ask_qty": float(ask_qty or 0),
            "total_buy_quantity": float(total_buy or 0),
            "total_sell_quantity": float(total_sell or 0),
            "spread_pct": bid_ask_spread_pct(bid, ask, ltp),
            "depth_imbalance": market_depth_imbalance(bid_qty, ask_qty),
            "volume": quote.get("volume") or quote.get("volume_traded") or 0,
            "volume_traded": quote.get("volume_traded") or quote.get("volume") or 0,
            "oi": oi or 0,
            "open_interest": oi or 0,
            "timestamp": timestamp_text,
            "exchange_timestamp": _timestamp_text(quote.get("exchange_timestamp")),
            "last_trade_time": _timestamp_text(quote.get("last_trade_time")),
            "timestamp_epoch": timestamp_epoch,
            "timestamp_source": timestamp_source,
            "received_epoch": received_epoch,
            "received_at": received_at,
            "age_seconds": float(age_seconds) if age_seconds not in ("", None) else None,
            "age_known": age_known,
            "quote_source": source,
            "data_source": quote.get("data_source") or source,
            "depth": depth,
            "depth_buy_levels": len(buy_depth),
            "depth_sell_levels": len(sell_depth),
            "depth_buy_level_rows": buy_depth,
            "depth_sell_level_rows": sell_depth,
            "depth_present": bool(buy_depth or sell_depth),
            "depth_buy_level_count": len(buy_depth),
            "depth_sell_level_count": len(sell_depth),
            "bid_present": float(bid or 0) > 0,
            "ask_present": float(ask or 0) > 0,
            "bid_qty_present": float(bid_qty or 0) > 0,
            "ask_qty_present": float(ask_qty or 0) > 0,
            "demo_data": bool(quote.get("demo_data")),
            "stale": bool(quote.get("stale")),
            "source": source,
        }
        normalized["depth_health"] = self.depth_controller.evaluate(normalized)
        normalized["depth_present"] = bool(normalized["depth_health"].get("depth_present"))
        normalized["spread_pct"] = normalized["depth_health"].get("spread_pct", normalized["spread_pct"])
        normalized["depth_imbalance"] = normalized["depth_health"].get("depth_imbalance", normalized["depth_imbalance"])
        return normalized


def _depth_price(quote: dict[str, Any], side: str) -> float:
    depth = quote.get("depth") or {}
    rows = depth.get(side) or []
    return float((rows[0] or {}).get("price") or 0) if rows else 0.0


def _depth_qty(quote: dict[str, Any], side: str) -> float:
    depth = quote.get("depth") or {}
    rows = depth.get(side) or []
    return float((rows[0] or {}).get("quantity") or 0) if rows else 0.0


def _timestamp_text(value: Any) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value)


def _timestamp_epoch(value: Any) -> float:
    if not value:
        return 0.0
    try:
        if hasattr(value, "timestamp"):
            return float(value.timestamp())
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if when.tzinfo is None:
            return float(when.timestamp())
        return float(when.astimezone(timezone.utc).timestamp())
    except Exception:
        return 0.0


def _quote_timestamp(quote: dict[str, Any]) -> tuple[Any, str]:
    for key, source in (
        ("exchange_timestamp", "exchange_timestamp"),
        ("timestamp", "timestamp"),
        ("last_trade_time", "last_trade_time"),
    ):
        value = quote.get(key)
        if value:
            return value, source
    return None, "unknown"


def _split_quote_key(value: str) -> tuple[str, str]:
    if ":" not in str(value or ""):
        return "", str(value or "").upper()
    exchange, symbol = str(value).split(":", 1)
    return exchange.upper(), symbol.upper()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
