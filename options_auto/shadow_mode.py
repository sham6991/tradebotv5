from __future__ import annotations

from typing import Any


class ShadowModeEngine:
    def __init__(self):
        self.signals: list[dict[str, Any]] = []

    def record(self, decision: dict[str, Any]) -> dict[str, Any]:
        signal = {
            "mode": "SHADOW",
            "would_trade": bool(decision.get("allowed")),
            "selected": (decision.get("selection") or {}).get("selected"),
            "blockers": decision.get("blockers") or [],
            "expected_pnl": float(decision.get("expected_pnl") or 0.0),
            "actual_pnl": None,
            "outcome": "PENDING",
            "late_entry": bool(decision.get("late_entry")),
            "late_exit": bool(decision.get("late_exit")),
            "missed_trade": False,
            "decision": decision,
        }
        self.signals.append(signal)
        return signal

    def record_outcome(self, index: int, actual_pnl: float, outcome: str = "") -> dict[str, Any]:
        signal = self.signals[int(index)]
        signal["actual_pnl"] = float(actual_pnl)
        signal["outcome"] = str(outcome or ("WIN" if actual_pnl > 0 else "LOSS" if actual_pnl < 0 else "FLAT")).upper()
        if not signal["would_trade"] and actual_pnl > 0:
            signal["missed_trade"] = True
        return dict(signal)

    def report(self) -> dict[str, Any]:
        total = len(self.signals)
        would_trade = sum(1 for signal in self.signals if signal["would_trade"])
        rejected = total - would_trade
        completed = [signal for signal in self.signals if signal.get("actual_pnl") is not None]
        wins = [signal for signal in completed if float(signal.get("actual_pnl") or 0) > 0]
        losses = [signal for signal in completed if float(signal.get("actual_pnl") or 0) < 0]
        return {
            "signals": total,
            "would_trade": would_trade,
            "rejected": rejected,
            "expected_pnl": round(sum(float(signal.get("expected_pnl") or 0) for signal in self.signals), 2),
            "actual_pnl": round(sum(float(signal.get("actual_pnl") or 0) for signal in completed), 2),
            "would_have_wins": len(wins),
            "would_have_losses": len(losses),
            "false_entries": sum(1 for signal in completed if signal["would_trade"] and float(signal.get("actual_pnl") or 0) < 0),
            "missed_trades": sum(1 for signal in self.signals if signal.get("missed_trade")),
            "late_entries": sum(1 for signal in self.signals if signal.get("late_entry")),
            "late_exits": sum(1 for signal in self.signals if signal.get("late_exit")),
            "recent": self.signals[-100:],
        }
