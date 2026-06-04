from __future__ import annotations

from datetime import datetime
from typing import Any

from options_auto.indicators.technicals import bid_ask_spread_pct, market_depth_imbalance


class LiveQuoteProvider:
    def __init__(self, provider: Any | None = None):
        self.provider = provider
        self.snapshots: dict[str, dict[str, Any]] = {}

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
        ltp = quote.get("last_price") or quote.get("ltp") or 0
        bid = quote.get("bid") or quote.get("best_bid") or _depth_price(quote, "buy")
        ask = quote.get("ask") or quote.get("best_ask") or _depth_price(quote, "sell")
        bid_qty = quote.get("bid_qty") or quote.get("buy_quantity") or _depth_qty(quote, "buy")
        ask_qty = quote.get("ask_qty") or quote.get("sell_quantity") or _depth_qty(quote, "sell")
        return {
            "symbol": symbol,
            "ltp": float(ltp or 0),
            "bid": float(bid or 0),
            "ask": float(ask or 0),
            "bid_qty": float(bid_qty or 0),
            "ask_qty": float(ask_qty or 0),
            "spread_pct": bid_ask_spread_pct(bid, ask, ltp),
            "depth_imbalance": market_depth_imbalance(bid_qty, ask_qty),
            "volume": quote.get("volume") or quote.get("volume_traded") or 0,
            "oi": quote.get("oi") or 0,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source": quote.get("source") or "kite_quote",
        }


def _depth_price(quote: dict[str, Any], side: str) -> float:
    depth = quote.get("depth") or {}
    rows = depth.get(side) or []
    return float((rows[0] or {}).get("price") or 0) if rows else 0.0


def _depth_qty(quote: dict[str, Any], side: str) -> float:
    depth = quote.get("depth") or {}
    rows = depth.get(side) or []
    return float((rows[0] or {}).get("quantity") or 0) if rows else 0.0

