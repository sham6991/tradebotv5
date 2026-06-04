from __future__ import annotations

from typing import Any

from options_auto.indicators.technicals import market_depth_imbalance


def depth_summary(depth: dict[str, Any] | None) -> dict[str, Any]:
    depth = dict(depth or {})
    buy = list(depth.get("buy") or [])
    sell = list(depth.get("sell") or [])
    bid_qty = sum(float(row.get("quantity") or 0) for row in buy[:5])
    ask_qty = sum(float(row.get("quantity") or 0) for row in sell[:5])
    bid_value = sum(float(row.get("price") or 0) * float(row.get("quantity") or 0) for row in buy[:5])
    ask_value = sum(float(row.get("price") or 0) * float(row.get("quantity") or 0) for row in sell[:5])
    return {
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "bid_value": round(bid_value, 2),
        "ask_value": round(ask_value, 2),
        "imbalance": market_depth_imbalance(bid_qty, ask_qty),
        "levels": min(len(buy), len(sell), 5),
    }

