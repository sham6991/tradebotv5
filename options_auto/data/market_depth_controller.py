from __future__ import annotations

from typing import Any


DEPTH_FULL = "FULL_DEPTH_OK"
DEPTH_TOP = "TOP_OF_BOOK_OK"
DEPTH_DEGRADED = "DEPTH_DEGRADED"
DEPTH_NONE = "NO_DEPTH"
DEPTH_STALE = "DEPTH_STALE"
DEPTH_INVALID = "DEPTH_INVALID"


class MarketDepthController:
    def evaluate(
        self,
        quote: dict[str, Any],
        settings: dict[str, Any] | None = None,
        profile_policy: dict[str, Any] | None = None,
        stage: str = "scanner",
    ) -> dict[str, Any]:
        settings = dict(settings or {})
        profile_policy = dict(profile_policy or {})
        stage = str(stage or "scanner").lower()
        profile = str(settings.get("strategy_profile") or profile_policy.get("profile") or "BALANCED").upper()
        max_age = _float(
            settings.get("final_validation_quote_stale_seconds") if stage in {"real_final", "final"} else settings.get("max_quote_age_seconds"),
            _float(settings.get("quote_stale_seconds"), 3.0),
        )
        age_known = bool(quote.get("age_known", True))
        age = _float(quote.get("age_seconds"), 0.0)
        buy_levels = _levels(quote, "buy")
        sell_levels = _levels(quote, "sell")
        bid = _float(quote.get("bid") or quote.get("best_bid") or _best_price(buy_levels), 0.0)
        ask = _float(quote.get("ask") or quote.get("best_ask") or _best_price(sell_levels), 0.0)
        bid_qty = _float(quote.get("bid_qty") or _best_qty(buy_levels), 0.0)
        ask_qty = _float(quote.get("ask_qty") or _best_qty(sell_levels), 0.0)
        total_buy = _float(quote.get("total_buy_quantity") or quote.get("buy_quantity"), bid_qty)
        total_sell = _float(quote.get("total_sell_quantity") or quote.get("sell_quantity"), ask_qty)
        spread_pct = _spread_pct(bid, ask, quote.get("ltp") or quote.get("last_price"))
        blockers: list[str] = []
        warnings: list[str] = []
        full_depth = bool(buy_levels and sell_levels and bid > 0 and ask > 0 and bid_qty > 0 and ask_qty > 0)
        top_of_book = bid > 0 and ask > 0
        if top_of_book and ask < bid:
            state = DEPTH_INVALID
            blockers.append("Invalid depth: ask is below bid.")
        elif age_known and age > max_age:
            state = DEPTH_STALE
            blockers.append(f"Depth stale: quote age {age:.1f}s > max {max_age:.1f}s.")
        elif full_depth:
            state = DEPTH_FULL
        elif top_of_book and (bid_qty > 0 or ask_qty > 0 or total_buy > 0 or total_sell > 0):
            state = DEPTH_TOP
            warnings.append("Full five-level depth is unavailable; using top-of-book.")
        elif top_of_book:
            state = DEPTH_DEGRADED
            warnings.append("Bid/ask exists but quantity depth is weak or missing.")
        else:
            state = DEPTH_NONE
            blockers.append("No usable market depth or bid/ask is available.")

        allowed_scanner = state in {DEPTH_FULL, DEPTH_TOP}
        if state == DEPTH_DEGRADED and profile == "AGGRESSIVE" and spread_pct <= _float(settings.get("max_spread_pct"), 0.6):
            allowed_scanner = True
        if profile == "CONSERVATIVE" and state == DEPTH_DEGRADED:
            allowed_scanner = False
        allowed_real = state in {DEPTH_FULL, DEPTH_TOP} and top_of_book and age_known and (age <= max_age if age_known else False)
        if state in {DEPTH_INVALID, DEPTH_STALE, DEPTH_NONE}:
            allowed_scanner = False
            allowed_real = False
        if not age_known and stage in {"real_final", "final"}:
            allowed_real = False
            blockers.append("Unknown quote age cannot pass real final validation.")
        reason = "; ".join(blockers or warnings or [state])
        return {
            "state": state,
            "depth_present": bool(buy_levels or sell_levels),
            "top_of_book_present": top_of_book,
            "bid_present": bid > 0,
            "ask_present": ask > 0,
            "bid_qty_present": bid_qty > 0,
            "ask_qty_present": ask_qty > 0,
            "full_depth_levels_buy": len(buy_levels),
            "full_depth_levels_sell": len(sell_levels),
            "total_buy_quantity": total_buy,
            "total_sell_quantity": total_sell,
            "best_bid": bid,
            "best_ask": ask,
            "spread_pct": spread_pct,
            "depth_imbalance": _imbalance(total_buy or bid_qty, total_sell or ask_qty),
            "allowed_for_scanner": allowed_scanner,
            "allowed_for_real_final": allowed_real,
            "reason": reason,
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": list(dict.fromkeys(warnings)),
        }


def _levels(quote: dict[str, Any], side: str) -> list[dict[str, Any]]:
    depth = quote.get("depth") if isinstance(quote.get("depth"), dict) else {}
    rows = depth.get(side) or []
    return [dict(row or {}) for row in rows if isinstance(row, dict)]


def _best_price(rows: list[dict[str, Any]]) -> float:
    return _float((rows[0] or {}).get("price"), 0.0) if rows else 0.0


def _best_qty(rows: list[dict[str, Any]]) -> float:
    return _float((rows[0] or {}).get("quantity"), 0.0) if rows else 0.0


def _spread_pct(bid: Any, ask: Any, ltp: Any) -> float:
    bid = _float(bid, 0.0)
    ask = _float(ask, 0.0)
    ltp = _float(ltp, 0.0)
    if bid <= 0 or ask <= 0:
        return 999.0
    base = ltp if ltp > 0 else (bid + ask) / 2
    return round(max(0.0, ask - bid) / base * 100, 4) if base > 0 else 999.0


def _imbalance(buy_qty: Any, sell_qty: Any) -> float:
    buy = _float(buy_qty, 0.0)
    sell = _float(sell_qty, 0.0)
    total = buy + sell
    return round((buy - sell) / total, 4) if total > 0 else 0.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
