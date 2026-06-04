from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from options_auto.core.clock import iso_now
from options_auto.execution.paper_broker import PaperBroker


@dataclass
class PaperLifecycleEngine:
    broker: PaperBroker
    pending_approval: dict[str, Any] | None = None
    active_trades: list[dict[str, Any]] = field(default_factory=list)
    closed_trades: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    charge_per_order: float = 20.0

    def create_pending(self, decision: dict[str, Any], timeout_seconds: int = 30, now_epoch: float | None = None) -> dict[str, Any]:
        if not decision.get("allowed"):
            raise ValueError("Cannot create paper approval for a blocked Options Auto decision.")
        now_epoch = time.time() if now_epoch is None else float(now_epoch)
        plan = dict(decision.get("trade_plan") or {})
        if not plan:
            raise ValueError("Trade plan is missing.")
        self.pending_approval = {
            "approval_id": f"OA-APP-{uuid4().hex[:10].upper()}",
            "status": "APPROVAL_PENDING",
            "created_at": iso_now(),
            "expires_at_epoch": now_epoch + int(timeout_seconds),
            "timeout_seconds": int(timeout_seconds),
            "decision": decision,
            "trade_plan": plan,
        }
        self.events.append({"timestamp": iso_now(), "event": "APPROVAL_PENDING", "approval_id": self.pending_approval["approval_id"]})
        return dict(self.pending_approval)

    def approve(self, approval_id: str | None = None, now_epoch: float | None = None) -> dict[str, Any]:
        pending = self._pending_or_error(approval_id)
        now_epoch = time.time() if now_epoch is None else float(now_epoch)
        if now_epoch > float(pending["expires_at_epoch"]):
            pending["status"] = "EXPIRED"
            self.events.append({"timestamp": iso_now(), "event": "APPROVAL_EXPIRED", "approval_id": pending["approval_id"]})
            self.pending_approval = None
            return {"status": "EXPIRED", "message": "Trade expired, scanning new setup."}
        plan = dict(pending["trade_plan"])
        entry_order = self.broker.place_limit_buy(plan["tradingsymbol"], int(plan["quantity"]), float(plan["entry_price"]))
        self.broker.apply_charges(self.charge_per_order, "paper entry charges", entry_order["order_id"])
        target_order = self._paper_open_order(plan, "TARGET")
        stoploss_order = self._paper_open_order(plan, "STOPLOSS")
        trade = {
            "trade_id": f"OA-PAPER-{uuid4().hex[:10].upper()}",
            "status": "ACTIVE",
            "tradingsymbol": plan["tradingsymbol"],
            "side": plan.get("side"),
            "quantity": int(plan["quantity"]),
            "lot_size": int(plan.get("lot_size") or 1),
            "entry_price": float(entry_order["average_price"]),
            "stoploss": float(plan["stoploss"]),
            "target": float(plan["target"]),
            "entry_order_id": entry_order["order_id"],
            "target_order_id": target_order["order_id"],
            "stoploss_order_id": stoploss_order["order_id"],
            "oco_active": True,
            "position_protected": True,
            "opened_at": iso_now(),
            "last_ltp": float(entry_order["average_price"]),
        }
        self.active_trades.append(trade)
        self.pending_approval = None
        self.events.append({"timestamp": iso_now(), "event": "PAPER_TRADE_ACTIVE", "trade_id": trade["trade_id"]})
        return {"status": "APPROVED", "entry_order": entry_order, "target_order": target_order, "stoploss_order": stoploss_order, "trade": dict(trade)}

    def reject(self, approval_id: str | None = None) -> dict[str, Any]:
        pending = self._pending_or_error(approval_id)
        pending["status"] = "REJECTED"
        self.pending_approval = None
        self.events.append({"timestamp": iso_now(), "event": "APPROVAL_REJECTED", "approval_id": pending["approval_id"]})
        return {"status": "REJECTED", "approval_id": pending["approval_id"]}

    def process_market(self, market: dict[str, Any]) -> dict[str, Any]:
        updates = []
        remaining = []
        for trade in self.active_trades:
            update = self._update_trade(trade, market)
            updates.append(update)
            if update.get("closed"):
                self.closed_trades.append(update["trade"])
            else:
                remaining.append(update["trade"])
        self.active_trades = remaining
        return {"updates": updates, "snapshot": self.snapshot()}

    def update_stoploss(self, trade_id: str, new_stoploss: float) -> dict[str, Any]:
        for trade in self.active_trades:
            if trade.get("trade_id") != trade_id:
                continue
            old_stoploss = float(trade.get("stoploss") or 0)
            new_stoploss = max(old_stoploss, float(new_stoploss))
            trade["stoploss"] = round(new_stoploss, 2)
            self.events.append({"timestamp": iso_now(), "event": "PAPER_SL_MODIFIED", "trade_id": trade_id, "old_stoploss": old_stoploss, "new_stoploss": trade["stoploss"]})
            return {"status": "MODIFIED", "trade": dict(trade)}
        return {"status": "NOT_FOUND", "trade_id": trade_id}

    def partial_exit(self, trade_id: str, quantity: int, exit_price: float, reason: str = "PARTIAL_EXIT") -> dict[str, Any]:
        for trade in self.active_trades:
            if trade.get("trade_id") != trade_id:
                continue
            quantity = min(int(quantity), int(trade.get("quantity") or 0))
            if quantity <= 0 or quantity >= int(trade.get("quantity") or 0):
                return {"status": "IGNORED", "reason": "Partial exit quantity is invalid."}
            order = self.broker.place_limit_sell(trade["tradingsymbol"], quantity, float(exit_price))
            self.broker.apply_charges(self.charge_per_order, "paper partial exit charges", order["order_id"])
            trade["quantity"] = int(trade["quantity"]) - quantity
            event = {"timestamp": iso_now(), "event": reason, "trade_id": trade_id, "quantity": quantity, "exit_order_id": order["order_id"]}
            self.events.append(event)
            return {"status": "PARTIAL_EXIT", "event": event, "trade": dict(trade)}
        return {"status": "NOT_FOUND", "trade_id": trade_id}

    def force_exit(self, trade_id: str, exit_price: float, reason: str) -> dict[str, Any]:
        remaining = []
        closed = None
        for trade in self.active_trades:
            if trade.get("trade_id") == trade_id:
                closed = self._close_trade(dict(trade), float(exit_price), reason)
                self.closed_trades.append(closed["trade"])
            else:
                remaining.append(trade)
        self.active_trades = remaining
        return closed or {"closed": False, "action": "NOT_FOUND", "trade_id": trade_id}

    def _update_trade(self, trade: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
        trade = dict(trade)
        ltp = float(market.get("ltp") or market.get("last_price") or trade.get("last_ltp") or trade["entry_price"])
        high = float(market.get("high") or ltp)
        low = float(market.get("low") or ltp)
        trade["last_ltp"] = ltp
        if low <= float(trade["stoploss"]):
            return self._close_trade(trade, float(trade["stoploss"]), "STOPLOSS")
        if high >= float(trade["target"]):
            return self._close_trade(trade, float(trade["target"]), "TARGET")
        if market.get("move_sl_to_breakeven"):
            trade["stoploss"] = max(float(trade["stoploss"]), float(trade["entry_price"]))
            self.events.append({"timestamp": iso_now(), "event": "MOVE_SL_TO_BREAKEVEN", "trade_id": trade["trade_id"], "stoploss": trade["stoploss"]})
        if market.get("trail_stoploss"):
            trade["stoploss"] = max(float(trade["stoploss"]), float(market["trail_stoploss"]))
            self.events.append({"timestamp": iso_now(), "event": "TRAIL_SL", "trade_id": trade["trade_id"], "stoploss": trade["stoploss"]})
        return {"closed": False, "action": "HOLD", "trade": trade}

    def _close_trade(self, trade: dict[str, Any], exit_price: float, reason: str) -> dict[str, Any]:
        exit_order = self.broker.place_limit_sell(trade["tradingsymbol"], int(trade["quantity"]), float(exit_price))
        self.broker.apply_charges(self.charge_per_order, f"paper {reason.lower()} exit charges", exit_order["order_id"])
        gross = (float(exit_price) - float(trade["entry_price"])) * int(trade["quantity"])
        charges = self.charge_per_order * 2
        trade.update({
            "status": "CLOSED",
            "exit_price": float(exit_price),
            "exit_reason": reason,
            "exit_order_id": exit_order["order_id"],
            "oco_active": False,
            "position_protected": False,
            "closed_at": iso_now(),
            "pnl_gross": round(gross, 2),
            "charges": round(charges, 2),
            "pnl_net": round(gross - charges, 2),
        })
        self.events.append({"timestamp": iso_now(), "event": f"{reason}_EXIT", "trade_id": trade["trade_id"], "exit_order_id": exit_order["order_id"]})
        return {"closed": True, "action": reason, "trade": trade}

    def _paper_open_order(self, plan: dict[str, Any], kind: str) -> dict[str, Any]:
        price = float(plan["target"] if kind == "TARGET" else plan["stoploss"])
        order = {
            "order_id": f"PAPER-{kind}-{uuid4().hex[:8].upper()}",
            "tradingsymbol": plan["tradingsymbol"],
            "transaction_type": "SELL",
            "quantity": int(plan["quantity"]),
            "price": price,
            "status": "OPEN",
            "order_type": "LIMIT" if kind == "TARGET" else "SL",
            "created_at": iso_now(),
            "tag": "OPTIONS_AUTO_PAPER",
        }
        self.broker.orders.append(order)
        return order

    def _pending_or_error(self, approval_id: str | None) -> dict[str, Any]:
        if not self.pending_approval:
            raise ValueError("No Options Auto paper approval is pending.")
        if approval_id and approval_id != self.pending_approval["approval_id"]:
            raise ValueError("Approval id does not match the pending Options Auto trade.")
        return self.pending_approval

    def snapshot(self) -> dict[str, Any]:
        return {
            "pending_approval": self.pending_approval,
            "active_trades": self.active_trades,
            "closed_trades": self.closed_trades[-100:],
            "events": self.events[-100:],
            "account": self.broker.snapshot(),
        }
