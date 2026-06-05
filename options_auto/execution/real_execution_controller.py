from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from options_auto.constants import MODE_REAL, REAL_EXECUTION_DISABLED_REASON
from options_auto.core.clock import iso_now
from options_auto.core.mode_guard import ModeGuard
from options_auto.execution.kite_api_manager import KiteApiManager
from options_auto.execution.reconciliation import ReconciliationEngine
from options_auto.intelligence.entry_timing_engine import round_to_tick


@dataclass
class RealExecutionRuntimeState:
    stop_new_entries: bool = False
    safe_mode: bool = False
    last_preflight: dict[str, Any] = field(default_factory=dict)
    last_reconciliation: dict[str, Any] = field(default_factory=dict)
    emergency_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop_new_entries": self.stop_new_entries,
            "safe_mode": self.safe_mode,
            "last_preflight": self.last_preflight,
            "last_reconciliation": self.last_reconciliation,
            "emergency_history": self.emergency_history[-50:],
        }


class RealExecutionController:
    """Dry-run real execution safety controller.

    This controller deliberately prepares and validates real-mode state without
    placing broker orders. Actual order mutations remain behind ModeGuard and
    the KiteOrderAdapter.
    """

    def __init__(self, api_manager: KiteApiManager | None = None, reconciliation: ReconciliationEngine | None = None):
        self.api = api_manager or KiteApiManager()
        self.reconciliation = reconciliation or ReconciliationEngine()
        self.state = RealExecutionRuntimeState()

    def preflight(
        self,
        mode_guard: ModeGuard,
        client: Any | None,
        settings: dict[str, Any],
        local_orders: list[dict[str, Any]] | None = None,
        active_trades: list[dict[str, Any]] | None = None,
        broker_orders: list[dict[str, Any]] | None = None,
        positions: list[dict[str, Any]] | dict[str, Any] | None = None,
        trade_plan: dict[str, Any] | None = None,
        profile: dict[str, Any] | None = None,
        results_writable: bool = True,
        watchdog_ready: bool = True,
        market_open: bool = True,
        instruments_valid: bool = True,
        static_ip_confirmed: bool | None = None,
    ) -> dict[str, Any]:
        settings = dict(settings or {})
        blockers: list[str] = []
        warnings: list[str] = []
        evidence: dict[str, Any] = {
            "timestamp": iso_now(),
            "mode": mode_guard.mode,
            "checks": {},
        }

        if mode_guard.mode != MODE_REAL:
            blockers.append("Real execution preflight can only run in REAL mode.")
            result = self._result("BLOCKED_BY_MODE", blockers, warnings, evidence)
            self.state.last_preflight = result
            return result

        if not mode_guard.real_mode_confirmed:
            blockers.append("Real mode confirmation is missing.")
        evidence["checks"]["real_mode_confirmed"] = mode_guard.real_mode_confirmed

        if not client:
            blockers.append("Real Zerodha client is not connected.")
        evidence["checks"]["client_connected"] = bool(client)

        profile = dict(profile or {})
        if client and not profile:
            fetched_profile = self._safe_client_call(client, ("profile", "user_profile"), priority="RECONCILIATION")
            if isinstance(fetched_profile, dict):
                profile = fetched_profile
        kite_user_id = profile.get("user_id") or profile.get("client_id") or profile.get("user_name") or ""
        if not kite_user_id:
            blockers.append("Real Kite profile is not fetched.")
        evidence["checks"]["kite_profile_user_id"] = kite_user_id

        margin = None
        if client:
            margin = self._safe_margin(client)
        if margin in ("", None):
            blockers.append("Real available margin is not fetched.")
        else:
            try:
                if float(margin) <= 0:
                    blockers.append("Real available margin is zero.")
            except (TypeError, ValueError):
                blockers.append("Real available margin is invalid.")
        evidence["checks"]["available_margin"] = margin

        if not instruments_valid:
            blockers.append("Instrument cache/contract validation is not ready.")
        evidence["checks"]["instruments_valid"] = bool(instruments_valid)

        static_ip_ok = bool(settings.get("static_ip_confirmed")) if static_ip_confirmed is None else bool(static_ip_confirmed)
        if not static_ip_ok:
            blockers.append("Static IP/order readiness is not confirmed for real money trading.")
        evidence["checks"]["static_ip_confirmed"] = static_ip_ok

        if not market_open:
            blockers.append("Market is not open for real order entry.")
        evidence["checks"]["market_open"] = bool(market_open)

        if not results_writable:
            blockers.append("Options Auto results folder is not writable.")
        evidence["checks"]["results_writable"] = bool(results_writable)

        if not watchdog_ready:
            blockers.append("Watchdog is not ready.")
        evidence["checks"]["watchdog_ready"] = bool(watchdog_ready)

        if self.state.stop_new_entries:
            blockers.append("Stop New Entries is active.")
        if self.state.safe_mode:
            blockers.append("Safe Mode is active; new real entries are blocked.")
        evidence["checks"]["stop_new_entries"] = self.state.stop_new_entries
        evidence["checks"]["safe_mode"] = self.state.safe_mode

        reconciliation = self.reconcile(local_orders, broker_orders, positions, trade_plan)
        evidence["reconciliation"] = reconciliation
        blockers.extend(reconciliation.get("blockers") or [])

        rate_health = self.api.health()
        if rate_health.get("recent_failures"):
            warnings.append("Recent Kite API manager failures exist; keep real entries paused until stable.")
        evidence["rate_limiter"] = {
            "calls": rate_health.get("calls", 0),
            "recent_failures": rate_health.get("recent_failures", 0),
            "healthy": rate_health.get("healthy", True),
        }

        dry_run_ready = not blockers
        if settings.get("dry_run_real_only") and not settings.get("real_orders_enabled"):
            blockers.append("Real dry-run override is active; live order sending is blocked.")
        result = self._result("REAL_PREFLIGHT_OK" if not blockers else "BLOCKED_BY_EXECUTION", blockers, warnings, evidence)
        result["dry_run_ready"] = dry_run_ready
        result["real_orders_enabled"] = bool(settings.get("real_orders_enabled"))
        result["active_trade_count"] = len(list(active_trades or []))
        self.state.last_preflight = result
        return result

    def place_entry_buy_limit(self, mode_guard: ModeGuard, adapter: Any, order_request: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
        blockers = []
        if self.state.stop_new_entries:
            blockers.append("Stop New Entries is active.")
        if self.state.safe_mode:
            blockers.append("Safe Mode is active; new real entries are blocked.")
        if not preflight.get("allowed"):
            blockers.extend(preflight.get("blockers") or ["Real preflight did not pass."])
        if str(order_request.get("order_type") or "").upper() != "LIMIT":
            blockers.append("Only BUY LIMIT entries are allowed for Options Auto real orders.")
        if str(order_request.get("transaction_type") or "").upper() != "BUY":
            blockers.append("Real entry order must be BUY.")
        if blockers:
            return {"allowed": False, "real_order_sent": False, "order_stage": "BLOCKED", "blockers": list(dict.fromkeys(blockers))}
        mode_guard.assert_real_order_allowed()
        response = adapter.place_entry_buy_limit(
            tradingsymbol=order_request["tradingsymbol"],
            quantity=int(order_request["quantity"]),
            price=float(order_request["price"]),
            exchange=order_request.get("exchange") or "NFO",
            product=order_request.get("product") or "NRML",
            tag=order_request.get("tag") or "OPTIONS_AUTO",
        )
        order_id = response.get("value") or response.get("order_id")
        order = {**order_request, "order_id": order_id, "status": "OPEN", "source": "OPTIONS_AUTO_REAL", "placed_at": iso_now()}
        return {
            "allowed": bool(response.get("ok")),
            "real_order_sent": bool(response.get("ok")),
            "order_stage": "ENTRY_ORDER_OPEN" if response.get("ok") else "ENTRY_ORDER_FAILED",
            "entry_order": order,
            "api_response": response,
            "blockers": [] if response.get("ok") else [response.get("error") or "Kite entry order failed."],
        }

    def protection_orders_from_fill(self, trade_plan: dict[str, Any], fill: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        actual_entry = _number(fill.get("average_price"), trade_plan.get("entry_price"))
        quantity = int(_number(fill.get("filled_quantity"), trade_plan.get("quantity")))
        option_atr14 = _number(trade_plan.get("option_atr14"), trade_plan.get("atr14"))
        tick = _number(trade_plan.get("tick_size"), 0.05)
        stop_distance = max(
            option_atr14 * _number(settings.get("atr_stoploss_multiplier"), 1.0),
            actual_entry * _number(settings.get("min_stoploss_pct"), 3.0) / 100.0,
            _number(settings.get("minimum_stoploss_points"), 2.0),
        )
        target_distance = stop_distance * _number(settings.get("risk_reward_multiplier"), 1.3)
        stoploss = round_to_tick(actual_entry - stop_distance, tick)
        target = round_to_tick(actual_entry + target_distance, tick)
        trigger = stoploss
        stop_limit = round_to_tick(max(tick, stoploss - tick), tick)
        base = {
            "tradingsymbol": trade_plan.get("tradingsymbol"),
            "exchange": trade_plan.get("exchange") or "NFO",
            "product": trade_plan.get("product") or "NRML",
            "quantity": quantity,
            "tag": "OPTIONS_AUTO",
        }
        return {
            "actual_entry": actual_entry,
            "target": target,
            "stoploss": stoploss,
            "target_order": {**base, "transaction_type": "SELL", "order_type": "LIMIT", "price": target},
            "stoploss_order": {**base, "transaction_type": "SELL", "order_type": "SL", "trigger_price": trigger, "price": stop_limit},
        }

    def reconcile(
        self,
        local_orders: list[dict[str, Any]] | None,
        broker_orders: list[dict[str, Any]] | None,
        positions: list[dict[str, Any]] | dict[str, Any] | None = None,
        trade_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.reconciliation.reconcile(local_orders, broker_orders, positions, trade_plan)
        self.state.last_reconciliation = result
        return result

    def stop_new_entries(self, source: str = "UI", reason: str = "") -> dict[str, Any]:
        self.state.stop_new_entries = True
        event = {
            "timestamp": iso_now(),
            "source": source or "UI",
            "reason": reason or "Operator stopped new real entries.",
            "state": "STOP_NEW_ENTRIES",
        }
        return {**event, "runtime": self.state.to_dict()}

    def enter_safe_mode(self, source: str = "UI", reason: str = "") -> dict[str, Any]:
        self.state.safe_mode = True
        self.state.stop_new_entries = True
        event = {
            "timestamp": iso_now(),
            "source": source or "UI",
            "reason": reason or "Safe Mode activated.",
            "state": "SAFE_MODE",
        }
        return {**event, "runtime": self.state.to_dict()}

    def emergency_exit_plan(
        self,
        mode_guard: ModeGuard,
        positions: list[dict[str, Any]] | dict[str, Any] | None,
        settings: dict[str, Any],
        confirmed: bool = False,
    ) -> dict[str, Any]:
        settings = dict(settings or {})
        rows = self.reconciliation._normalise_positions(positions)
        blockers: list[str] = []
        if mode_guard.mode != MODE_REAL:
            blockers.append("Emergency exit planning for broker positions is REAL-mode only.")
        if not confirmed:
            blockers.append("Emergency exit requires explicit confirmation.")

        actions = []
        for position in rows:
            quantity = self._position_quantity(position)
            if quantity == 0:
                continue
            symbol = position.get("tradingsymbol") or position.get("symbol") or ""
            exit_side = "SELL" if quantity > 0 else "BUY"
            actions.append({
                "tradingsymbol": symbol,
                "transaction_type": exit_side,
                "quantity": abs(quantity),
                "order_type": "LIMIT",
                "reason": "Emergency position close plan; dry-run only by default.",
            })

        if settings.get("allow_real_emergency_orders"):
            blockers.append("Emergency order sending is not enabled in Options Auto yet; use broker terminal/manual supervision.")
        result = {
            "allowed": False,
            "state": "EMERGENCY_PLAN_DRY_RUN",
            "blockers": blockers,
            "actions": actions,
            "dry_run": True,
            "orders_sent": 0,
            "timestamp": iso_now(),
        }
        self.state.emergency_history.append(result)
        return result

    def snapshot(self) -> dict[str, Any]:
        return self.state.to_dict()

    def _result(self, state: str, blockers: list[str], warnings: list[str], evidence: dict[str, Any]) -> dict[str, Any]:
        return {
            "allowed": not blockers,
            "state": state,
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": list(dict.fromkeys(warnings)),
            "evidence": evidence,
        }

    def _safe_margin(self, client: Any) -> float | None:
        value = self._safe_client_call(client, ("available_margin",), priority="RECONCILIATION")
        if value not in ("", None):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        margins = self._safe_client_call(client, ("margins",), priority="RECONCILIATION")
        if isinstance(margins, dict):
            for segment in ("equity", "commodity"):
                available = margins.get(segment, {}).get("available", {})
                for key in ("live_balance", "cash", "opening_balance", "net"):
                    raw = available.get(key)
                    if raw not in ("", None):
                        try:
                            return float(raw)
                        except (TypeError, ValueError):
                            return None
        return None

    def _safe_client_call(self, client: Any, names: tuple[str, ...], priority: str = "QUOTE") -> Any:
        for name in names:
            if hasattr(client, name):
                result = self.api.call(name, lambda name=name: getattr(client, name)(), priority=priority)
                return result.get("value") if result.get("ok") else None
            kite = getattr(client, "kite", None)
            if kite and hasattr(kite, name):
                result = self.api.call(name, lambda name=name: getattr(kite, name)(), priority=priority)
                return result.get("value") if result.get("ok") else None
        return None

    def _position_quantity(self, position: dict[str, Any]) -> int:
        try:
            return int(float(position.get("quantity") or position.get("net_quantity") or 0))
        except (TypeError, ValueError):
            return 0


def results_folder_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return os.path.isdir(path) and os.access(path, os.W_OK)
    except OSError:
        return False


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
