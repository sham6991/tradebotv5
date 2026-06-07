from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from options_auto.core.clock import iso_now
from options_auto.execution.real_execution_controller import RealExecutionController


IDLE = "IDLE"
ENTRY_REQUESTED = "ENTRY_REQUESTED"
ENTRY_OPEN = "ENTRY_OPEN"
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

ENTRY_FILLED_UNPROTECTED = "ENTRY_FILLED_UNPROTECTED"
PROTECTIVE_EXIT_PLACING = "PROTECTIVE_EXIT_PLACING"
PROTECTIVE_EXIT_ACTIVE = "PROTECTIVE_EXIT_ACTIVE"
PROTECTIVE_EXIT_FAILED = "PROTECTIVE_EXIT_FAILED"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
FLATTENING = "FLATTENING"
FLAT = "FLAT"
FLAT_CONFIRMED = "FLAT_CONFIRMED"
FLAT_UNVERIFIED = "FLAT_UNVERIFIED"
BROKER_STATE_UNKNOWN = "BROKER_STATE_UNKNOWN"
MANUAL_RECONCILIATION_REQUIRED = "MANUAL_RECONCILIATION_REQUIRED"
EXIT_FILLED_CANCEL_NOT_VERIFIED = "EXIT_FILLED_CANCEL_NOT_VERIFIED"


