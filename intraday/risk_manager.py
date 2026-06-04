from __future__ import annotations

from dataclasses import dataclass, field

from .constants import MODE_REAL
from .models import IntradaySettings, Signal


@dataclass
class RiskState:
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trade_count: int = 0
    open_positions: int = 0
    consecutive_losses: int = 0
    symbol_trade_counts: dict[str, int] = field(default_factory=dict)
    kill_switch: bool = False
    freeze_reason: str = ""


class RiskManager:
    def __init__(self, settings: IntradaySettings):
        self.settings = settings
        self.state = RiskState()

    def pre_trade_blockers(self, signal: Signal) -> list[str]:
        blockers = list(signal.blockers)
        settings = self.settings
        state = self.state
        if state.kill_switch:
            blockers.append("Kill switch is active.")
        if state.freeze_reason:
            blockers.append(state.freeze_reason)
        if state.realized_pnl <= -abs(settings.max_daily_loss):
            blockers.append("Max daily loss reached.")
        if state.realized_pnl >= abs(settings.max_daily_profit):
            blockers.append("Max daily profit target reached.")
        if state.trade_count >= settings.max_trades_per_day:
            blockers.append("Max trades per day reached.")
        if state.open_positions >= settings.max_open_positions:
            blockers.append("Max open positions reached.")
        if state.symbol_trade_counts.get(signal.symbol, 0) >= settings.max_trades_per_stock:
            blockers.append(f"Max trades reached for {signal.symbol}.")
        if state.consecutive_losses >= settings.stop_after_consecutive_losses:
            blockers.append("Consecutive loss lock is active.")
        if settings.mode == MODE_REAL and not settings.confirm_real_mode:
            blockers.append("Real mode is not explicitly confirmed.")
        if settings.mode == MODE_REAL and signal.final_decision == "ELIGIBLE" and not settings.auto_real_orders_confirmed:
            blockers.append("Auto real orders are not separately confirmed.")
        return blockers

    def mark_order_attempt(self, symbol: str) -> None:
        self.state.trade_count += 1
        self.state.symbol_trade_counts[symbol] = self.state.symbol_trade_counts.get(symbol, 0) + 1

    def kill(self, reason: str = "Kill switch activated.") -> None:
        self.state.kill_switch = True
        self.state.freeze_reason = reason
