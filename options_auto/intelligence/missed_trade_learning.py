from __future__ import annotations

from typing import Any


class MissedTradeLearning:
    """Analysis-only outcome tracker.

    This module never changes live thresholds by itself. It only reports what
    would need review.
    """

    def evaluate(self, decisions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        decisions = [dict(item) for item in list(decisions or [])]
        buckets = {
            "accepted_won": 0,
            "accepted_lost": 0,
            "rejected_would_have_won": 0,
            "rejected_would_have_lost": 0,
        }
        review_items = []
        for item in decisions:
            accepted = bool(item.get("accepted") if "accepted" in item else item.get("allowed"))
            pnl = float(item.get("actual_pnl") or item.get("pnl") or item.get("outcome_pnl") or 0)
            if accepted and pnl > 0:
                buckets["accepted_won"] += 1
            elif accepted and pnl < 0:
                buckets["accepted_lost"] += 1
            elif not accepted and pnl > 0:
                buckets["rejected_would_have_won"] += 1
                review_items.append({"type": "MISSED_WINNER", "reason": item.get("reason") or item.get("blockers") or "", "pnl": pnl})
            elif not accepted and pnl < 0:
                buckets["rejected_would_have_lost"] += 1
        total = len(decisions)
        missed_winner_rate = buckets["rejected_would_have_won"] / total * 100 if total else 0.0
        return {
            "sample_size": total,
            **buckets,
            "missed_winner_rate": round(missed_winner_rate, 2),
            "review_items": review_items[-100:],
            "analysis_only": True,
            "live_parameter_changes": [],
        }
