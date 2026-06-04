from __future__ import annotations

from typing import Any
from uuid import uuid4

from options_auto.core.clock import iso_now


class PositionManager:
    def open_from_fill(self, trade_plan: dict[str, Any], entry_order: dict[str, Any]) -> dict[str, Any]:
        plan = dict(trade_plan or {})
        order = dict(entry_order or {})
        planned_entry = float(plan.get("entry_price") or order.get("price") or 0)
        average_price = float(order.get("average_price") or order.get("price") or planned_entry or 0)
        quantity = int(order.get("filled_quantity") or order.get("quantity") or plan.get("quantity") or 0)
        stop_distance = max(0.05, planned_entry - float(plan.get("stoploss") or 0)) if planned_entry else 0.0
        target_distance = max(0.05, float(plan.get("target") or planned_entry) - planned_entry) if planned_entry else 0.0
        return {
            "trade_id": plan.get("trade_id") or f"OA-REAL-{uuid4().hex[:10].upper()}",
            "tradingsymbol": plan.get("tradingsymbol") or order.get("tradingsymbol"),
            "side": plan.get("side"),
            "status": "POSITION_ACTIVE",
            "average_price": average_price,
            "entry_price": average_price,
            "quantity": quantity,
            "lot_size": int(plan.get("lot_size") or 1),
            "stoploss": round(max(0.05, average_price - stop_distance), 2),
            "target": round(average_price + target_distance, 2),
            "entry_order_id": order.get("order_id"),
            "target_order_id": plan.get("target_order_id") or "",
            "stoploss_order_id": plan.get("stoploss_order_id") or "",
            "oco_active": bool(plan.get("target_order_id") and plan.get("stoploss_order_id")),
            "position_protected": bool(plan.get("target_order_id") and plan.get("stoploss_order_id")),
            "opened_at": iso_now(),
            "stoploss_modifications": [],
            "partial_exits": [],
        }

    def apply_exit_decision(self, trade: dict[str, Any], decision: dict[str, Any], market: dict[str, Any] | None = None) -> dict[str, Any]:
        updated = dict(trade or {})
        decision = dict(decision or {})
        market = dict(market or {})
        events = []
        old_stoploss = float(updated.get("stoploss") or 0)
        new_stoploss = float(decision.get("new_stoploss") or old_stoploss)
        if decision.get("stoploss_change") and new_stoploss > old_stoploss:
            updated["stoploss"] = round(new_stoploss, 2)
            updated["last_stoploss_modified_epoch"] = market.get("now_epoch")
            event = {"timestamp": iso_now(), "event": "STOPLOSS_MODIFIED", "old_stoploss": old_stoploss, "new_stoploss": updated["stoploss"]}
            updated.setdefault("stoploss_modifications", []).append(event)
            events.append(event)
        partial_quantity = int(decision.get("partial_quantity") or 0)
        if decision.get("action") == "PARTIAL_EXIT" and partial_quantity > 0:
            current_quantity = int(updated.get("quantity") or 0)
            exit_quantity = min(partial_quantity, current_quantity)
            updated["quantity"] = current_quantity - exit_quantity
            event = {"timestamp": iso_now(), "event": "PARTIAL_EXIT", "quantity": exit_quantity}
            updated.setdefault("partial_exits", []).append(event)
            events.append(event)
        if decision.get("action") in {"STOPLOSS_EXIT", "TARGET_EXIT", "THETA_EXIT", "IV_CRUSH_EXIT", "END_OF_DAY_EXIT", "TIME_EXIT", "REVERSAL_EXIT"}:
            updated["status"] = "EXIT_PENDING"
            updated["exit_reason"] = decision.get("action")
            events.append({"timestamp": iso_now(), "event": "EXIT_PENDING", "reason": decision.get("action")})
        return {"trade": updated, "events": events}

    def validate_add_quantity(self, trade: dict[str, Any], add_quantity: int, market: dict[str, Any]) -> dict[str, Any]:
        trade = dict(trade or {})
        market = dict(market or {})
        add_quantity = int(add_quantity or 0)
        if add_quantity <= 0:
            return {"allowed": True, "blockers": [], "new_quantity": int(trade.get("quantity") or 0)}
        current_ltp = float(market.get("ltp") or market.get("last_price") or 0)
        average_price = float(trade.get("average_price") or trade.get("entry_price") or 0)
        if current_ltp and average_price and current_ltp < average_price:
            return {
                "allowed": False,
                "blockers": ["Averaging down a losing option position is not allowed."],
                "new_quantity": int(trade.get("quantity") or 0),
            }
        return {
            "allowed": True,
            "blockers": [],
            "new_quantity": int(trade.get("quantity") or 0) + add_quantity,
        }
