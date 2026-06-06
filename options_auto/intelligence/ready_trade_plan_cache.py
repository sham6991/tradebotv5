from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT
from options_auto.core.clock import iso_now


@dataclass
class ReadyTradePlanCache:
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)

    def refresh_from_decision(self, decision: dict[str, Any], settings: dict[str, Any], now_epoch: float | None = None) -> dict[str, Any]:
        now_epoch = time.time() if now_epoch is None else float(now_epoch)
        underlying = str(settings.get("underlying") or "NIFTY").upper()
        side = str(decision.get("selected_side") or SIDE_WAIT).upper()
        contract = dict(decision.get("selected_contract") or {})
        trade_plan = dict(decision.get("trade_plan") or {})
        status = "READY" if decision.get("allowed") and side in {SIDE_CE, SIDE_PE} and contract and trade_plan else "WAIT"
        valid_for = self.valid_for_seconds(settings)
        plan_id = self._plan_id(underlying, side, contract, trade_plan)
        plan = {
            "plan_id": plan_id,
            "created_at": iso_now(),
            "last_refreshed_at": iso_now(),
            "last_refreshed_epoch": now_epoch,
            "valid_until_epoch": now_epoch + valid_for,
            "valid_for_seconds": valid_for,
            "mode": settings.get("mode"),
            "underlying": underlying,
            "side": side if status == "READY" else SIDE_WAIT,
            "contract": contract,
            "entry_plan": {
                "entry_limit": trade_plan.get("entry_price"),
                "signal_price": contract.get("ltp") or trade_plan.get("entry_price"),
                "tick_size": contract.get("tick_size") or 0.05,
            },
            "target_plan": {"target": trade_plan.get("target")},
            "stoploss_plan": {"stoploss": trade_plan.get("stoploss")},
            "quantity_plan": {
                "quantity": trade_plan.get("quantity"),
                "lots": trade_plan.get("lots"),
                "lot_size": trade_plan.get("lot_size"),
            },
            "scores": {
                "trade_score": decision.get("trade_score"),
                "theta_premium_risk": decision.get("theta_premium_risk"),
                "entry_dependency_mode": decision.get("entry_dependency_mode") or (decision.get("trade_score") or {}).get("entry_dependency_mode"),
            },
            "market_context": {
                "market_cue": decision.get("market_cue"),
                "regime": decision.get("regime"),
                "data_quality": decision.get("data_quality"),
                "governor": decision.get("governor"),
            },
            "premium_context": {
                "premium_momentum": contract.get("premium_momentum"),
                "premium_expansion_confirmed": contract.get("premium_expansion_confirmed"),
                "premium_return_1": contract.get("premium_return_1") or (contract.get("premium_momentum") or {}).get("premium_return_1"),
                "premium_return_3": contract.get("premium_return_3") or (contract.get("premium_momentum") or {}).get("premium_return_3"),
                "option_atr14": contract.get("option_atr14") or contract.get("atr14"),
                "option_vwap": contract.get("option_vwap") or contract.get("vwap"),
                "relative_volume": contract.get("relative_volume"),
                "spread_pct": contract.get("spread_pct"),
            },
            "blockers": list(decision.get("blockers") or []),
            "warnings": list(decision.get("warnings") or []),
            "status": status if status == "READY" else ("BLOCKED" if decision.get("blockers") else "WAIT"),
        }
        self.plans[underlying] = plan
        return dict(plan)

    def get(self, underlying: str = "NIFTY", now_epoch: float | None = None) -> dict[str, Any] | None:
        now_epoch = time.time() if now_epoch is None else float(now_epoch)
        plan = self.plans.get(str(underlying or "NIFTY").upper())
        if not plan:
            return None
        if now_epoch > float(plan.get("valid_until_epoch") or 0):
            stale = dict(plan)
            stale["status"] = "STALE"
            stale["blockers"] = list(stale.get("blockers") or []) + ["Ready trade plan expired."]
            self.plans[str(underlying or "NIFTY").upper()] = stale
            return stale
        return dict(plan)

    def invalidate(self, underlying: str = "NIFTY", reason: str = "") -> dict[str, Any] | None:
        key = str(underlying or "NIFTY").upper()
        plan = self.plans.get(key)
        if not plan:
            return None
        plan = dict(plan)
        plan["status"] = "STALE"
        if reason:
            plan["blockers"] = list(plan.get("blockers") or []) + [reason]
        self.plans[key] = plan
        return dict(plan)

    def valid_for_seconds(self, settings: dict[str, Any]) -> float:
        profile = str(settings.get("strategy_profile") or "BALANCED").upper()
        if profile == "AGGRESSIVE":
            return float(settings.get("max_plan_age_seconds_aggressive") or 3)
        if profile == "CONSERVATIVE":
            return float(settings.get("max_plan_age_seconds_conservative") or 8)
        return float(settings.get("max_plan_age_seconds_balanced") or 5)

    def snapshot(self) -> dict[str, Any]:
        return {"plans": {key: dict(value) for key, value in self.plans.items()}}

    def _plan_id(self, underlying: str, side: str, contract: dict[str, Any], trade_plan: dict[str, Any]) -> str:
        symbol = contract.get("tradingsymbol") or ""
        entry = trade_plan.get("entry_price") or ""
        return f"RTP-{underlying}-{side}-{symbol}-{entry}-{uuid4().hex[:8].upper()}"
