from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .active_trade_manager import (
    ACTION_FULL_EXIT,
    ACTION_HOLD,
    ACTION_MODIFY_TARGET,
    ACTION_MOVE_SL_TO_BREAKEVEN,
    ACTION_NO_ACTION,
    ACTION_PARTIAL_EXIT,
    ACTION_TIGHTEN_SL,
    ACTION_TRAIL_SL,
    ActiveTradeManager,
)
from .constants import MODE_REAL, ORDER_LIMIT_ONLY, SIDE_LONG, SIDE_SHORT, SIMULATED_ORDER_MODES
from .execution_safeguards import (
    normalize_order_request_prices,
    tick_size_from_instrument,
    validate_stoploss_limit_relationship,
)
from .margin_engine import calculate_intraday_equity_quantity
from .order_request import emergency_exit_order, entry_order, stoploss_order, target_order
from .stoploss_pricing import stoploss_limit_prices


class IntradayOrderLifecycle:
    def __init__(self, broker, database, settings, session_id: str, audit=None, instrument_rows: dict[str, dict[str, Any]] | None = None):
        self.broker = broker
        self.database = database
        self.settings = settings
        self.session_id = session_id
        self.audit = audit
        self.instrument_rows = instrument_rows or {}
        self.active_trade: dict[str, Any] | None = None
        self.active_trades: dict[str, dict[str, Any]] = {}
        self.active_manager = ActiveTradeManager(settings)
        self.order_history: list[dict[str, Any]] = []
        self.session_realized_pnl = 0.0
        self.session_unrealized_pnl = 0.0
        self.real_freeze_reason = ""
        self._local_counter = 0

    def submit_entry(self, signal, quantity: int | None = None) -> dict:
        health_blockers = self._broker_health_blockers()
        if health_blockers:
            return self._blocked(health_blockers[0], None, {"margin_validation_status": "FAILED", "rejection_reason": health_blockers[0]})
        tick_size = self._tick_size(signal.symbol, signal.exchange)
        signal.entry_price = self._round_price(signal.entry_price, tick_size)
        signal.stoploss = self._round_price(signal.stoploss, tick_size)
        signal.target = self._round_price(signal.target, tick_size)
        funds = self.broker.get_funds()
        margin = calculate_intraday_equity_quantity(
            signal.symbol,
            signal.exchange,
            signal.side,
            signal.entry_price,
            signal.stoploss,
            funds.get("available"),
            self.settings.max_capital_allocation_pct,
            self.settings.risk_per_trade_pct,
            estimated_leverage=self.settings.estimated_leverage,
            mode=self.settings.mode,
            margin_calculator=self.broker.calculate_margin if self.settings.mode == MODE_REAL else None,
            user_max_quantity=quantity or self.settings.max_quantity_per_trade or None,
            session_id=self.session_id,
        )
        self.database.save_margin_check(self.session_id, self.settings, signal, margin)
        if margin["final_quantity"] <= 0 or margin["margin_validation_status"] != "PASSED":
            return self._blocked(margin.get("rejection_reason") or "Margin validation failed.", None, margin)
        quantity = int(margin["final_quantity"])
        request = entry_order(
            signal.symbol,
            signal.side,
            quantity,
            signal.entry_price,
            exchange=signal.exchange,
            session_id=self.session_id,
            order_mode=ORDER_LIMIT_ONLY,
        )
        request = normalize_order_request_prices(request, tick_size)
        request.validate(market_orders_enabled=False)
        try:
            existing = self._matching_real_order(request, tick_size)
        except Exception as exc:
            return self._blocked(
                f"Order-book idempotency check failed before broker send; real order blocked: {exc}",
                request,
                margin,
            )
        if existing:
            order = self._order_row(
                request,
                signal.side,
                response=existing,
                status=existing.get("status", "OPEN"),
                status_message="Matched existing broker order by tag/session before send; duplicate send avoided.",
                margin_required=margin.get("actual_required_margin") or margin.get("estimated_required_margin"),
            )
            order["signal_stoploss"] = signal.stoploss
            order["signal_target"] = signal.target
            order["setup_name"] = signal.setup_name
            order["score"] = signal.score
            order["margin"] = margin
            self._record_order(order)
            return {"ok": True, "order": order, "response": existing, "margin": margin, "broker_margin": {"reconciled": True}}
        try:
            broker_margin = self.broker.calculate_margin(request)
        except Exception as exc:
            margin["margin_validation_status"] = "FAILED"
            margin["rejection_reason"] = "Margin validation failed immediately before order send."
            margin["raw_margin_response"] = {"error": str(exc)}
            self.database.save_margin_check(self.session_id, self.settings, signal, margin)
            return self._blocked("Margin validation failed immediately before order send.", request, margin)
        if not broker_margin.get("ok", True):
            margin["margin_validation_status"] = "FAILED"
            margin["rejection_reason"] = "Insufficient margin before order send."
            margin["raw_margin_response"] = broker_margin
            self.database.save_margin_check(self.session_id, self.settings, signal, margin)
            return self._blocked("Insufficient margin before order send.", request, margin)
        try:
            response = self.broker.place_order(request)
        except Exception as exc:
            existing = self._matching_real_order(request, tick_size, swallow_errors=True)
            if existing:
                response = {**existing, "reconciled_after_error": True, "placement_error": str(exc)}
            else:
                return self._blocked(
                    "Order placement failed and no matching broker order was found; real trading paused for safety.",
                    request,
                    {**margin, "margin_validation_status": "FAILED", "rejection_reason": str(exc)},
                )
        order = self._order_row(
            request,
            signal.side,
            response=response,
            status=response.get("status", "PENDING"),
            status_message=f"Entry LIMIT sent. Required margin {float(margin.get('actual_required_margin') or margin.get('estimated_required_margin') or 0):.2f}; available {float(margin.get('available_funds') or 0):.2f}.",
            margin_required=margin.get("actual_required_margin") or margin.get("estimated_required_margin"),
        )
        order["signal_stoploss"] = signal.stoploss
        order["signal_target"] = signal.target
        order["setup_name"] = signal.setup_name
        order["score"] = signal.score
        order["margin"] = margin
        self._record_order(order)
        return {"ok": True, "order": order, "response": response, "margin": margin, "broker_margin": broker_margin}

    def process_market_data(
        self,
        market_data: dict[str, Any] | None = None,
        now: datetime | None = None,
        snapshots: list[Any] | dict[str, Any] | None = None,
    ) -> None:
        now = now or datetime.now()
        market_data = market_data or {}
        if self.settings.mode == MODE_REAL:
            self._reconcile_real_orders(now)
            self._process_active_trades(market_data, now, snapshots)
            return
        if self.settings.mode in SIMULATED_ORDER_MODES:
            self._process_pending_entries(market_data, now)
            self._process_active_trades(market_data, now, snapshots)

    def close_active_trade(self, reason: str, exit_price: float, now: datetime | None = None) -> None:
        if self.active_trade and self.active_trade.get("status") == "OPEN":
            self._close_trade(reason, float(exit_price), now or datetime.now(), trade=self.active_trade)

    def _process_pending_entries(self, market_data: dict[str, Any], now: datetime) -> None:
        for order in list(self.order_history):
            if order.get("role") != "ENTRY" or order.get("status") != "PENDING":
                continue
            symbol = order["symbol"]
            latest = self._latest_bar(market_data.get(symbol) or {})
            timeout_seconds = max(1, int(getattr(self.settings, "limit_order_timeout_seconds", 60) or 60))
            if now - self._parse_time(order.get("created_at")) >= timedelta(seconds=timeout_seconds):
                cancel = self.broker.cancel_order(order.get("broker_order_id"))
                order["status"] = "CANCELLED"
                order["updated_at"] = now.isoformat(timespec="seconds")
                order["status_message"] = f"Entry LIMIT unfilled for {timeout_seconds} seconds; cancellation confirmed."
                self.database.update_order_status(order["local_order_id"], "CANCELLED", order["status_message"], cancel)
                if hasattr(self.database, "save_order_event"):
                    self.database.save_order_event(self.session_id, order, "CANCELLED", order["status_message"], cancel)
                continue
            if latest and self._entry_touched(order, latest):
                fill_price = float(order["price"])
                order["status"] = "COMPLETE"
                order["updated_at"] = now.isoformat(timespec="seconds")
                order["status_message"] = self._paper_fill_message(order)
                self.database.update_order_status(order["local_order_id"], "COMPLETE", order["status_message"], {"fill_price": fill_price, "paper_fill_model": getattr(self.settings, "paper_fill_model", "CANDLE_TOUCH_CONSERVATIVE")})
                if hasattr(self.database, "save_order_event"):
                    self.database.save_order_event(self.session_id, order, "COMPLETE", order["status_message"], {"fill_price": fill_price, "paper_fill_model": getattr(self.settings, "paper_fill_model", "CANDLE_TOUCH_CONSERVATIVE")})
                self._open_trade(order, fill_price, latest, now)

    def _process_active_trades(
        self,
        market_data: dict[str, Any],
        now: datetime,
        snapshots: list[Any] | dict[str, Any] | None = None,
    ) -> None:
        open_trades = self._open_trades()
        if not open_trades:
            self.session_unrealized_pnl = 0.0
            if hasattr(self.broker, "account_store") and self.broker.account_store:
                self.broker.account_store.mark_to_market(position_value=0.0, unrealized_pnl=0.0)
            return
        snapshots_by_symbol = self._snapshots_by_symbol(snapshots)
        total_unrealized = 0.0
        total_position_value = 0.0
        for trade in list(open_trades):
            if trade.get("status") != "OPEN":
                continue
            latest = self._latest_bar(market_data.get(trade["symbol"]) or {})
            if not latest:
                continue
            ltp = float(latest.get("close") or trade["entry_price"])
            quantity = int(trade.get("quantity") or 0)
            if trade["side"] == SIDE_LONG:
                unrealized = (ltp - trade["entry_price"]) * quantity
                stop_hit = float(latest.get("low") or ltp) <= trade["stoploss_trigger"]
                target_hit = float(latest.get("high") or ltp) >= trade["target"]
            else:
                unrealized = (trade["entry_price"] - ltp) * quantity
                stop_hit = float(latest.get("high") or ltp) >= trade["stoploss_trigger"]
                target_hit = float(latest.get("low") or ltp) <= trade["target"]
            trade["unrealized_pnl"] = unrealized
            trade["last_ltp"] = ltp
            total_unrealized += unrealized
            total_position_value += ltp * quantity
            if stop_hit:
                self._close_trade("STOPLOSS", trade["stoploss_limit"], now, trade=trade)
                continue
            if target_hit:
                self._close_trade("TARGET", trade["target"], now, trade=trade)
                continue
            snapshot = snapshots_by_symbol.get(str(trade.get("symbol") or "").upper())
            decision = self.active_manager.evaluate(trade, snapshot=snapshot, market_row=market_data.get(trade["symbol"]) or {}, now=now)
            self._save_trade_health(trade, decision, now)
            self._apply_management_decision(trade, decision, now)
        open_trades = self._open_trades()
        total_unrealized = sum(float(trade.get("unrealized_pnl") or 0) for trade in open_trades)
        total_position_value = sum(float(trade.get("last_ltp") or trade.get("entry_price") or 0) * int(trade.get("quantity") or 0) for trade in open_trades)
        self.session_unrealized_pnl = total_unrealized
        if hasattr(self.broker, "account_store") and self.broker.account_store:
            self.broker.account_store.mark_to_market(
                position_value=total_position_value,
                unrealized_pnl=self.session_unrealized_pnl,
            )
        self._refresh_active_trade_reference()

    def _open_trade(self, entry_order_row: dict, fill_price: float, _latest: dict, now: datetime, filled_quantity: int | None = None) -> None:
        exit_plan = self._exit_plan_from_fill(entry_order_row, fill_price, _latest)
        stop_prices = stoploss_limit_prices(entry_order_row["side"], exit_plan["stoploss"], self.settings.stoploss_buffer)
        quantity = int(filled_quantity or entry_order_row["quantity"])
        target = float(exit_plan["target"])
        stop = stoploss_order(
            entry_order_row["symbol"],
            entry_order_row["side"],
            quantity,
            trigger_price=stop_prices["trigger_price"],
            limit_price=stop_prices["limit_price"],
            exchange=entry_order_row["exchange"],
            session_id=self.session_id,
        )
        tick_size = self._tick_size(entry_order_row["symbol"], entry_order_row["exchange"])
        stop = normalize_order_request_prices(stop, tick_size)
        stop_blockers = validate_stoploss_limit_relationship(stop, tick_size)
        if stop_blockers:
            raise ValueError("; ".join(stop_blockers))
        target_req = target_order(
            entry_order_row["symbol"],
            entry_order_row["side"],
            quantity,
            target_price=target,
            exchange=entry_order_row["exchange"],
            session_id=self.session_id,
        )
        target_req = normalize_order_request_prices(target_req, tick_size)
        trade_id = f"{self.session_id}-{entry_order_row['local_order_id']}"
        trade = {
            "trade_id": trade_id,
            "session_id": self.session_id,
            "symbol": entry_order_row["symbol"],
            "exchange": entry_order_row["exchange"],
            "side": entry_order_row["side"],
            "quantity": quantity,
            "original_quantity": quantity,
            "entry_time": now.isoformat(timespec="seconds"),
            "entry_price": fill_price,
            "stoploss_trigger": stop_prices["trigger_price"],
            "stoploss_limit": stop_prices["limit_price"],
            "initial_stoploss_trigger": stop_prices["trigger_price"],
            "initial_stoploss_limit": stop_prices["limit_price"],
            "target": target,
            "initial_target": target,
            "entry_order_id": entry_order_row["local_order_id"],
            "stoploss_order_id": "",
            "target_order_id": "",
            "margin_required": float(entry_order_row.get("margin_required") or fill_price * quantity),
            "status": "OPEN",
            "setup_name": entry_order_row.get("setup_name", ""),
            "score": entry_order_row.get("score", 0),
            "management": {
                "action": "OPENED",
                "health_score": 50.0,
                "partial_exit_done": False,
                "events": 0,
                "exit_plan_source": exit_plan["source"],
            },
        }
        if self.settings.mode == MODE_REAL:
            stop_row, target_row = self._place_real_protective_orders(stop, target_req, entry_order_row, trade, now)
        else:
            stop_row = self._order_row(stop, entry_order_row["side"], status="PENDING", status_message="OCO stoploss SL-LIMIT placed after entry fill.", role="STOPLOSS")
            target_row = self._order_row(target_req, entry_order_row["side"], status="PENDING", status_message="OCO target LIMIT placed after entry fill.", role="TARGET")
            self._record_order(stop_row)
            self._record_order(target_row)
        if stop_row:
            trade["stoploss_order_id"] = stop_row["local_order_id"]
            trade["stoploss_broker_order_id"] = stop_row.get("broker_order_id", "")
        if target_row:
            trade["target_order_id"] = target_row["local_order_id"]
            trade["target_broker_order_id"] = target_row.get("broker_order_id", "")
        self.active_trades[trade_id] = trade
        self.active_trade = trade
        if hasattr(self.broker, "account_store") and self.broker.account_store:
            self.broker.account_store.mark_to_market(position_value=fill_price * quantity, unrealized_pnl=0.0)
        self._record_management_event(trade, {
            "action": "OPENED",
            "health_score": 50.0,
            "r_multiple": 0.0,
            "reason": "Entry fill confirmed; protective orders initialized.",
            "details": {"fill_price": fill_price, "quantity": quantity, "exit_plan": exit_plan},
        }, now, status="APPLIED")

    def _close_trade(self, reason: str, exit_price: float, now: datetime, trade: dict[str, Any] | None = None) -> None:
        trade = trade or self.active_trade
        if not trade:
            return
        side = trade["side"]
        quantity = int(trade["quantity"])
        pnl = (float(exit_price) - trade["entry_price"]) * quantity if side == SIDE_LONG else (trade["entry_price"] - float(exit_price)) * quantity
        charges = max(1.0, abs(float(exit_price) * quantity) * 0.0003)
        self.session_realized_pnl += pnl - charges
        self.session_unrealized_pnl = 0.0
        if reason in {"TARGET", "STOPLOSS"}:
            winner_id = trade["target_order_id"] if reason == "TARGET" else trade["stoploss_order_id"]
            cancel_id = trade["stoploss_order_id"] if reason == "TARGET" else trade["target_order_id"]
            self._set_local_order_status(winner_id, "COMPLETE", f"{reason} order filled; OCO peer will be cancelled.", {"exit_price": exit_price})
            if self.settings.mode == MODE_REAL:
                self._cancel_broker_order(cancel_id, f"OCO peer cancelled because {reason} filled.")
            self._set_local_order_status(cancel_id, "CANCELLED", f"OCO peer cancelled because {reason} filled.", {"oco_filled": winner_id})
        else:
            for local_id in (trade.get("stoploss_order_id"), trade.get("target_order_id")):
                if self.settings.mode == MODE_REAL:
                    self._cancel_broker_order(local_id, f"Protective order cancelled because {reason} closed the trade.")
                self._set_local_order_status(local_id, "CANCELLED", f"Protective order cancelled because {reason} closed the trade.", {"exit_price": exit_price})
        if hasattr(self.broker, "account_store") and self.broker.account_store:
            if hasattr(self.broker.account_store, "apply_trade_settlement"):
                self.broker.account_store.apply_trade_settlement(
                    pnl,
                    release_margin=trade["margin_required"],
                    charges=charges,
                    defer_positive_profit=True,
                )
            else:
                self.broker.account_store.apply_realized_pnl(pnl, release_margin=trade["margin_required"], charges=charges)
            self.broker.account_store.mark_to_market(position_value=0.0, unrealized_pnl=0.0)
        trade.update({
            "exit_time": now.isoformat(timespec="seconds"),
            "exit_price": float(exit_price),
            "pnl_gross": pnl,
            "charges": charges,
            "pnl_net": pnl - charges,
            "exit_reason": reason,
            "status": "CLOSED",
        })
        self._record_management_event(trade, {
            "action": "FULL_EXIT",
            "health_score": (trade.get("management") or {}).get("health_score", 0),
            "r_multiple": (trade.get("management") or {}).get("r_multiple", 0),
            "exit_price": float(exit_price),
            "reason": reason,
            "details": {"pnl_net": pnl - charges},
        }, now, status="APPLIED")
        self.database.save_trade({
            "session_id": self.session_id,
            "symbol": trade["symbol"],
            "side": trade["side"],
            "quantity": quantity,
            "entry_time": trade["entry_time"],
            "entry_price": trade["entry_price"],
            "exit_time": trade["exit_time"],
            "exit_price": trade["exit_price"],
            "stoploss": trade["stoploss_trigger"],
            "target": trade["target"],
            "pnl_gross": pnl,
            "charges": charges,
            "pnl_net": pnl - charges,
            "exit_reason": reason,
            "setup_name": trade.get("setup_name", ""),
            "score": trade.get("score", 0),
            "result": "WIN" if pnl > 0 else "LOSS",
        })
        self.active_trades[trade.get("trade_id") or trade.get("entry_order_id") or trade["symbol"]] = trade
        self._refresh_active_trade_reference()

    def _apply_management_decision(self, trade: dict[str, Any], decision, now: datetime) -> None:
        data = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
        action = data.get("action") or ACTION_NO_ACTION
        management = trade.setdefault("management", {})
        management.update({
            "action": action,
            "health_score": data.get("health_score", 0),
            "r_multiple": data.get("r_multiple", 0),
            "reason": data.get("reason", ""),
            "opposite_signal_score": data.get("opposite_signal_score", 0),
            "last_checked_at": now.isoformat(timespec="seconds"),
        })
        if action in {ACTION_HOLD, ACTION_NO_ACTION}:
            return
        if action == ACTION_PARTIAL_EXIT:
            self._partial_exit(trade, int(data.get("partial_quantity") or 0), float(data.get("exit_price") or trade.get("last_ltp") or trade["entry_price"]), data, now)
            return
        if action == ACTION_FULL_EXIT:
            if data.get("require_user_confirmation") and self.settings.mode == MODE_REAL:
                self._record_management_event(trade, data, now, status="WAITING_CONFIRMATION")
                return
            if self.settings.mode == MODE_REAL:
                self._request_real_full_exit(trade, data, now, exit_reason="ACTIVE_MANAGER_EXIT", role="ACTIVE_EXIT")
                return
            self._close_trade("ACTIVE_MANAGER_EXIT", float(data.get("exit_price") or trade.get("last_ltp") or trade["entry_price"]), now, trade=trade)
            return
        if action in {ACTION_MOVE_SL_TO_BREAKEVEN, ACTION_TRAIL_SL, ACTION_TIGHTEN_SL}:
            new_stop = data.get("new_stoploss")
            if new_stop is not None:
                applied = self._modify_stoploss(trade, float(new_stop), data, now)
                self._record_management_event(trade, data, now, status="APPLIED" if applied else "SKIPPED")
            return
        if action == ACTION_MODIFY_TARGET:
            new_target = data.get("new_target")
            if new_target is not None:
                applied = self._modify_target(trade, float(new_target), data, now)
                self._record_management_event(trade, data, now, status="APPLIED" if applied else "SKIPPED")

    def _save_trade_health(self, trade: dict[str, Any], decision, now: datetime) -> None:
        data = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
        if hasattr(self.database, "save_trade_health"):
            self.database.save_trade_health(self.session_id, {
                "timestamp": now.isoformat(timespec="seconds"),
                "symbol": trade.get("symbol", ""),
                "health_score": data.get("health_score", 0),
                "recommendation": data.get("action", ""),
                "dynamic_sl": data.get("new_stoploss") or trade.get("stoploss_trigger"),
                "target": data.get("new_target") or trade.get("target"),
                "opposite_signal_score": data.get("opposite_signal_score", 0),
                "invalidation_status": data.get("invalidation_status", "VALID"),
                "details": data.get("details") or {},
            })

    def _modify_stoploss(self, trade: dict[str, Any], new_trigger: float, decision: dict[str, Any], now: datetime) -> bool:
        side = str(trade.get("side") or "").upper()
        current_trigger = float(trade.get("stoploss_trigger") or 0)
        if self._modification_throttled(trade, "last_sl_modified_at", getattr(self.settings, "min_seconds_between_sl_modifications", 15), now):
            decision.setdefault("details", {})["throttle"] = "SL modification skipped due to throttle."
            return False
        if side == SIDE_LONG and new_trigger <= current_trigger:
            return False
        if side == SIDE_SHORT and new_trigger >= current_trigger:
            return False
        tick_size = self._tick_size(trade["symbol"], trade["exchange"])
        stop_prices = stoploss_limit_prices(side, new_trigger, self.settings.stoploss_buffer)
        stop = stoploss_order(
            trade["symbol"],
            side,
            int(trade["quantity"]),
            trigger_price=stop_prices["trigger_price"],
            limit_price=stop_prices["limit_price"],
            exchange=trade["exchange"],
            session_id=self.session_id,
        )
        stop = normalize_order_request_prices(stop, tick_size)
        blockers = validate_stoploss_limit_relationship(stop, tick_size)
        if blockers:
            decision.setdefault("details", {})["validation_blockers"] = blockers
            return False
        response = {"local_only": True}
        broker_order_id = str(trade.get("stoploss_broker_order_id") or "")
        if self.settings.mode == MODE_REAL:
            if not broker_order_id:
                self._freeze_real_orders("Cannot modify stoploss because real SL broker order id is missing.")
                return False
            try:
                response = self.broker.modify_order(
                    broker_order_id,
                    {
                        "price": stop.price,
                        "trigger_price": stop.trigger_price,
                        "quantity": int(trade["quantity"]),
                        "order_type": "SL",
                    },
                )
            except Exception as exc:
                self._freeze_real_orders(f"Real stoploss modification failed: {exc}")
                return False
        old_trigger = trade["stoploss_trigger"]
        old_limit = trade["stoploss_limit"]
        trade["stoploss_trigger"] = float(stop.trigger_price)
        trade["stoploss_limit"] = float(stop.price)
        trade.setdefault("management", {})["last_sl_modified_at"] = now.isoformat(timespec="seconds")
        trade.setdefault("management", {})["previous_stoploss"] = old_trigger
        self._update_local_order_prices(
            trade.get("stoploss_order_id", ""),
            price=float(stop.price),
            trigger_price=float(stop.trigger_price),
            status_message=f"Active manager modified SL from {old_trigger:.2f}/{old_limit:.2f}.",
            response=response,
        )
        return True

    def _modify_target(self, trade: dict[str, Any], new_target: float, decision: dict[str, Any], now: datetime) -> bool:
        side = str(trade.get("side") or "").upper()
        current_target = float(trade.get("target") or 0)
        if self._modification_throttled(trade, "last_target_modified_at", getattr(self.settings, "min_seconds_between_target_modifications", 15), now):
            decision.setdefault("details", {})["throttle"] = "Target modification skipped due to throttle."
            return False
        if side == SIDE_LONG and new_target <= current_target:
            return False
        if side == SIDE_SHORT and new_target >= current_target:
            return False
        tick_size = self._tick_size(trade["symbol"], trade["exchange"])
        target_req = target_order(
            trade["symbol"],
            side,
            int(trade["quantity"]),
            target_price=new_target,
            exchange=trade["exchange"],
            session_id=self.session_id,
        )
        target_req = normalize_order_request_prices(target_req, tick_size)
        response = {"local_only": True}
        broker_order_id = str(trade.get("target_broker_order_id") or "")
        if self.settings.mode == MODE_REAL:
            if not broker_order_id:
                self._freeze_real_orders("Cannot modify target because real target broker order id is missing.")
                return False
            try:
                response = self.broker.modify_order(
                    broker_order_id,
                    {
                        "price": target_req.price,
                        "quantity": int(trade["quantity"]),
                        "order_type": "LIMIT",
                    },
                )
            except Exception as exc:
                self._freeze_real_orders(f"Real target modification failed: {exc}")
                return False
        old_target = trade["target"]
        trade["target"] = float(target_req.price)
        trade.setdefault("management", {})["last_target_modified_at"] = now.isoformat(timespec="seconds")
        trade.setdefault("management", {})["previous_target"] = old_target
        self._update_local_order_prices(
            trade.get("target_order_id", ""),
            price=float(target_req.price),
            status_message=f"Active manager modified target from {old_target:.2f}.",
            response=response,
        )
        return True

    def _partial_exit(self, trade: dict[str, Any], quantity: int, exit_price: float, decision: dict[str, Any], now: datetime) -> None:
        quantity = max(0, min(int(quantity or 0), int(trade.get("quantity") or 0) - 1))
        if quantity <= 0:
            self._record_management_event(trade, decision, now, status="SKIPPED")
            return
        if self.settings.mode == MODE_REAL:
            decision["reason"] = "Real partial exit is not implemented. Full exit and SL/target management only."
            self._record_management_event(trade, decision, now, status="REAL_PARTIAL_EXIT_BLOCKED")
            return
        side = trade["side"]
        pnl = (float(exit_price) - trade["entry_price"]) * quantity if side == SIDE_LONG else (trade["entry_price"] - float(exit_price)) * quantity
        charges = max(1.0, abs(float(exit_price) * quantity) * 0.0003)
        original_quantity = max(1, int(trade.get("original_quantity") or trade.get("quantity") or 1))
        release_margin = float(trade.get("margin_required") or 0) * quantity / original_quantity
        self.session_realized_pnl += pnl - charges
        if hasattr(self.broker, "account_store") and self.broker.account_store:
            if hasattr(self.broker.account_store, "apply_trade_settlement"):
                self.broker.account_store.apply_trade_settlement(
                    pnl,
                    release_margin=release_margin,
                    charges=charges,
                    defer_positive_profit=True,
                )
            else:
                self.broker.account_store.apply_realized_pnl(pnl, release_margin=release_margin, charges=charges)
        trade["quantity"] = int(trade["quantity"]) - quantity
        trade["margin_required"] = max(0.0, float(trade.get("margin_required") or 0) - release_margin)
        trade.setdefault("management", {})["partial_exit_done"] = True
        trade.setdefault("management", {})["partial_exited_quantity"] = quantity
        trade.setdefault("management", {})["last_partial_exit_price"] = float(exit_price)
        self._update_local_order_prices(trade.get("stoploss_order_id", ""), quantity=int(trade["quantity"]), status_message="Active manager reduced SL quantity after partial exit.")
        self._update_local_order_prices(trade.get("target_order_id", ""), quantity=int(trade["quantity"]), status_message="Active manager reduced target quantity after partial exit.")
        self.database.save_trade({
            "session_id": self.session_id,
            "symbol": trade["symbol"],
            "side": trade["side"],
            "quantity": quantity,
            "entry_time": trade["entry_time"],
            "entry_price": trade["entry_price"],
            "exit_time": now.isoformat(timespec="seconds"),
            "exit_price": float(exit_price),
            "stoploss": trade["stoploss_trigger"],
            "target": trade["target"],
            "pnl_gross": pnl,
            "charges": charges,
            "pnl_net": pnl - charges,
            "exit_reason": "PARTIAL_EXIT",
            "setup_name": trade.get("setup_name", ""),
            "score": trade.get("score", 0),
            "result": "WIN" if pnl > 0 else "LOSS",
        })
        self._record_management_event(trade, decision, now, status="APPLIED")

    def _request_real_full_exit(
        self,
        trade: dict[str, Any],
        decision: dict[str, Any],
        now: datetime,
        *,
        exit_reason: str,
        role: str,
        cancel_target_first: bool = True,
    ) -> bool:
        if self.settings.mode != MODE_REAL:
            return False
        decision = dict(decision or {})
        management = trade.setdefault("management", {})
        pending = self._pending_real_exit_order(trade)
        if pending:
            management["real_exit_pending"] = True
            decision.setdefault("details", {})["pending_exit_order_id"] = pending.get("broker_order_id") or pending.get("local_order_id")
            self._record_management_event(trade, decision, now, status="REAL_EXIT_ALREADY_PENDING")
            return False
        quantity = int(trade.get("quantity") or 0)
        if quantity <= 0:
            self._record_management_event(trade, decision, now, status="REAL_EXIT_SKIPPED")
            return False
        if cancel_target_first and trade.get("target_order_id"):
            target_cancelled = self._cancel_broker_order(
                trade["target_order_id"],
                f"Target cancelled before {exit_reason} market square-off.",
            )
            if not target_cancelled:
                self._record_management_event(trade, decision, now, status="REAL_EXIT_BLOCKED")
                return False
            self._set_local_order_status(
                trade["target_order_id"],
                "CANCELLED",
                f"Target cancelled before {exit_reason} market square-off.",
                {"exit_reason": exit_reason},
            )
        net_quantity = quantity if trade.get("side") == SIDE_LONG else -quantity
        request = emergency_exit_order(
            trade["symbol"],
            net_quantity,
            exchange=trade.get("exchange") or "NSE",
            session_id=self.session_id,
            settings=self.settings,
            ltp=decision.get("exit_price") or trade.get("last_ltp") or trade.get("entry_price"),
            lower_circuit_limit=decision.get("lower_circuit_limit"),
            upper_circuit_limit=decision.get("upper_circuit_limit"),
        )
        try:
            if hasattr(self.broker, "place_emergency_order"):
                response = self.broker.place_emergency_order(request)
            else:
                response = self.broker.place_order(request)
        except Exception as exc:
            self._freeze_real_orders(f"{exit_reason} market square-off failed: {exc}")
            decision.setdefault("details", {})["exit_order_error"] = str(exc)
            self._record_management_event(trade, decision, now, status="REAL_EXIT_FAILED")
            return False
        if not isinstance(response, dict):
            response = {"order_id": str(response), "status": "PLACED"}
        order = self._order_row(
            request,
            trade.get("side", ""),
            response=response,
            status=response.get("status", "PLACED"),
            status_message=f"Real {exit_reason} {request.order_type} square-off sent; waiting for broker fill confirmation.",
            role=role,
        )
        order["parent_trade_id"] = trade.get("trade_id", "")
        self._record_order(order)
        management["real_exit_pending"] = True
        management["real_exit_reason"] = exit_reason
        management["real_exit_order_id"] = order["local_order_id"]
        management["real_exit_broker_order_id"] = order.get("broker_order_id", "")
        decision.setdefault("details", {})["exit_order_id"] = order["local_order_id"]
        decision.setdefault("details", {})["exit_broker_order_id"] = order.get("broker_order_id", "")
        self._record_management_event(trade, decision, now, status="REAL_EXIT_SENT")
        return True

    def _place_real_protective_orders(self, stop, target_req, entry_order_row: dict, trade: dict[str, Any], now: datetime) -> tuple[dict | None, dict | None]:
        stop_row = None
        target_row = None
        try:
            stop_response = self.broker.place_order(stop)
            stop_row = self._order_row(
                stop,
                entry_order_row["side"],
                response=stop_response,
                status=stop_response.get("status", "PLACED"),
                status_message="Real protective SL-LIMIT placed after confirmed entry fill.",
                role="STOPLOSS",
            )
            self._record_order(stop_row)
        except Exception as exc:
            self._freeze_real_orders(f"Real protective stoploss placement failed after entry fill: {exc}")
            stop_row = self._order_row(
                stop,
                entry_order_row["side"],
                response={"error": str(exc)},
                status="FAILED",
                status_message="Real protective stoploss placement failed; session frozen.",
                role="STOPLOSS",
            )
            self._record_order(stop_row)
            trade.setdefault("management", {})["frozen"] = True
            trade.setdefault("management", {})["freeze_reason"] = self.real_freeze_reason
            trade["stoploss_order_id"] = stop_row["local_order_id"]
            trade["stoploss_broker_order_id"] = ""
            self._request_real_full_exit(
                trade,
                {
                    "action": ACTION_FULL_EXIT,
                    "health_score": (trade.get("management") or {}).get("health_score", 0),
                    "r_multiple": (trade.get("management") or {}).get("r_multiple", 0),
                    "exit_price": trade.get("entry_price"),
                    "reason": "Real protective stoploss placement failed after entry fill; emergency square-off requested.",
                    "details": {"protective_stoploss_error": str(exc)},
                },
                now,
                exit_reason="EMERGENCY_EXIT",
                role="EMERGENCY_EXIT",
                cancel_target_first=False,
            )
            return stop_row, None
        try:
            target_response = self.broker.place_order(target_req)
            target_row = self._order_row(
                target_req,
                entry_order_row["side"],
                response=target_response,
                status=target_response.get("status", "PLACED"),
                status_message="Real target LIMIT placed after confirmed entry fill.",
                role="TARGET",
            )
            self._record_order(target_row)
        except Exception as exc:
            self._freeze_real_orders(f"Real target placement failed after SL placement: {exc}")
            target_row = self._order_row(
                target_req,
                entry_order_row["side"],
                response={"error": str(exc)},
                status="FAILED",
                status_message="Real target placement failed; hard SL remains active and session frozen.",
                role="TARGET",
            )
            self._record_order(target_row)
            trade.setdefault("management", {})["frozen"] = True
            trade.setdefault("management", {})["freeze_reason"] = self.real_freeze_reason
        return stop_row, target_row

    def _reconcile_real_orders(self, now: datetime) -> None:
        if self.settings.mode != MODE_REAL:
            return
        try:
            orders = self.broker.get_orders()
            trades = self.broker.get_trades() if hasattr(self.broker, "get_trades") else []
        except Exception as exc:
            self._freeze_real_orders(f"Real order reconciliation failed: {exc}")
            return
        by_order_id = {str(row.get("order_id") or row.get("id") or ""): row for row in orders if row.get("order_id") or row.get("id")}
        for order in list(self.order_history):
            role = order.get("role")
            if role == "ENTRY" and order.get("status") not in {"COMPLETE", "CANCELLED", "REJECTED"}:
                broker_row = by_order_id.get(str(order.get("broker_order_id") or ""))
                if not broker_row:
                    continue
                status = str(broker_row.get("status") or "").upper()
                filled_quantity = self._filled_quantity(broker_row, order, trades)
                if status in {"COMPLETE", "FILLED"} or filled_quantity > 0:
                    fill_price = self._average_fill_price(broker_row, order, trades)
                    status_label = "COMPLETE" if filled_quantity >= int(order.get("quantity") or 0) else "PARTIAL"
                    order["status"] = status_label
                    order["quantity"] = filled_quantity or int(order.get("quantity") or 0)
                    order["updated_at"] = now.isoformat(timespec="seconds")
                    order["status_message"] = f"Real entry fill reconciled from broker order book as {status_label}."
                    self.database.update_order_status(order["local_order_id"], status_label, order["status_message"], broker_row)
                    if not self._trade_for_entry(order["local_order_id"]):
                        self._open_trade(order, fill_price, {}, now, filled_quantity=order["quantity"])
                elif status in {"CANCELLED", "REJECTED"}:
                    order["status"] = status
                    order["updated_at"] = now.isoformat(timespec="seconds")
                    order["status_message"] = f"Real entry ended as {status} in broker order book."
                    self.database.update_order_status(order["local_order_id"], status, order["status_message"], broker_row)
            elif role in {"STOPLOSS", "TARGET"} and order.get("status") not in {"COMPLETE", "CANCELLED", "REJECTED"}:
                broker_row = by_order_id.get(str(order.get("broker_order_id") or ""))
                if not broker_row:
                    continue
                status = str(broker_row.get("status") or "").upper()
                if status in {"COMPLETE", "FILLED"}:
                    trade = self._trade_for_protective_order(order.get("local_order_id", ""))
                    if not trade:
                        continue
                    exit_price = self._average_fill_price(broker_row, order, trades)
                    self._close_trade("STOPLOSS" if role == "STOPLOSS" else "TARGET", exit_price, now, trade=trade)
                elif status in {"CANCELLED", "REJECTED"}:
                    order["status"] = status
                    order["updated_at"] = now.isoformat(timespec="seconds")
                    order["status_message"] = f"Real protective order ended as {status} in broker order book."
                    self.database.update_order_status(order["local_order_id"], status, order["status_message"], broker_row)
                    if role == "STOPLOSS":
                        self._freeze_real_orders("Real protective stoploss is not live; session frozen.")
            elif role in {"ACTIVE_EXIT", "EMERGENCY_EXIT"} and order.get("status") not in {"COMPLETE", "CANCELLED", "REJECTED"}:
                broker_row = by_order_id.get(str(order.get("broker_order_id") or ""))
                if not broker_row:
                    continue
                status = str(broker_row.get("status") or "").upper()
                filled_quantity = self._filled_quantity(broker_row, order, trades)
                if status in {"COMPLETE", "FILLED"} or filled_quantity >= int(order.get("quantity") or 0):
                    trade = self._trade_for_exit_order(order.get("local_order_id", ""))
                    if not trade:
                        continue
                    exit_price = self._average_fill_price(broker_row, order, trades)
                    order["status"] = "COMPLETE"
                    order["updated_at"] = now.isoformat(timespec="seconds")
                    order["status_message"] = "Real market exit confirmed from broker order book."
                    self.database.update_order_status(order["local_order_id"], "COMPLETE", order["status_message"], broker_row)
                    exit_reason = "EMERGENCY_EXIT" if role == "EMERGENCY_EXIT" else "ACTIVE_MANAGER_EXIT"
                    self._close_trade(exit_reason, exit_price, now, trade=trade)
                elif filled_quantity > 0:
                    order["status"] = "PARTIAL"
                    order["updated_at"] = now.isoformat(timespec="seconds")
                    order["status_message"] = "Real market exit is partially filled; session frozen until broker reconciliation completes."
                    self.database.update_order_status(order["local_order_id"], "PARTIAL", order["status_message"], broker_row)
                    self._freeze_real_orders(order["status_message"])
                elif status in {"CANCELLED", "REJECTED"}:
                    trade = self._trade_for_exit_order(order.get("local_order_id", ""))
                    if trade:
                        trade.setdefault("management", {})["real_exit_pending"] = False
                    order["status"] = status
                    order["updated_at"] = now.isoformat(timespec="seconds")
                    order["status_message"] = f"Real market exit ended as {status}; manual review required."
                    self.database.update_order_status(order["local_order_id"], status, order["status_message"], broker_row)
                    self._freeze_real_orders(order["status_message"])

    def _record_management_event(self, trade: dict[str, Any], decision: dict[str, Any], now: datetime, status: str = "RECORDED") -> None:
        if hasattr(decision, "to_dict"):
            decision = decision.to_dict()
        decision = dict(decision or {})
        old_stop = trade.get("stoploss_trigger")
        old_target = trade.get("target")
        row = {
            "timestamp": now.isoformat(timespec="seconds"),
            "trade_id": trade.get("trade_id", ""),
            "symbol": trade.get("symbol", ""),
            "side": trade.get("side", ""),
            "action": decision.get("action", ""),
            "health_score": decision.get("health_score", 0),
            "r_multiple": decision.get("r_multiple", 0),
            "old_stoploss": old_stop,
            "new_stoploss": decision.get("new_stoploss"),
            "old_target": old_target,
            "new_target": decision.get("new_target"),
            "exit_price": decision.get("exit_price"),
            "partial_quantity": decision.get("partial_quantity", 0),
            "broker_order_id": trade.get("stoploss_broker_order_id") or trade.get("target_broker_order_id") or "",
            "status": status,
            "reason": decision.get("reason", ""),
            "details": decision.get("details") or {},
        }
        trade.setdefault("management", {})["events"] = int(trade.setdefault("management", {}).get("events") or 0) + 1
        if hasattr(self.database, "save_trade_management_event"):
            self.database.save_trade_management_event(self.session_id, row)

    def _update_local_order_prices(
        self,
        local_id: str,
        *,
        price: float | None = None,
        trigger_price: float | None = None,
        quantity: int | None = None,
        status_message: str = "",
        response: dict | None = None,
    ) -> None:
        if not local_id:
            return
        for order in self.order_history:
            if order["local_order_id"] == local_id:
                if price is not None:
                    order["price"] = price
                if trigger_price is not None:
                    order["trigger_price"] = trigger_price
                if quantity is not None:
                    order["quantity"] = int(quantity)
                if status_message:
                    order["status_message"] = status_message
                order["updated_at"] = datetime.now().isoformat(timespec="seconds")
                if response is not None:
                    order["broker_response"] = response
                if hasattr(self.database, "save_order_event"):
                    self.database.save_order_event(self.session_id, order, "MODIFIED", status_message, response or {})
                break
        if hasattr(self.database, "update_order_prices"):
            self.database.update_order_prices(
                local_id,
                price=price,
                trigger_price=trigger_price,
                quantity=quantity,
                status_message=status_message,
                broker_response=response,
            )

    def _cancel_broker_order(self, local_id: str, message: str) -> bool:
        order = self._order_by_local_id(local_id)
        broker_order_id = order.get("broker_order_id") if order else ""
        if order and str(order.get("status") or "").upper() in {"COMPLETE", "FILLED", "CANCELLED", "REJECTED"}:
            return True
        if self.settings.mode != MODE_REAL or not broker_order_id:
            return True
        try:
            response = self.broker.cancel_order(broker_order_id)
        except Exception as exc:
            self._freeze_real_orders(f"Failed to cancel real OCO peer {broker_order_id}: {exc}")
            return False
        if order:
            order["status_message"] = message
            if hasattr(self.database, "save_order_event"):
                self.database.save_order_event(self.session_id, order, "CANCEL_SENT", message, response)
        return True

    def _freeze_real_orders(self, reason: str) -> None:
        self.real_freeze_reason = str(reason or "Real order lifecycle frozen.")
        if hasattr(self.broker, "real_order_pause_reason"):
            self.broker.real_order_pause_reason = self.real_freeze_reason
        if self.audit:
            self.audit.log("CRITICAL", "orders", "real_order_lifecycle_frozen", {"reason": self.real_freeze_reason})

    def _snapshots_by_symbol(self, snapshots: list[Any] | dict[str, Any] | None) -> dict[str, Any]:
        if not snapshots:
            return {}
        if isinstance(snapshots, dict):
            return {str(key).upper(): value for key, value in snapshots.items()}
        result = {}
        for snapshot in snapshots:
            symbol = snapshot.get("symbol") if isinstance(snapshot, dict) else getattr(snapshot, "symbol", "")
            if symbol:
                result[str(symbol).upper()] = snapshot
        return result

    def _open_trades(self) -> list[dict[str, Any]]:
        if self.active_trades:
            return [trade for trade in self.active_trades.values() if trade.get("status") == "OPEN"]
        if self.active_trade and self.active_trade.get("status") == "OPEN":
            return [self.active_trade]
        return []

    def open_trade_count(self) -> int:
        return len(self._open_trades())

    def pending_entry_count(self) -> int:
        return sum(1 for row in self.order_history if row.get("role") == "ENTRY" and row.get("status") in {"PENDING", "PLACED", "OPEN", "PARTIAL"})

    def _refresh_active_trade_reference(self) -> None:
        open_trades = self._open_trades()
        if open_trades:
            self.active_trade = open_trades[0]
            return
        if self.active_trades:
            self.active_trade = list(self.active_trades.values())[-1]

    def _trade_for_entry(self, local_order_id: str) -> dict[str, Any] | None:
        for trade in self.active_trades.values():
            if trade.get("entry_order_id") == local_order_id:
                return trade
        return None

    def _trade_for_protective_order(self, local_order_id: str) -> dict[str, Any] | None:
        for trade in self.active_trades.values():
            if local_order_id in {trade.get("stoploss_order_id"), trade.get("target_order_id")}:
                return trade
        return None

    def _trade_for_exit_order(self, local_order_id: str) -> dict[str, Any] | None:
        order = self._order_by_local_id(local_order_id)
        parent_trade_id = order.get("parent_trade_id") if order else ""
        if parent_trade_id and parent_trade_id in self.active_trades:
            return self.active_trades[parent_trade_id]
        for trade in self.active_trades.values():
            management = trade.get("management") or {}
            if local_order_id == management.get("real_exit_order_id"):
                return trade
        return None

    def _pending_real_exit_order(self, trade: dict[str, Any]) -> dict[str, Any] | None:
        trade_id = trade.get("trade_id", "")
        for order in self.order_history:
            if order.get("role") not in {"ACTIVE_EXIT", "EMERGENCY_EXIT"}:
                continue
            if order.get("parent_trade_id") != trade_id:
                continue
            if str(order.get("status") or "").upper() not in {"COMPLETE", "FILLED", "CANCELLED", "REJECTED"}:
                return order
        return None

    def _order_by_local_id(self, local_id: str) -> dict[str, Any] | None:
        for order in self.order_history:
            if order.get("local_order_id") == local_id:
                return order
        return None

    def _filled_quantity(self, broker_row: dict[str, Any], local_order: dict[str, Any], trades: list[dict[str, Any]]) -> int:
        for key in ("filled_quantity", "filled_qty", "quantity_filled"):
            if broker_row.get(key) not in ("", None):
                try:
                    return int(float(broker_row.get(key) or 0))
                except (TypeError, ValueError):
                    pass
        order_id = str(local_order.get("broker_order_id") or "")
        qty = 0
        for trade in trades or []:
            if str(trade.get("order_id") or "") == order_id:
                try:
                    qty += int(float(trade.get("quantity") or 0))
                except (TypeError, ValueError):
                    pass
        if qty:
            return qty
        if str(broker_row.get("status") or "").upper() in {"COMPLETE", "FILLED"}:
            return int(local_order.get("quantity") or 0)
        return 0

    def _average_fill_price(self, broker_row: dict[str, Any], local_order: dict[str, Any], trades: list[dict[str, Any]]) -> float:
        for key in ("average_price", "avg_price", "price"):
            if broker_row.get(key) not in ("", None):
                try:
                    value = float(broker_row.get(key) or 0)
                    if value > 0:
                        return value
                except (TypeError, ValueError):
                    pass
        order_id = str(local_order.get("broker_order_id") or "")
        total_qty = 0
        total_value = 0.0
        for trade in trades or []:
            if str(trade.get("order_id") or "") != order_id:
                continue
            try:
                qty = int(float(trade.get("quantity") or 0))
                price = float(trade.get("average_price") or trade.get("price") or 0)
            except (TypeError, ValueError):
                continue
            total_qty += qty
            total_value += qty * price
        if total_qty > 0:
            return total_value / total_qty
        return float(local_order.get("price") or 0)

    def _blocked(self, message: str, request, margin: dict) -> dict:
        return {"ok": False, "message": message, "request": request.to_dict() if request else {}, "margin": margin}

    def _order_row(self, request, side: str, response=None, status="PENDING", status_message="", role="ENTRY", margin_required=None):
        self._local_counter += 1
        params = request.to_kite_params()
        now = datetime.now().isoformat(timespec="seconds")
        return {
            "session_id": self.session_id,
            "broker_order_id": (response or {}).get("order_id", ""),
            "local_order_id": f"{request.tag}-{self._local_counter}",
            "mode": self.settings.mode,
            "symbol": request.tradingsymbol,
            "exchange": request.exchange,
            "side": side,
            "transaction_type": request.transaction_type,
            "order_type": request.order_type,
            "product": request.product,
            "quantity": request.quantity,
            "price": request.price,
            "trigger_price": request.trigger_price,
            "status": status,
            "status_message": status_message,
            "broker_response": response or {"params": params},
            "created_at": now,
            "updated_at": now,
            "role": role,
            "margin_required": margin_required,
        }

    def _record_order(self, order: dict) -> None:
        self.order_history.append(order)
        self.database.save_order(order)
        if hasattr(self.database, "save_order_event"):
            self.database.save_order_event(self.session_id, order, "CREATED", order.get("status_message", ""), order.get("broker_response") or {})

    def _set_local_order_status(self, local_id: str, status: str, message: str, response: dict) -> None:
        if not local_id:
            return
        for order in self.order_history:
            if order["local_order_id"] == local_id:
                order["status"] = status
                order["status_message"] = message
                order["updated_at"] = datetime.now().isoformat(timespec="seconds")
                if hasattr(self.database, "save_order_event"):
                    self.database.save_order_event(self.session_id, order, status, message, response)
                break
        self.database.update_order_status(local_id, status, message, response)

    def _latest_bar(self, row: dict) -> dict:
        candles = row.get("candles") or []
        if candles:
            return candles[-1]
        return row

    def _entry_touched(self, order: dict, latest: dict) -> bool:
        price = float(order.get("price") or 0)
        high = float(latest.get("high") or latest.get("close") or 0)
        low = float(latest.get("low") or latest.get("close") or 0)
        ltp = float(latest.get("ltp") or latest.get("close") or 0)
        if str(getattr(self.settings, "paper_fill_model", "CANDLE_TOUCH_CONSERVATIVE")).upper() == "LTP_TOUCH":
            if order.get("transaction_type") == "BUY":
                return ltp <= price
            return ltp >= price
        if order.get("transaction_type") == "BUY":
            return low <= price
        return high >= price

    def _paper_fill_message(self, order: dict) -> str:
        model = str(getattr(self.settings, "paper_fill_model", "CANDLE_TOUCH_CONSERVATIVE") or "").upper()
        if model == "LTP_TOUCH":
            return "Paper fill based on latest LTP touching the entry limit."
        if order.get("transaction_type") == "BUY":
            return "Paper fill based on candle low touching buy limit."
        return "Paper fill based on candle high touching sell limit."

    def _exit_plan_from_fill(self, entry_order_row: dict, fill_price: float, latest: dict) -> dict[str, Any]:
        side = str(entry_order_row.get("side") or "").upper()
        planned_entry = float(entry_order_row.get("price") or fill_price or 0)
        planned_stop = float(entry_order_row.get("signal_stoploss") or 0)
        planned_target = float(entry_order_row.get("signal_target") or 0)
        tick_size = self._tick_size(entry_order_row["symbol"], entry_order_row["exchange"])
        if not getattr(self.settings, "recalculate_exit_from_actual_fill", True):
            return {
                "stoploss": planned_stop,
                "target": planned_target,
                "risk_points": abs(planned_entry - planned_stop),
                "source": "ORIGINAL_SIGNAL",
            }
        if side == SIDE_SHORT:
            original_risk = planned_stop - planned_entry
        else:
            original_risk = planned_entry - planned_stop
        average_range = float(latest.get("average_range_14") or latest.get("avg_range_14") or 0)
        if average_range <= 0:
            high = float(latest.get("high") or fill_price)
            low = float(latest.get("low") or fill_price)
            average_range = abs(high - low)
        if original_risk <= 0:
            original_risk = max(fill_price * 0.003, tick_size * 2)
        risk_points = max(original_risk, average_range * 0.75 if average_range > 0 else 0, fill_price * 0.002, tick_size * 2)
        rr = float(getattr(self.settings, "minimum_risk_reward", 1.5) or 1.5)
        if side == SIDE_SHORT:
            stoploss = fill_price + risk_points
            target = fill_price - risk_points * rr
        else:
            stoploss = fill_price - risk_points
            target = fill_price + risk_points * rr
        return {
            "stoploss": self._round_price(max(tick_size, stoploss), tick_size),
            "target": self._round_price(max(tick_size, target), tick_size),
            "risk_points": round(risk_points, 4),
            "source": "ACTUAL_FILL_RECALCULATED",
            "planned_entry": planned_entry,
            "actual_entry": fill_price,
        }

    def _modification_throttled(self, trade: dict[str, Any], key: str, seconds: int, now: datetime) -> bool:
        seconds = max(1, int(seconds or 1))
        value = (trade.get("management") or {}).get(key)
        if not value:
            return False
        try:
            previous = datetime.fromisoformat(str(value))
        except ValueError:
            return False
        return now - previous < timedelta(seconds=seconds)

    def _parse_time(self, value: str) -> datetime:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return datetime.now()

    def _tick_size(self, symbol: str, exchange: str) -> float:
        instrument = self.instrument_rows.get(f"{str(exchange or 'NSE').upper()}:{str(symbol or '').upper()}") or {}
        return tick_size_from_instrument(instrument)

    def _round_price(self, value: float, tick_size: float) -> float:
        from .execution_safeguards import round_price_to_tick

        return round_price_to_tick(value, tick_size)

    def _matching_real_order(self, request, tick_size: float, swallow_errors: bool = False) -> dict | None:
        if self.settings.mode != MODE_REAL or not hasattr(self.broker, "find_matching_order"):
            return None
        try:
            return self.broker.find_matching_order(request, tick_size)
        except Exception:
            if swallow_errors:
                return None
            raise

    def _broker_health_blockers(self) -> list[str]:
        if self.settings.mode != MODE_REAL or not hasattr(self.broker, "api_health_blockers"):
            return []
        return list(self.broker.api_health_blockers() or [])
