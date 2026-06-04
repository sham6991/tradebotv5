from __future__ import annotations

from typing import Any


class BacktestBroker:
    def __init__(self, starting_balance: float = 20000.0, slippage_points: float = 0.05, charge_per_order: float = 20.0):
        self.starting_balance = float(starting_balance)
        self.balance = float(starting_balance)
        self.slippage_points = float(slippage_points)
        self.charge_per_order = float(charge_per_order)
        self.orders: list[dict[str, Any]] = []
        self.trades: list[dict[str, Any]] = []

    def simulate_long_option_trade(self, candles, entry_price: float, stoploss: float, target: float, quantity: int, signal_index: int = 0) -> dict[str, Any]:
        entry_fill = float(entry_price) + self.slippage_points
        status = "MISSED"
        exit_price = 0.0
        exit_index = signal_index
        exit_reason = ""
        for index in range(signal_index, len(candles)):
            row = candles.iloc[index] if hasattr(candles, "iloc") else candles[index]
            low = float(row.get("low") or row.get("Low") or 0)
            high = float(row.get("high") or row.get("High") or 0)
            if index == signal_index:
                if low <= stoploss:
                    status = "CLOSED"
                    exit_price = float(stoploss) - self.slippage_points
                    exit_reason = "STOPLOSS_SAME_CANDLE"
                    exit_index = index
                    break
                continue
            if low <= stoploss:
                status = "CLOSED"
                exit_price = float(stoploss) - self.slippage_points
                exit_reason = "STOPLOSS"
                exit_index = index
                break
            if high >= target:
                status = "CLOSED"
                exit_price = float(target) - self.slippage_points
                exit_reason = "TARGET"
                exit_index = index
                break
        if status != "CLOSED":
            row = candles.iloc[-1] if hasattr(candles, "iloc") else candles[-1]
            exit_price = float(row.get("close") or row.get("Close") or entry_fill)
            exit_index = len(candles) - 1
            exit_reason = "DAY_END"
            status = "CLOSED"
        gross = (exit_price - entry_fill) * int(quantity)
        charges = self.charge_per_order * 2
        net = gross - charges
        self.balance += net
        trade = {
            "status": status,
            "entry_price": round(entry_fill, 2),
            "exit_price": round(exit_price, 2),
            "quantity": int(quantity),
            "gross_pnl": round(gross, 2),
            "charges": round(charges, 2),
            "net_pnl": round(net, 2),
            "exit_reason": exit_reason,
            "entry_index": int(signal_index),
            "exit_index": int(exit_index),
        }
        self.trades.append(trade)
        return trade

    def snapshot(self) -> dict[str, Any]:
        return {"starting_balance": self.starting_balance, "balance": self.balance, "trades": self.trades}

