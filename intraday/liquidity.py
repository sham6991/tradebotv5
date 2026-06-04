from __future__ import annotations


def score_liquidity(ltp: float, depth: dict | None = None) -> dict:
    depth = depth or {}
    buy = depth.get("buy") or depth.get("bids") or []
    sell = depth.get("sell") or depth.get("asks") or []
    best_bid = float((buy[0] if buy else {}).get("price") or 0)
    best_ask = float((sell[0] if sell else {}).get("price") or 0)
    bid_qty = sum(float(row.get("quantity") or row.get("qty") or 0) for row in buy[:5])
    ask_qty = sum(float(row.get("quantity") or row.get("qty") or 0) for row in sell[:5])
    if not best_bid and ltp:
        best_bid = float(ltp)
    if not best_ask and ltp:
        best_ask = float(ltp)
    spread = max(0.0, best_ask - best_bid)
    spread_pct = (spread / float(ltp) * 100) if ltp else 0.0
    total_depth = bid_qty + ask_qty
    imbalance = ((bid_qty - ask_qty) / total_depth) if total_depth else 0.0
    spread_score = max(0.0, 45.0 - min(45.0, spread_pct * 200))
    depth_score = min(35.0, total_depth / 1000.0 * 35.0)
    balance_score = max(0.0, 20.0 - abs(imbalance) * 8.0)
    score = max(0.0, min(100.0, spread_score + depth_score + balance_score))
    return {
        "spread": spread,
        "spread_pct": spread_pct,
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "depth_imbalance": imbalance,
        "liquidity_score": score,
        "fill_probability": min(1.0, score / 100.0),
    }
