from __future__ import annotations

from typing import Any


class ProfessionalDisciplineEngine:
    def evaluate(self, decision: dict[str, Any], risk_state: dict[str, Any] | None = None) -> dict[str, Any]:
        risk_state = dict(risk_state or {})
        blockers = []
        warnings = []
        score = 100.0
        if decision.get("chase_detected"):
            blockers.append("FOMO/chase filter rejected the setup.")
            score -= 35
        if int(risk_state.get("consecutive_losses") or 0) > 0 and decision.get("aggressiveness") == "high":
            blockers.append("Revenge-trade guard blocks high aggression after a loss.")
            score -= 30
        if decision.get("manual_override_to_increase_risk"):
            warnings.append("Manual risk increase detected.")
            score -= 20
        if decision.get("hold_loser_without_reason"):
            blockers.append("Hope-hold prevention requires exit or fresh thesis.")
            score -= 30
        return {
            "allowed": not blockers,
            "state": "DISCIPLINE_OK" if not blockers else "BLOCKED_BY_DISCIPLINE",
            "discipline_score": max(0.0, min(100.0, score)),
            "blockers": blockers,
            "warnings": warnings,
        }

