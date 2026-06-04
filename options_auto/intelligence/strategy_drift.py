from __future__ import annotations

from typing import Any


class StrategyDriftMonitor:
    def evaluate(self, trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        trades = list(trades or [])
        closed = [trade for trade in trades if trade.get("closed", True)]
        if not closed:
            return {"state": "INSUFFICIENT_DATA", "recommendation": "Keep learning mode.", "sample_size": 0}
        wins = [trade for trade in closed if float(trade.get("pnl") or trade.get("net_pnl") or 0) > 0]
        losses = [trade for trade in closed if float(trade.get("pnl") or trade.get("net_pnl") or 0) < 0]
        gross_profit = sum(float(trade.get("pnl") or trade.get("net_pnl") or 0) for trade in wins)
        gross_loss = abs(sum(float(trade.get("pnl") or trade.get("net_pnl") or 0) for trade in losses))
        win_rate = len(wins) / len(closed) * 100
        profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        false_signals = 0
        slippages = []
        premium_scores = []
        for trade in closed:
            pnl = float(trade.get("pnl") or trade.get("net_pnl") or 0)
            equity += pnl
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity - peak)
            if trade.get("false_signal"):
                false_signals += 1
            if trade.get("slippage_points") not in ("", None):
                slippages.append(float(trade.get("slippage_points") or 0))
            if trade.get("premium_response_score") not in ("", None):
                premium_scores.append(float(trade.get("premium_response_score") or 0))
        avg_slippage = sum(slippages) / len(slippages) if slippages else 0.0
        premium_response_quality = sum(premium_scores) / len(premium_scores) if premium_scores else 100.0
        false_signal_rate = false_signals / len(closed) * 100
        drift_reasons = []
        if win_rate < 42:
            drift_reasons.append("Rolling win rate is weak.")
        if profit_factor < 1.05:
            drift_reasons.append("Rolling profit factor is weak.")
        if false_signal_rate > 25:
            drift_reasons.append("False signal rate is elevated.")
        if avg_slippage > 2.0:
            drift_reasons.append("Slippage has increased.")
        if premium_response_quality < 45:
            drift_reasons.append("Premium response quality is weak.")
        if len(closed) >= 10 and drift_reasons:
            state = "DRIFT_DETECTED"
            recommendation = "Switch conservative, reduce size, and raise threshold."
        else:
            state = "STABLE"
            recommendation = "Continue current stage."
        lock_engine = len(closed) >= 10 and (profit_factor < 0.8 or false_signal_rate > 40)
        return {
            "state": state,
            "recommendation": recommendation,
            "sample_size": len(closed),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else "inf",
            "drawdown": round(max_drawdown, 2),
            "false_signal_rate": round(false_signal_rate, 2),
            "avg_slippage_points": round(avg_slippage, 2),
            "premium_response_quality": round(premium_response_quality, 2),
            "drift_reasons": drift_reasons,
            "suggested_size_multiplier": 0.5 if state == "DRIFT_DETECTED" else 1.0,
            "suggested_threshold_adjustment": 5 if state == "DRIFT_DETECTED" else 0,
            "lock_engine": lock_engine,
            "analysis_only": True,
        }
