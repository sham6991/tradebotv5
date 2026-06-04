from __future__ import annotations

from typing import Any


def explain_score(score_breakdown: dict[str, Any], blockers: list[str] | None = None) -> str:
    blockers = list(blockers or [])
    if blockers:
        return "Blocked: " + "; ".join(blockers[:4])
    ordered = sorted(score_breakdown.items(), key=lambda item: float(item[1] or 0), reverse=True)
    parts = [f"{name} {float(value):.1f}" for name, value in ordered[:4]]
    return "Accepted by " + ", ".join(parts) if parts else "No strong components."

