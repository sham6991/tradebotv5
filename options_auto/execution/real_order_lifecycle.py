from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from options_auto.core.clock import iso_now
from options_auto.execution.real_execution_controller import RealExecutionController


IDLE = "IDLE"
ENTRY_ORDER_SENT = "ENTRY_ORDER_SENT"
ENTRY_ORDER_OPEN = "ENTRY_ORDER_OPEN"
ENTRY_PARTIAL = "ENTRY_PARTIAL"
ENTRY_COMPLETE = "ENTRY_COMPLETE"
ENTRY_REJECTED = "ENTRY_REJECTED"
ENTRY_CANCELLED = "ENTRY_CANCELLED"
ENTRY_TIMEOUT = "ENTRY_TIMEOUT"
PROTECTION_PENDING = "PROTECTION_PENDING"
TARGET_PLACED = "TARGET_PLACED"
SL_PLACED = "SL_PLACED"
OCO_ACTIVE = "OCO_ACTIVE"
TARGET_FILLED = "TARGET_FILLED"
SL_FILLED = "SL_FILLED"
EXIT_RECONCILED = "EXIT_RECONCILED"
UNPROTECTED_POSITION = "UNPROTECTED_POSITION"
MANUAL_ATTENTION = "MANUAL_ATTENTION"
SAFE_MODE = "SAFE_MODE"


TERMINAL_ENTRY_STATES = {ENTRY_REJECTED, ENTRY_CANCELLED, ENTRY_TIMEOUT}


