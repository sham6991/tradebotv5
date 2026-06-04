from __future__ import annotations

from typing import Any


class MasterGovernor:
    def evaluate(
        self,
        mode_guard: dict[str, Any],
        data_quality: dict[str, Any],
        risk: dict[str, Any],
        discipline: dict[str, Any],
        execution: dict[str, Any],
        market: dict[str, Any] | None = None,
        strategy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        market = dict(market or {})
        strategy = dict(strategy or {})
        blockers = []
        blockers.extend(data_quality.get("blockers") or [])
        blockers.extend(risk.get("blockers") or [])
        blockers.extend(discipline.get("blockers") or [])
        blockers.extend(execution.get("blockers") or [])
        blockers.extend(market.get("blockers") or [])
        blockers.extend(strategy.get("blockers") or [])
        if not strategy.get("selected"):
            blockers.append("No selected trade candidate.")
        if blockers:
            state = self._state_for(data_quality, risk, discipline, execution, market, strategy)
        else:
            state = "ALLOW_TRADING"
        return {
            "allowed": not blockers,
            "state": state,
            "blockers": blockers,
            "warnings": (data_quality.get("warnings") or []) + (risk.get("warnings") or []) + (discipline.get("warnings") or []) + (execution.get("warnings") or []),
            "mode": mode_guard.get("mode"),
        }

    def _state_for(self, data_quality, risk, discipline, execution, market, strategy) -> str:
        if data_quality.get("blockers"):
            return "BLOCKED_BY_DATA"
        if risk.get("blockers"):
            return "BLOCKED_BY_RISK"
        if execution.get("blockers"):
            return "BLOCKED_BY_EXECUTION"
        if market.get("blockers"):
            return "BLOCKED_BY_MARKET"
        if discipline.get("blockers"):
            return "BLOCKED_BY_DISCIPLINE"
        if strategy.get("blockers") or not strategy.get("selected"):
            return "WAIT_FOR_SETUP"
        return "MANUAL_ATTENTION_REQUIRED"