TERMINAL_ENTRY_STATES = {ENTRY_REJECTED, ENTRY_CANCELLED, ENTRY_TIMEOUT}
PROTECTIVE_ACTIVE_STATUSES = {"OPEN", "TRIGGER PENDING", "VALIDATION PENDING"}
PROTECTIVE_FAILED_STATUSES = {"REJECTED", "CANCELLED", "CANCELLED AMO", "EXPIRED", "FAILED"}


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
    protected_state: str = FLAT
    emergency_flatten_required: bool = False

    def submit_entry(self, entry_order: dict[str, Any], trade_plan: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
        self.entry_order = dict(entry_order or {})
        self.trade_plan = dict(trade_plan or {})
        self.target_order = {}
        self.stoploss_order = {}
        self.fill = {}
        self.blockers = []
        self.warnings = []
        self.state = ENTRY_ORDER_OPEN if self.entry_order.get("order_id") else ENTRY_ORDER_SENT
        self.protected_state = ENTRY_OPEN if self.entry_order.get("order_id") else ENTRY_REQUESTED
        self.emergency_flatten_required = False
        self.entry_order.setdefault("submitted_at", iso_now())
        self.entry_order.setdefault("status", "OPEN")
        self._event(self.state, order_id=self.entry_order.get("order_id"), protected_state=self.protected_state)
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
            self.protected_state = FLAT
            reason = update.get("status_message") or update.get("rejection_reason") or "Broker rejected entry order."
            self.blockers = [str(reason)]
            self._event(self.state, reason=reason)
            return self.snapshot()
        if status == "CANCELLED":
            self.state = ENTRY_CANCELLED
            self.protected_state = FLAT
            self._event(self.state)
            return self.snapshot()
        if self._entry_timed_out(now, settings):
            self.state = ENTRY_TIMEOUT
            self.protected_state = FLAT
            self.blockers = ["Entry order timed out before fill."]
            self._event(self.state)
            return self.snapshot()
        self.state = ENTRY_ORDER_OPEN
        self.protected_state = ENTRY_OPEN
        self._event(self.state, broker_status=status, protected_state=self.protected_state)
        return self.snapshot()

    def on_entry_complete(self, order_update: dict[str, Any], settings: dict[str, Any] | None = None, adapter: Any | None = None) -> dict[str, Any]:
        self.fill = _fill_from_order(order_update, self.trade_plan)
        if self._protection_submission_started():
            protected_qty = min(_int(self.target_order.get("quantity")), _int(self.stoploss_order.get("quantity")))
            if protected_qty > 0 and _int(self.fill.get("filled_quantity")) > protected_qty:
                return self.mark_unprotected("Filled quantity exceeds confirmed protective exit quantity.")
            return self.snapshot()
        self.state = ENTRY_COMPLETE
        self.protected_state = ENTRY_FILLED_UNPROTECTED
        self._event(self.state, average_price=self.fill.get("average_price"), filled_quantity=self.fill.get("filled_quantity"), protected_state=self.protected_state)
        return self.place_protection_orders(settings=settings, adapter=adapter)

    def on_entry_partial(self, order_update: dict[str, Any], settings: dict[str, Any] | None = None, adapter: Any | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        self.fill = _fill_from_order(order_update, self.trade_plan)
        if self._protection_submission_started():
            protected_qty = min(_int(self.target_order.get("quantity")), _int(self.stoploss_order.get("quantity")))
            if protected_qty > 0 and _int(self.fill.get("filled_quantity")) > protected_qty:
                return self.mark_unprotected("Partial fill increased beyond protective exit quantity.")
            return self.snapshot()
        self.state = ENTRY_PARTIAL
        self.protected_state = ENTRY_PARTIAL
        self._event(self.state, filled_quantity=self.fill.get("filled_quantity"), protected_state=self.protected_state)
        if settings.get("partial_fill_protect_immediately", True):
            return self.place_protection_orders(settings=settings, adapter=adapter)
        return self.snapshot()

    def place_protection_orders(self, settings: dict[str, Any] | None = None, adapter: Any | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        if not self.fill or _int(self.fill.get("filled_quantity")) <= 0:
            self.blockers = ["No broker fill is available; target/SL were not placed."]
            self.protected_state = RECONCILIATION_REQUIRED
            return self.snapshot()
        self.state = PROTECTION_PENDING
        self.protected_state = PROTECTIVE_EXIT_PLACING
        protection = self.controller.protection_orders_from_fill(self.trade_plan, self.fill, settings)
        target_request = protection["target_order"]
        stop_request = protection["stoploss_order"]
        self.target_order = {**target_request, "status": "PENDING"}
        self.stoploss_order = {**stop_request, "status": "PENDING"}
        self._event(PROTECTION_PENDING, quantity=self.fill.get("filled_quantity"), protected_state=self.protected_state)

        if adapter:
            # IMPORTANT USER REQUIREMENT:
            # Options Auto intentionally submits target before stoploss.
            # Do not reorder without explicit user approval.
            # Reliability hardening verifies broker state after this sequence.
            target_response = adapter.place_target_sell_limit(
                target_request["tradingsymbol"],
                int(target_request["quantity"]),
                float(target_request["price"]),
                target_request.get("exchange") or "NFO",
                target_request.get("product") or "NRML",
                target_request.get("tag") or "OPTIONS_AUTO",
            )
            if target_response.get("ok"):
                self.target_order.update({"order_id": target_response.get("value") or target_response.get("order_id"), "status": "SUBMITTED", "submitted_at": iso_now()})
            else:
                self.target_order.update({"status": "FAILED", "error": target_response.get("error")})
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
                self.stoploss_order.update({"order_id": sl_response.get("value") or sl_response.get("order_id"), "status": "SUBMITTED", "submitted_at": iso_now()})
                self.state = PROTECTION_PENDING
            else:
                self.stoploss_order.update({"status": "FAILED", "error": sl_response.get("error")})
                self.mark_unprotected("Stoploss placement failed.")
        else:
            self.target_order.update({"status": "READY_TO_SUBMIT"})
            self.stoploss_order.update({"status": "READY_TO_SUBMIT"})
            self.state = PROTECTION_PENDING
        return self.snapshot()

    def handle_exit_order_update(self, order_update: dict[str, Any], adapter: Any | None = None) -> dict[str, Any]:
        update = dict(order_update or {})
        order_id = str(update.get("order_id") or update.get("id") or "")
        role = self._exit_order_role(order_id)
        if not role:
            return self.snapshot()
        target = self.target_order if role == "target" else self.stoploss_order
        target.update(update)
        target["last_status_seen_at"] = iso_now()
        status = _status(target)
        if status in {"COMPLETE", "FILLED"}:
            return self.monitor_oco([self.target_order, self.stoploss_order], adapter=adapter)
        if status in PROTECTIVE_FAILED_STATUSES and self.protected_state != FLAT and _int(self.fill.get("filled_quantity")) > 0:
            return self.mark_unprotected(f"Protective {role} order {status.lower()} in broker update.")
        return self.verify_protection_orders([self.target_order, self.stoploss_order])

    def verify_protection_orders(self, broker_orders: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if broker_orders is None and (self.target_order.get("order_id") or self.stoploss_order.get("order_id")):
            self.state = PROTECTION_PENDING
            self.protected_state = RECONCILIATION_REQUIRED
            self.safe_mode = True
            self.controller.enter_safe_mode("OPTIONS_AUTO", "Broker orderbook unavailable; protection cannot be verified.")
            self.blockers = list(dict.fromkeys(self.blockers + ["Broker orderbook unavailable; protection cannot be verified."]))
            self._event(RECONCILIATION_REQUIRED, protected_state=self.protected_state)
            return self.snapshot()
        target = self._find_order(broker_orders, self.target_order.get("order_id"))
        stop = self._find_order(broker_orders, self.stoploss_order.get("order_id"))
        if target:
            self.target_order.update(target)
        if stop:
            self.stoploss_order.update(stop)
        target_status = _status(self.target_order)
        stop_status = _status(self.stoploss_order)
        target_open = target_status in PROTECTIVE_ACTIVE_STATUSES
        stop_open = stop_status in PROTECTIVE_ACTIVE_STATUSES
        failed_roles = []
        if target_status in PROTECTIVE_FAILED_STATUSES:
            failed_roles.append("target")
        if stop_status in PROTECTIVE_FAILED_STATUSES:
            failed_roles.append("stoploss")
        if target_status in PROTECTIVE_FAILED_STATUSES and stop_open and _int(self.fill.get("filled_quantity")) > 0:
            self.state = PROTECTION_PENDING
            self.protected_state = PROTECTIVE_EXIT_ACTIVE
            self.safe_mode = True
            self.controller.stop_new_entries("OPTIONS_AUTO", "Target missing, stoploss active; manual target reconciliation required.")
            self.warnings = list(dict.fromkeys(self.warnings + ["Target missing, stoploss active. New entries are blocked until resolved."]))
            self._event("TARGET_MISSING_STOPLOSS_ACTIVE", protected_state=self.protected_state)
            return self.snapshot()
        if failed_roles and _int(self.fill.get("filled_quantity")) > 0 and self.protected_state != FLAT:
            return self.mark_unprotected(f"Protective {'/'.join(failed_roles)} order failed in broker orderbook.")
        if target_open and stop_open:
            self.state = OCO_ACTIVE
            self.protected_state = PROTECTIVE_EXIT_ACTIVE
            self._event(OCO_ACTIVE, protected_state=self.protected_state)
        elif _int(self.fill.get("filled_quantity")) > 0 and self.protected_state not in {PROTECTIVE_EXIT_FAILED, RECONCILIATION_REQUIRED, FLAT}:
            self.state = PROTECTION_PENDING
            self.protected_state = PROTECTIVE_EXIT_PLACING
        return self.snapshot()

    def monitor_oco(self, broker_orders: list[dict[str, Any]] | None = None, adapter: Any | None = None, positions: list[dict[str, Any]] | dict[str, Any] | None = None) -> dict[str, Any]:
        target = self._find_order(broker_orders, self.target_order.get("order_id"))
        stop = self._find_order(broker_orders, self.stoploss_order.get("order_id"))
        if target:
            self.target_order.update(target)
        if stop:
            self.stoploss_order.update(stop)
        if _status(self.target_order) in {"COMPLETE", "FILLED"}:
            self.state = TARGET_FILLED
            self.protected_state = FLATTENING
            self.cancel_opposite_exit(adapter, self.stoploss_order.get("order_id"))
            return self.verify_exit_flatness(broker_orders, positions, opposite_order=self.stoploss_order)
        elif _status(self.stoploss_order) in {"COMPLETE", "FILLED"}:
            self.state = SL_FILLED
            self.protected_state = FLATTENING
            self.cancel_opposite_exit(adapter, self.target_order.get("order_id"))
            return self.verify_exit_flatness(broker_orders, positions, opposite_order=self.target_order)
        return self.snapshot()

    def cancel_opposite_exit(self, adapter: Any | None, order_id: str | None) -> dict[str, Any]:
        if order_id and adapter:
            response = adapter.cancel_order(order_id)
            self._event("OCO_CANCEL_SUBMITTED", order_id=order_id, ok=response.get("ok"))
        return self.snapshot()

    def verify_exit_flatness(
        self,
        broker_orders: list[dict[str, Any]] | None = None,
        positions: list[dict[str, Any]] | dict[str, Any] | None = None,
        opposite_order: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if broker_orders is None or positions is None:
            self.protected_state = MANUAL_RECONCILIATION_REQUIRED
            self.safe_mode = True
            self.controller.enter_safe_mode("OPTIONS_AUTO", "Exit fill seen but broker orderbook/positions are unavailable for flat verification.")
            self.blockers = list(dict.fromkeys(self.blockers + ["Exit fill seen but flat state is not broker-verified."]))
            self._event(MANUAL_RECONCILIATION_REQUIRED, protected_state=self.protected_state)
            return self.snapshot()
        if opposite_order:
            fresh = self._find_order(broker_orders, opposite_order.get("order_id"))
            if fresh:
                opposite_order.update(fresh)
        opposite_status = _status(opposite_order or {})
        cancel_verified = not (opposite_order or {}).get("order_id") or opposite_status in {"CANCELLED", "REJECTED", "COMPLETE", "FILLED"}
        flat_verified = self._position_flat(positions)
        if cancel_verified and flat_verified:
            self.state = EXIT_RECONCILED
            self.protected_state = FLAT_CONFIRMED
            self.emergency_flatten_required = False
            self.safe_mode = False
            self._event(FLAT_CONFIRMED, protected_state=self.protected_state)
            return self.snapshot()
        self.state = EXIT_FILLED_CANCEL_NOT_VERIFIED
        self.protected_state = MANUAL_RECONCILIATION_REQUIRED if not cancel_verified else FLAT_UNVERIFIED
        self.safe_mode = True
        self.controller.enter_safe_mode("OPTIONS_AUTO", "Exit filled but opposite OCO leg cancellation or flat position is not verified.")
        self.blockers = list(dict.fromkeys(self.blockers + ["Exit filled but opposite OCO leg cancellation is not verified. Check Kite orderbook manually."]))
        self._event(self.state, protected_state=self.protected_state, cancel_verified=cancel_verified, flat_verified=flat_verified)
        return self.snapshot()

    def reconcile_positions(self, broker_orders: list[dict[str, Any]] | None = None, positions: list[dict[str, Any]] | dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.controller.reconcile([self.entry_order, self.target_order, self.stoploss_order], broker_orders, positions, self.trade_plan)
        if result.get("unprotected_positions"):
            self.protected_state = RECONCILIATION_REQUIRED
            self.mark_unprotected("Unprotected real position detected. New entries stopped. Check broker terminal immediately.")
        return {**self.snapshot(), "reconciliation": result}

    def mark_unprotected(self, reason: str) -> dict[str, Any]:
        self.state = UNPROTECTED_POSITION
        self.protected_state = PROTECTIVE_EXIT_FAILED
        self.safe_mode = True
        self.emergency_flatten_required = True
        self.blockers = list(dict.fromkeys(self.blockers + [reason]))
        self.controller.enter_safe_mode("OPTIONS_AUTO", reason)
        self._event(UNPROTECTED_POSITION, reason=reason, protected_state=self.protected_state)
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
            "protected_state": self.protected_state,
            "emergency_flatten_required": self.emergency_flatten_required,
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

    def _exit_order_role(self, order_id: Any) -> str:
        if not order_id:
            return ""
        text = str(order_id)
        if text == str(self.target_order.get("order_id") or self.target_order.get("id") or ""):
            return "target"
        if text == str(self.stoploss_order.get("order_id") or self.stoploss_order.get("id") or ""):
            return "stoploss"
        return ""

    def _protection_submission_started(self) -> bool:
        if self.protected_state in {PROTECTIVE_EXIT_PLACING, PROTECTIVE_EXIT_ACTIVE, PROTECTIVE_EXIT_FAILED, RECONCILIATION_REQUIRED, FLATTENING}:
            return True
        return bool(
            self.target_order.get("order_id")
            or self.stoploss_order.get("order_id")
            or _status(self.target_order) in {"PENDING", "SUBMITTED", *PROTECTIVE_ACTIVE_STATUSES}
            or _status(self.stoploss_order) in {"PENDING", "SUBMITTED", *PROTECTIVE_ACTIVE_STATUSES}
        )

    def _position_flat(self, positions: list[dict[str, Any]] | dict[str, Any] | None) -> bool:
        rows: list[dict[str, Any]] = []
        if isinstance(positions, dict):
            for key in ("net", "day", "positions"):
                value = positions.get(key)
                if isinstance(value, list):
                    rows.extend([dict(item) for item in value if isinstance(item, dict)])
        elif isinstance(positions, list):
            rows = [dict(item) for item in positions if isinstance(item, dict)]
        symbol = str(self.trade_plan.get("tradingsymbol") or self.entry_order.get("tradingsymbol") or "").upper()
        for row in rows:
            row_symbol = str(row.get("tradingsymbol") or row.get("symbol") or "").upper()
            if symbol and row_symbol and row_symbol != symbol:
                continue
            if _int(row.get("quantity"), row.get("net_quantity") or 0) != 0:
                return False
        return True

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
