from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RiskState:
    realized_pnl: float = 0.0
    open_trades: int = 0
    trades_today: int = 0
    consecutive_losses: int = 0
    stoploss_hits: int = 0
    cooldown_until_epoch: float = 0.0
    api_failures: int = 0
    rejected_orders: int = 0
    slippage_points: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class RiskEngine:
    def evaluate(self, settings: dict[str, Any], state: RiskState | dict[str, Any] | None = None, now_epoch: float = 0.0) -> dict[str, Any]:
        if isinstance(state, RiskState):
            state_dict = state.to_dict()
        else:
            state_dict = dict(state or {})
        blockers = []
        warnings = []
        realized = float(state_dict.get("realized_pnl") or 0)
        if realized <= -abs(float(settings.get("max_daily_loss") or 0)):
            blockers.append("Max daily loss reached.")
        if realized >= abs(float(settings.get("max_daily_profit_lock") or 0)):
            blockers.append("Daily profit lock reached.")
        if int(state_dict.get("trades_today") or 0) >= int(settings.get("max_trades_per_day") or 0):
            blockers.append("Max trades per day reached.")
        if int(state_dict.get("open_trades") or 0) >= int(settings.get("max_open_trades") or 1):
            blockers.append("Max open trades reached.")
        if int(state_dict.get("consecutive_losses") or 0) >= int(settings.get("max_consecutive_losses") or 0):
            blockers.append("Consecutive loss lock reached.")
        if now_epoch and float(state_dict.get("cooldown_until_epoch") or 0) > now_epoch:
            blockers.append("Cooldown is active.")
        if int(state_dict.get("api_failures") or 0) >= 3:
            blockers.append("Broker/API failure guard is active.")
        if float(state_dict.get("slippage_points") or 0) > float(settings.get("max_allowed_slippage_points") or 5):
            warnings.append("Recent slippage is elevated.")
        return {
            "allowed": not blockers,
            "state": "RISK_OK" if not blockers else "BLOCKED_BY_RISK",
            "blockers": blockers,
            "warnings": warnings,
            "risk_state": state_dict,
        }


class PositionSizer:
    def quantity(self, premium: float, lot_size: int, available_capital: float, settings: dict[str, Any]) -> dict[str, Any]:
        lot_size = int(lot_size or 0)
        premium = float(premium or 0)
        available_capital = float(available_capital or 0)
        if lot_size <= 0 or premium <= 0 or available_capital <= 0:
            return {"quantity": 0, "lots": 0, "reason": "Missing lot size, premium, or capital."}
        cap_pct = float(settings.get("max_capital_per_trade_pct") or 20)
        capital_cap = available_capital * cap_pct / 100.0
        max_lots = max(1, int(settings.get("max_lots_per_trade") or 1))
        affordable_lots = int(capital_cap // (premium * lot_size))
        lots = max(0, min(max_lots, affordable_lots))
        if lots <= 0:
            return {"quantity": 0, "lots": 0, "required_per_lot": premium * lot_size, "capital_cap": capital_cap, "reason": "Calculated quantity is below one lot."}
        return {"quantity": lots * lot_size, "lots": lots, "required": lots * lot_size * premium, "capital_cap": capital_cap, "reason": ""}

