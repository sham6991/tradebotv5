from __future__ import annotations

from typing import Any


PROMOTION_ORDER = ["LEARNING", "PAPER_SAFE", "REAL_1_LOT_TRIAL", "REAL_CONTROLLED", "AGGRESSIVE"]


class PromotionManager:
    def evaluate(self, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
        metrics = dict(metrics or {})
        current = str(metrics.get("current_stage") or "LEARNING").upper()
        if current not in PROMOTION_ORDER:
            current = "LEARNING"
        blockers = []
        warnings = []
        if int(metrics.get("sessions_completed") or 0) < 5:
            blockers.append("Complete at least 5 sessions.")
        if float(metrics.get("net_pnl") or 0) <= 0:
            blockers.append("Net result must be positive.")
        if float(metrics.get("max_drawdown_pct") or 100) > 8:
            blockers.append("Drawdown must stay under 8%.")
        if int(metrics.get("unprotected_position_incidents") or 0) > 0:
            blockers.append("No unprotected position incidents allowed.")
        if int(metrics.get("major_safety_errors") or 0) > 0:
            blockers.append("No major safety errors allowed.")
        index = PROMOTION_ORDER.index(current)
        natural_next_stage = PROMOTION_ORDER[min(index + 1, len(PROMOTION_ORDER) - 1)]
        requested_stage = str(metrics.get("requested_stage") or "").upper()
        force_override = bool(metrics.get("force_override"))
        if requested_stage and requested_stage in PROMOTION_ORDER and PROMOTION_ORDER.index(requested_stage) > index + 1:
            if force_override:
                warnings.append("Force override requested for promotion jump; show clear real-money warning before enabling.")
                next_stage = requested_stage
            else:
                blockers.append("Promotion cannot jump stages without explicit force override.")
                next_stage = current
        else:
            next_stage = requested_stage if requested_stage in PROMOTION_ORDER else natural_next_stage
        return {
            "current_stage": current,
            "next_stage": next_stage if not blockers and next_stage != current else current,
            "promotion_allowed": not blockers and next_stage != current,
            "blockers": blockers,
            "warnings": warnings,
        }
