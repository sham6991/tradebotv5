from __future__ import annotations

from typing import Any


def summarize_trades(trades: list[dict[str, Any]] | None) -> dict[str, Any]:
    trades = list(trades or [])
    pnl = [float(trade.get("net_pnl") or trade.get("pnl") or 0) for trade in trades]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trade_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": round(sum(pnl), 2),
        "win_rate": round((len(wins) / len(trades) * 100) if trades else 0.0, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else "inf",
    }

