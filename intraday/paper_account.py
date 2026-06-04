from __future__ import annotations

import json
import os
from datetime import datetime


class PaperAccountStore:
    def __init__(self, path: str, default_balance: float = 100000.0):
        self.path = path
        self.default_balance = float(default_balance)

    def load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            data = {}
        if not data:
            data = self._fresh(self.default_balance)
            self.save(data)
        return self._normalise(data)

    def save(self, data: dict) -> dict:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        data = self._normalise(data)
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        return data

    def reset(self, balance: float) -> dict:
        return self.save(self._fresh(float(balance)))

    def set_balance(self, balance: float) -> dict:
        data = self.load()
        data["available_balance"] = float(balance)
        return self.save(self._refresh_totals(data))

    def reserve_margin(self, amount: float) -> dict:
        amount = max(0.0, float(amount or 0))
        data = self.load()
        if amount > float(data["available_balance"]):
            raise ValueError("Insufficient paper funds for required margin.")
        data["available_balance"] = float(data["available_balance"]) - amount
        data["used_margin"] = float(data["used_margin"]) + amount
        return self.save(self._refresh_totals(data))

    def release_margin(self, amount: float) -> dict:
        amount = max(0.0, float(amount or 0))
        data = self.load()
        release = min(amount, float(data["used_margin"]))
        data["used_margin"] = float(data["used_margin"]) - release
        data["available_balance"] = float(data["available_balance"]) + release
        return self.save(self._refresh_totals(data))

    def apply_realized_pnl(self, pnl: float, release_margin: float = 0.0, charges: float = 0.0) -> dict:
        return self.apply_trade_settlement(pnl, release_margin=release_margin, charges=charges, defer_positive_profit=False)

    def apply_trade_settlement(
        self,
        pnl: float,
        release_margin: float = 0.0,
        charges: float = 0.0,
        defer_positive_profit: bool = False,
    ) -> dict:
        data = self.load()
        release = min(max(0.0, float(release_margin or 0)), float(data["used_margin"]))
        net = float(pnl or 0) - float(charges or 0)
        data["used_margin"] = float(data["used_margin"]) - release
        data["available_balance"] = float(data["available_balance"]) + release
        if defer_positive_profit and net > 0:
            data["available_balance"] = float(data["available_balance"]) - float(charges or 0)
            data["pending_session_profit"] = float(data.get("pending_session_profit") or 0) + net
        else:
            data["available_balance"] = float(data["available_balance"]) + net
        data["realized_pnl"] = float(data["realized_pnl"]) + net
        data["charges"] = float(data.get("charges") or 0) + float(charges or 0)
        data["position_value"] = 0.0
        data["unrealized_pnl"] = 0.0
        return self.save(self._refresh_totals(data))

    def settle_pending_session_profit(self) -> dict:
        data = self.load()
        pending = max(0.0, float(data.get("pending_session_profit") or 0))
        if pending:
            data["available_balance"] = float(data["available_balance"]) + pending
            data["pending_session_profit"] = 0.0
        return self.save(self._refresh_totals(data))

    def mark_to_market(self, position_value: float | None = None, unrealized_pnl: float | None = None) -> dict:
        data = self.load()
        if position_value is not None:
            data["position_value"] = max(0.0, float(position_value or 0))
        if unrealized_pnl is not None:
            data["unrealized_pnl"] = float(unrealized_pnl or 0)
        return self.save(self._refresh_totals(data))

    def snapshot(self) -> dict:
        data = self.load()
        return {
            "available": data["available_balance"],
            "available_cash": data["available_cash"],
            "used_margin": data["used_margin"],
            "position_value": data["position_value"],
            "equity": data["equity"],
            "starting_balance": data["starting_balance"],
            "realized_pnl": data["realized_pnl"],
            "unrealized_pnl": data["unrealized_pnl"],
            "charges": data.get("charges", 0.0),
            "pending_session_profit": data.get("pending_session_profit", 0.0),
            "net_pnl": data["net_pnl"],
            "updated_at": data["updated_at"],
        }

    def _fresh(self, balance: float) -> dict:
        return {
            "starting_balance": float(balance),
            "available_balance": float(balance),
            "available_cash": float(balance),
            "used_margin": 0.0,
            "position_value": 0.0,
            "equity": float(balance),
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "charges": 0.0,
            "pending_session_profit": 0.0,
            "net_pnl": 0.0,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _normalise(self, data: dict) -> dict:
        fresh = self._fresh(float(data.get("starting_balance") or self.default_balance))
        fresh.update(data or {})
        for key in (
            "starting_balance",
            "available_balance",
            "available_cash",
            "used_margin",
            "position_value",
            "equity",
            "realized_pnl",
            "unrealized_pnl",
            "charges",
            "pending_session_profit",
            "net_pnl",
        ):
            try:
                fresh[key] = float(fresh.get(key) or 0)
            except (TypeError, ValueError):
                fresh[key] = 0.0
        if not fresh.get("updated_at"):
            fresh["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return self._refresh_totals(fresh)

    def _refresh_totals(self, data: dict) -> dict:
        data["available_cash"] = float(data.get("available_balance") or 0)
        data["net_pnl"] = float(data.get("realized_pnl") or 0) + float(data.get("unrealized_pnl") or 0)
        data["equity"] = (
            float(data.get("available_balance") or 0)
            + float(data.get("used_margin") or 0)
            + float(data.get("unrealized_pnl") or 0)
            + float(data.get("pending_session_profit") or 0)
        )
        return data