@dataclass
class RealOrderLifecycleEngine:
    controller: RealExecutionController
    state: str = IDLE
    entry_order: dict[str, Any] = field(default_factory=dict)
    trade_plan: dict[str, Any] = field(default_factory=dict)
    target_order: dict[str, Any] = field(default_factory=dict)
    stoploss_order: dict[str, Any] = field(default_factory=dict)
    fill: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    safe_mode: bool = False

    def submit_entry(self, entry_order: dict[str, Any], trade_plan: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
        self.entry_order = dict(entry_order or {})
        self.trade_plan = dict(trade_plan or {})
        self.target_order = {}
        self.stoploss_order = {}
        self.fill = {}
        self.blockers = []
        self.warnings = []
        self.state = ENTRY_ORDER_OPEN if self.entry_order.get("order_id") else ENTRY_ORDER_SENT
        self.entry_order.setdefault("submitted_at", iso_now())
        self.entry_order.setdefault("status", "OPEN")
        self._event(self.state, order_id=self.entry_order.get("order_id"))
        return self.snapshot()

    def poll_entry_status(self, broker_orders: list[dict[str, Any]] | None = None, now: datetime | None = None, settings: dict[str, Any] | None = None, adapter: Any | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        order = self._find_order(broker_orders, self.entry_order.get("order_id"))
        if not order:
            return self.snapshot()
        return self.handle_order_update(order, settings=settings, adapter=adapter, now=now)

    def handle_order_update(self, order_update: dict[str, Any], settings: dict[str, Any] | None = None, adapter: Any | None = None, now: datetime | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        now = now or datetime.now()
        update = dict(order_update or {})
        status = _status(update)
        filled_quantity = _int(update.get("filled_quantity"), update.get("filled") or 0)
        quantity = _int(update.get("quantity"), self.entry_order.get("quantity") or self.trade_plan.get("quantity") or 0)
        self.entry_order.update(update)
        self.entry_order["last_status_seen_at"] = now.isoformat(timespec="seconds")

        if status in {"COMPLETE", "FILLED"} or (quantity > 0 and filled_quantity >= quantity):
            return self.on_entry_complete(update, settings=settings, adapter=adapter)
        if filled_quantity > 0 and filled_quantity < quantity:
            return self.on_entry_partial(update, settings=settings, adapter=adapter)
        if status == "REJECTED":
            self.state = ENTRY_REJECTED
            reason = update.get("status_message") or update.get("rejection_reason") or "Broker rejected entry order."
            self.blockers = [str(reason)]
            self._event(self.state, reason=reason)
            return self.snapshot()
        if status == "CANCELLED":
            self.state = ENTRY_CANCELLED
            self._event(self.state)
            return self.snapshot()
        if self._entry_timed_out(now, settings):
            self.state = ENTRY_TIMEOUT
            self.blockers = ["Entry order timed out before fill."]
            self._event(self.state)
            return self.snapshot()
        self.state = ENTRY_ORDER_OPEN
        self._event(self.state, broker_status=status)
        return self.snapshot()

    def on_entry_complete(self, order_update: dict[str, Any], settings: dict[str, Any] | None = None, adapter: Any | None = None) -> dict[str, Any]:
        self.state = ENTRY_COMPLETE
        self.fill = _fill_from_order(order_update, self.trade_plan)
        self._event(self.state, average_price=self.fill.get("average_price"), filled_quantity=self.fill.get("filled_quantity"))
        return self.place_protection_orders(settings=settings, adapter=adapter)

    def on_entry_partial(self, order_update: dict[str, Any], settings: dict[str, Any] | None = None, adapter: Any | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        self.state = ENTRY_PARTIAL
        self.fill = _fill_from_order(order_update, self.trade_plan)
        self._event(self.state, filled_quantity=self.fill.get("filled_quantity"))
        if settings.get("partial_fill_protect_immediately", True):
            return self.place_protection_orders(settings=settings, adapter=adapter)
        return self.snapshot()

    def place_protection_orders(self, settings: dict[str, Any] | None = None, adapter: Any | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        if not self.fill or _int(self.fill.get("filled_quantity")) <= 0:
            self.blockers = ["No broker fill is available; target/SL were not placed."]
            return self.snapshot()
        self.state = PROTECTION_PENDING
        protection = self.controller.protection_orders_from_fill(self.trade_plan, self.fill, settings)
        target_request = protection["target_order"]
        stop_request = protection["stoploss_order"]
        self.target_order = {**target_request, "status": "PENDING"}
        self.stoploss_order = {**stop_request, "status": "PENDING"}
        self._event(PROTECTION_PENDING, quantity=self.fill.get("filled_quantity"))

        if adapter:
            target_response = adapter.place_target_sell_limit(
                target_request["tradingsymbol"],
                int(target_request["quantity"]),
                float(target_request["price"]),
                target_request.get("exchange") or "NFO",
                target_request.get("product") or "NRML",
                target_request.get("tag") or "OPTIONS_AUTO",
            )
            if target_response.get("ok"):
                self.target_order.update({"order_id": target_response.get("value") or target_response.get("order_id"), "status": "OPEN", "submitted_at": iso_now()})
                self.state = TARGET_PLACED
            else:
                self.target_order.update({"status": "FAILED", "error": target_response.get("error")})
                self.state = MANUAL_ATTENTION
                self.warnings.append("Target placement failed; protective SL must remain supervised.")

            sl_response = adapter.place_stoploss_sell_sl_limit(
                stop_request["tradingsymbol"],
                int(stop_request["quantity"]),
                float(stop_request["trigger_price"]),
                float(stop_request["price"]),
                stop_request.get("exchange") or "NFO",
                stop_request.get("product") or "NRML",
                stop_request.get("tag") or "OPTIONS_AUTO",
            )
            if sl_response.get("ok"):
                self.stoploss_order.update({"order_id": sl_response.get("value") or sl_response.get("order_id"), "status": "OPEN", "submitted_at": iso_now()})
                self.state = OCO_ACTIVE if self.target_order.get("status") == "OPEN" else SL_PLACED
            else:
                self.stoploss_order.update({"status": "FAILED", "error": sl_response.get("error")})
                self.mark_unprotected("Stoploss placement failed.")
        else:
            self.target_order.update({"status": "READY_TO_SUBMIT"})
            self.stoploss_order.update({"status": "READY_TO_SUBMIT"})
            self.state = PROTECTION_PENDING
        return self.snapshot()

    def verify_protection_orders(self, broker_orders: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        target = self._find_order(broker_orders, self.target_order.get("order_id"))
        stop = self._find_order(broker_orders, self.stoploss_order.get("order_id"))
        if target:
            self.target_order.update(target)
        if stop:
            self.stoploss_order.update(stop)
        target_open = _status(self.target_order) in {"OPEN", "TRIGGER PENDING", "VALIDATION PENDING"}
        stop_open = _status(self.stoploss_order) in {"OPEN", "TRIGGER PENDING", "VALIDATION PENDING"}
        if target_open and stop_open:
            self.state = OCO_ACTIVE
        elif not stop_open and _int(self.fill.get("filled_quantity")) > 0:
            self.mark_unprotected("Filled real position has no active stoploss order.")
        return self.snapshot()

    def monitor_oco(self, broker_orders: list[dict[str, Any]] | None = None, adapter: Any | None = None) -> dict[str, Any]:
        target = self._find_order(broker_orders, self.target_order.get("order_id"))
        stop = self._find_order(broker_orders, self.stoploss_order.get("order_id"))
        if target:
            self.target_order.update(target)
        if stop:
            self.stoploss_order.update(stop)
        if _status(self.target_order) in {"COMPLETE", "FILLED"}:
            self.state = TARGET_FILLED
            self.cancel_opposite_exit(adapter, self.stoploss_order.get("order_id"))
        elif _status(self.stoploss_order) in {"COMPLETE", "FILLED"}:
            self.state = SL_FILLED
            self.cancel_opposite_exit(adapter, self.target_order.get("order_id"))
        return self.snapshot()

    def cancel_opposite_exit(self, adapter: Any | None, order_id: str | None) -> dict[str, Any]:
        if order_id and adapter:
            response = adapter.cancel_order(order_id)
            self._event("OCO_CANCEL_SUBMITTED", order_id=order_id, ok=response.get("ok"))
        self.state = EXIT_RECONCILED
        self._event(self.state)
        return self.snapshot()

    def reconcile_positions(self, broker_orders: list[dict[str, Any]] | None = None, positions: list[dict[str, Any]] | dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.controller.reconcile([self.entry_order, self.target_order, self.stoploss_order], broker_orders, positions, self.trade_plan)
        if result.get("unprotected_positions"):
            self.mark_unprotected("Unprotected real position detected. New entries stopped. Check broker terminal immediately.")
        return {**self.snapshot(), "reconciliation": result}

    def mark_unprotected(self, reason: str) -> dict[str, Any]:
        self.state = UNPROTECTED_POSITION
        self.safe_mode = True
        self.blockers = list(dict.fromkeys(self.blockers + [reason]))
        self.controller.enter_safe_mode("OPTIONS_AUTO", reason)
        self._event(UNPROTECTED_POSITION, reason=reason)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "entry_order": dict(self.entry_order),
            "target_order": dict(self.target_order),
            "stoploss_order": dict(self.stoploss_order),
            "fill": dict(self.fill),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "safe_mode": self.safe_mode,
            "history": list(self.history[-100:]),
        }

    def _entry_timed_out(self, now: datetime, settings: dict[str, Any]) -> bool:
        submitted = _dt(self.entry_order.get("submitted_at") or self.entry_order.get("placed_at"))
        if not submitted:
            return False
        timeout = float(settings.get("real_entry_timeout_seconds") or 30)
        return now >= submitted + timedelta(seconds=timeout)

    def _find_order(self, broker_orders: list[dict[str, Any]] | None, order_id: Any) -> dict[str, Any]:
        if not order_id:
            return {}
        for order in broker_orders or []:
            if str((order or {}).get("order_id") or (order or {}).get("id") or "") == str(order_id):
                return dict(order or {})
        return {}

    def _event(self, event: str, **extra: Any) -> None:
        self.history.append({"timestamp": iso_now(), "event": event, **extra})
        self.history = self.history[-200:]


def _fill_from_order(order: dict[str, Any], trade_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "average_price": _number(order.get("average_price"), trade_plan.get("entry_price")),
        "filled_quantity": _int(order.get("filled_quantity"), order.get("quantity") or trade_plan.get("quantity")),
        "order_id": order.get("order_id") or order.get("id"),
        "filled_at": iso_now(),
    }


def _status(order: dict[str, Any]) -> str:
    return str((order or {}).get("status") or "").upper()


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


def _int(value: Any, default: Any = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        try:
            return int(float(default))
        except (TypeError, ValueError):
            return 0


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
