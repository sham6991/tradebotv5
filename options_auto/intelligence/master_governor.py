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
        blocker_stages = self._blocker_stages(data_quality, risk, discipline, execution, market, strategy)
        blockers = []
        for item in blocker_stages:
            blockers.extend(item.get("blockers") or [])
        blockers = list(dict.fromkeys(blockers))
        state = blocker_stages[0]["stage"] if blocker_stages else "ALLOW_TRADING"
        primary_blocker = (blocker_stages[0].get("blockers") or [""])[0] if blocker_stages else ""
        return {
            "allowed": not blockers,
            "state": state,
            "blockers": blockers,
            "warnings": (data_quality.get("warnings") or []) + (risk.get("warnings") or []) + (discipline.get("warnings") or []) + (execution.get("warnings") or []),
            "mode": mode_guard.get("mode"),
            "primary_block_stage": state if blockers else "",
            "primary_blocker": primary_blocker,
            "blocker_stages": blocker_stages,
        }

    def _blocker_stages(self, data_quality, risk, discipline, execution, market, strategy) -> list[dict[str, Any]]:
        rows = [
            ("BLOCKED_BY_DATA", data_quality.get("blockers") or []),
            ("BLOCKED_BY_RISK", risk.get("blockers") or []),
            ("BLOCKED_BY_EXECUTION", execution.get("blockers") or []),
            ("BLOCKED_BY_MARKET", market.get("blockers") or []),
            ("BLOCKED_BY_DISCIPLINE", discipline.get("blockers") or []),
        ]
        strategy_blockers = list(strategy.get("blockers") or [])
        if not strategy.get("selected"):
            strategy_blockers.append("No selected trade candidate.")
        rows.append(("WAIT_FOR_SETUP", strategy_blockers))
        return [
            {"stage": stage, "blockers": list(dict.fromkeys(blockers))}
            for stage, blockers in rows
            if blockers
        ]

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
