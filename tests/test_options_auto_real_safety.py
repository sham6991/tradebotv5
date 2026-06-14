import time
import tempfile
import unittest

from options_auto.core.mode_guard import ModeGuard
from options_auto.execution.real_execution_controller import RealExecutionController
from options_auto.execution.reconciliation import ReconciliationEngine
from options_auto.terminal_service import OptionsAutoTerminalService


class FakeRealClient:
    def __init__(self, orders=None, positions=None, margin=50000):
        self._orders = list(orders or [])
        self._positions = positions if positions is not None else []
        self._margin = margin
        self.orders_called = 0
        self.positions_called = 0
        self.limit_orders = []
        self.stoploss_orders = []

    def profile(self):
        return {"user_id": "REAL1"}

    def available_margin(self):
        return self._margin

    def orders(self):
        self.orders_called += 1
        return list(self._orders)

    def positions(self):
        self.positions_called += 1
        return self._positions

    def place_limit_order(self, **kwargs):
        self.limit_orders.append(dict(kwargs))
        return f"REAL-{len(self.limit_orders)}"

    def place_stoploss_limit_order(self, **kwargs):
        self.stoploss_orders.append(dict(kwargs))
        return f"SL-{len(self.stoploss_orders)}"

    def cancel_order(self, order_id):
        for order in self._orders:
            if str(order.get("order_id")) == str(order_id):
                order["status"] = "CANCELLED"
                return {"order_id": order_id, "status": "CANCELLED"}
        return {"order_id": order_id, "status": "NOT_FOUND"}


class FakeFinalValidationEngine:
    def __init__(self):
        self.calls = []

    def validate_final_entry(self, plan, latest_quote, settings, state):
        self.calls.append({
            "plan": dict(plan or {}),
            "latest_quote": dict(latest_quote or {}),
            "settings": dict(settings or {}),
            "state": dict(state or {}),
        })
        return {"allowed": True, "blockers": [], "warnings": [], "entry_limit": 142.45, "reason": "ok"}


class FakeEmergencyAdapter:
    def __init__(self):
        self.orders = []

    def place_emergency_sell_limit(self, **kwargs):
        self.orders.append(dict(kwargs))
        return {"ok": True, "value": f"EMG{len(self.orders)}"}


class FakeKillSwitchRealClient(FakeRealClient):
    def place_limit_order(self, **kwargs):
        self.limit_orders.append(dict(kwargs))
        order_id = f"REAL-{len(self.limit_orders)}"
        order = {**kwargs, "order_id": order_id, "status": "COMPLETE"}
        self._orders.append(order)
        if str(kwargs.get("transaction_type") or "").upper() == "SELL":
            for position in self._positions:
                if str(position.get("tradingsymbol")) == str(kwargs.get("tradingsymbol")):
                    position["quantity"] = 0
                    position["net_quantity"] = 0
        return order_id


def auto_order(order_id, tradingsymbol="NIFTY26JUN22500CE", transaction_type="BUY", status="OPEN", order_type="LIMIT", quantity=50):
    return {
        "order_id": order_id,
        "tradingsymbol": tradingsymbol,
        "transaction_type": transaction_type,
        "status": status,
        "order_type": order_type,
        "quantity": quantity,
        "tag": "OPTIONS_AUTO",
    }


class OptionsAutoRealSafetyTests(unittest.TestCase):
    def _service(self, kite_client_provider=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return OptionsAutoTerminalService(temp_dir.name, kite_client_provider=kite_client_provider)

    def test_paper_mode_real_preflight_is_blocked_without_static_ip_check(self):
        service = self._service()

        result = service.real_preflight_check({"mode": "PAPER", "settings": {"mode": "PAPER"}})

        self.assertFalse(result["allowed"])
        self.assertEqual(result["state"], "BLOCKED_BY_MODE")
        self.assertNotIn("Static IP", "; ".join(result["blockers"]))

    def test_real_preflight_requires_static_ip_only_in_real_mode(self):
        client = FakeRealClient()
        service = self._service(lambda mode: client if mode == "LIVE" else None)

        blocked = service.real_preflight_check({
            "mode": "REAL",
            "settings": {"mode": "REAL", "confirm_real_mode": True, "real_orders_enabled": True, "dry_run_real_only": False},
            "kite_profile": {"user_id": "REAL1"},
            "broker_orders": [],
            "positions": [],
            "market_open": True,
            "instruments_valid": True,
            "static_ip_confirmed": False,
        })
        allowed = service.real_preflight_check({
            "mode": "REAL",
            "settings": {"mode": "REAL", "confirm_real_mode": True, "real_orders_enabled": True, "dry_run_real_only": False, "static_ip_confirmed": True},
            "kite_profile": {"user_id": "REAL1"},
            "broker_orders": [],
            "positions": [],
            "market_open": True,
            "instruments_valid": True,
        })

        self.assertIn("Static IP", "; ".join(blocked["blockers"]))
        self.assertTrue(allowed["allowed"])
        self.assertTrue(allowed["dry_run_ready"])

    def test_real_preflight_does_not_enable_live_orders_from_connection_only(self):
        client = FakeRealClient()
        service = self._service(lambda mode: client if mode == "LIVE" else None)

        result = service.real_preflight_check({
            "mode": "REAL",
            "settings": {"mode": "REAL", "confirm_real_mode": True, "static_ip_confirmed": True},
            "kite_profile": {"user_id": "REAL1"},
            "broker_orders": [],
            "positions": [],
            "market_open": True,
            "instruments_valid": True,
        })

        self.assertFalse(result["allowed"])
        self.assertTrue(result["dry_run_ready"])
        self.assertFalse(result["real_orders_enabled"])
        self.assertIn("scan-only mode", "; ".join(result["blockers"]).lower())

    def test_real_preflight_respects_explicit_dry_run_override(self):
        client = FakeRealClient()
        service = self._service(lambda mode: client if mode == "LIVE" else None)

        result = service.real_preflight_check({
            "mode": "REAL",
            "settings": {"mode": "REAL", "confirm_real_mode": True, "static_ip_confirmed": True, "dry_run_real_only": True},
            "kite_profile": {"user_id": "REAL1"},
            "broker_orders": [],
            "positions": [],
            "market_open": True,
            "instruments_valid": True,
        })

        self.assertFalse(result["allowed"])
        self.assertTrue(result["dry_run_ready"])
        self.assertIn("scan-only mode", "; ".join(result["blockers"]).lower())

    def test_real_preflight_blocks_when_real_orders_disabled_even_without_dry_run(self):
        client = FakeRealClient()
        service = self._service(lambda mode: client if mode == "LIVE" else None)

        result = service.real_preflight_check({
            "mode": "REAL",
            "settings": {
                "mode": "REAL",
                "confirm_real_mode": True,
                "static_ip_confirmed": True,
                "dry_run_real_only": False,
                "real_orders_enabled": False,
            },
            "kite_profile": {"user_id": "REAL1"},
            "broker_orders": [],
            "positions": [],
            "market_open": True,
            "instruments_valid": True,
        })

        self.assertFalse(result["allowed"])
        self.assertTrue(result["dry_run_ready"])
        self.assertFalse(result["real_orders_enabled"])
        self.assertIn("Real orders are disabled", "; ".join(result["blockers"]))

    def test_start_real_engine_does_not_force_enable_real_flags(self):
        client = FakeRealClient()
        service = self._service(lambda mode: client if mode == "LIVE" else None)

        result = service.start_real_engine({
            "settings": {
                "confirm_real_mode": True,
                "static_ip_confirmed": True,
                "dry_run_real_only": True,
                "real_orders_enabled": False,
                "real_auto_entry_enabled": False,
            },
            "kite_profile": {"user_id": "REAL1"},
            "broker_orders": [],
            "positions": [],
            "market_open": True,
            "instruments_valid": True,
        })

        self.assertTrue(result["allowed"])
        self.assertTrue(result["real_engine_started"])
        self.assertFalse(result["real_order_ready"])
        self.assertTrue(result["live_scan"]["running"])
        self.assertFalse(service.settings["real_orders_enabled"])
        self.assertTrue(service.settings["dry_run_real_only"])
        self.assertFalse(service.settings["real_auto_entry_enabled"])
        self.assertEqual(client.limit_orders, [])
        service.stop_live_scan({"mode": "REAL"})

    def test_duplicate_and_manual_orders_block_reconciliation(self):
        engine = ReconciliationEngine()
        duplicate = auto_order("A1")
        manual = {
            "order_id": "M1",
            "tradingsymbol": "NIFTY26JUN22500CE",
            "transaction_type": "BUY",
            "status": "OPEN",
            "quantity": 50,
            "tag": "",
        }

        result = engine.reconcile([], [duplicate, manual], [], {"tradingsymbol": "NIFTY26JUN22500CE", "quantity": 50})

        self.assertFalse(result["ok"])
        self.assertEqual(result["duplicate_orders"], [duplicate])
        self.assertEqual(result["unknown_manual_orders"], [manual])

    def test_unprotected_position_is_detected(self):
        position = {"tradingsymbol": "NIFTY26JUN22500CE", "quantity": 50}
        entry = auto_order("ENTRY1", status="COMPLETE", transaction_type="BUY")

        result = ReconciliationEngine().reconcile([entry], [entry], [position])

        self.assertFalse(result["ok"])
        self.assertEqual(result["unprotected_positions"], [position])

    def test_emergency_plan_is_dry_run_and_sends_no_orders(self):
        controller = RealExecutionController()
        guard = ModeGuard(mode="REAL", real_mode_confirmed=True, real_orders_enabled=False)

        result = controller.emergency_exit_plan(
            guard,
            [{"tradingsymbol": "NIFTY26JUN22500CE", "quantity": 50}],
            {"allow_real_emergency_orders": False},
            confirmed=True,
        )

        self.assertFalse(result["allowed"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["orders_sent"], 0)
        self.assertEqual(result["actions"][0]["transaction_type"], "SELL")

    def test_emergency_flatten_enabled_places_filled_quantity_aggressive_limit_only(self):
        controller = RealExecutionController()
        guard = ModeGuard(mode="REAL", real_mode_confirmed=True, real_orders_enabled=True)
        adapter = FakeEmergencyAdapter()

        result = controller.emergency_exit_plan(
            guard,
            [{"tradingsymbol": "NIFTY26JUN22500CE", "exchange": "NFO", "product": "NRML", "quantity": 50, "last_price": 112.4}],
            {"allow_real_emergency_flatten": True, "emergency_flatten_max_slippage_points": 2.0},
            confirmed=True,
            adapter=adapter,
        )

        self.assertTrue(result["allowed"], result["blockers"])
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["orders_sent"], 1)
        self.assertEqual(adapter.orders[0]["tradingsymbol"], "NIFTY26JUN22500CE")
        self.assertEqual(adapter.orders[0]["quantity"], 50)
        self.assertEqual(adapter.orders[0]["price"], 110.4)

    def test_options_auto_real_kill_switch_cancels_flattens_and_verifies_flat(self):
        client = FakeKillSwitchRealClient(
            orders=[{**auto_order("TARGET1", transaction_type="SELL", status="OPEN", order_type="LIMIT"), "exchange": "NFO"}],
            positions=[{"tradingsymbol": "NIFTY26JUN22500CE", "exchange": "NFO", "product": "NRML", "quantity": 50, "net_quantity": 50, "last_price": 112.4}],
        )
        service = self._service(lambda mode: client if mode == "LIVE" else None)

        result = service.kill_switch({
            "mode": "REAL",
            "settings": {"emergency_flatten_verify_timeout_seconds": 0},
            "reason": "operator emergency",
        })

        runtime = result["real_runtime"]
        self.assertTrue(result["killed"])
        self.assertTrue(runtime["flat_verified"], runtime)
        self.assertEqual(runtime["cancelled_orders"][0]["order"]["order_id"], "TARGET1")
        self.assertEqual(client._orders[0]["status"], "CANCELLED")
        self.assertEqual(runtime["flatten"]["orders_sent"], 1)
        self.assertEqual(client.limit_orders[-1]["transaction_type"], "SELL")
        self.assertEqual(runtime["open_orders_after"], [])
        self.assertEqual(runtime["positions_after"], [])

    def test_stop_new_entries_blocks_otherwise_clean_preflight(self):
        controller = RealExecutionController()
        controller.stop_new_entries("TEST")
        guard = ModeGuard(mode="REAL", real_mode_confirmed=True, real_orders_enabled=True, kite_profile={"user_id": "REAL1"})
        client = FakeRealClient()

        result = controller.preflight(
            guard,
            client,
            {"real_orders_enabled": True, "static_ip_confirmed": True},
            broker_orders=[],
            positions=[],
            profile={"user_id": "REAL1"},
        )

        self.assertFalse(result["allowed"])
        self.assertIn("Stop New Entries is active.", result["blockers"])

    def test_real_stop_syncs_running_option_position_from_zerodha_state(self):
        target = auto_order("TARGET1", transaction_type="SELL", order_type="LIMIT")
        stoploss = auto_order("SL1", transaction_type="SELL", order_type="SL")
        target.update({"exchange": "NFO", "price": 148.95})
        stoploss.update({"exchange": "NFO", "price": 137.4, "trigger_price": 137.45})
        position = {
            "tradingsymbol": "NIFTY26JUN22500CE",
            "exchange": "NFO",
            "product": "NRML",
            "quantity": 50,
            "average_price": 142.4,
            "last_price": 145.0,
            "pnl": 130.0,
        }
        client = FakeRealClient(orders=[target, stoploss], positions={"net": [position]})
        service = self._service(lambda mode: client if mode == "LIVE" else None)

        result = service.stop_live_scan({"mode": "REAL"})

        trade = result["session"]["active_trades"][0]
        self.assertEqual(result["session"]["status"], "REAL_STOPPED")
        self.assertEqual(trade["tradingsymbol"], "NIFTY26JUN22500CE")
        self.assertEqual(trade["entry_price"], 142.4)
        self.assertEqual(trade["target_order_id"], "TARGET1")
        self.assertEqual(trade["stoploss_order_id"], "SL1")
        self.assertTrue(trade["position_protected"])

    def test_reconciliation_detects_broker_open_position_when_local_flat(self):
        target = auto_order("TARGET1", transaction_type="SELL", order_type="LIMIT")
        stoploss = auto_order("SL1", transaction_type="SELL", order_type="SL", status="TRIGGER PENDING")
        target.update({"exchange": "NFO", "price": 148.95})
        stoploss.update({"exchange": "NFO", "price": 137.4, "trigger_price": 137.45})
        position = {
            "tradingsymbol": "NIFTY26JUN22500CE",
            "exchange": "NFO",
            "product": "NRML",
            "quantity": 50,
            "average_price": 142.4,
            "last_price": 145.0,
            "pnl": 130.0,
        }
        client = FakeRealClient(orders=[target, stoploss], positions={"net": [position]})
        service = self._service(lambda mode: client if mode == "LIVE" else None)
        service.session.orders = [target, stoploss]

        result = service.real_reconcile({"broker_orders": [target, stoploss], "positions": {"net": [position]}})

        lifecycle = result["real_order_lifecycle"]
        self.assertTrue(result["ok"], result["blockers"])
        self.assertEqual(lifecycle["broker_open_positions"][0]["tradingsymbol"], "NIFTY26JUN22500CE")
        self.assertEqual(result["session"]["active_trades"][0]["tradingsymbol"], "NIFTY26JUN22500CE")
        self.assertEqual(result["session"]["status"], "REAL_POSITION_ACTIVE")

    def test_unprotected_position_blocks_new_entries_after_lifecycle_reconcile(self):
        position = {"tradingsymbol": "NIFTY26JUN22500CE", "exchange": "NFO", "quantity": 50}
        client = FakeRealClient(orders=[], positions={"net": [position]})
        service = self._service(lambda mode: client if mode == "LIVE" else None)

        reconcile = service.real_reconcile({"broker_orders": [], "positions": {"net": [position]}})
        preflight = service.real_preflight_check({
            "mode": "REAL",
            "settings": {"mode": "REAL", "confirm_real_mode": True, "real_orders_enabled": True, "dry_run_real_only": False, "static_ip_confirmed": True},
            "kite_profile": {"user_id": "REAL1"},
            "broker_orders": [],
            "positions": {"net": [position]},
            "market_open": True,
            "instruments_valid": True,
        })

        self.assertEqual(reconcile["real_order_lifecycle"]["state"], "UNPROTECTED_POSITION")
        self.assertFalse(preflight["allowed"])
        self.assertIn("Safe Mode is active", "; ".join(preflight["blockers"]))

    def test_guarded_real_order_sends_buy_limit_only_after_preflight_and_final_validation(self):
        client = FakeRealClient(margin=100000)
        service = self._service(lambda mode: client if mode == "LIVE" else None)
        service.low_latency_engine = FakeFinalValidationEngine()
        now_epoch = time.time()
        decision = {
            "allowed": True,
            "blockers": [],
            "selected_contract": {
                "tradingsymbol": "NIFTY26JUN22500CE",
                "instrument_token": "123",
                "instrument_type": "CE",
                "option_type": "CE",
                "exchange": "NFO",
                "lot_size": 50,
                "tick_size": 0.05,
                "ltp": 142.4,
                "bid": 142.35,
                "ask": 142.45,
            },
            "trade_plan": {
                "tradingsymbol": "NIFTY26JUN22500CE",
                "instrument_token": "123",
                "exchange": "NFO",
                "side": "CE",
                "entry_price": 142.45,
                "quantity": 50,
                "lot_size": 50,
                "tick_size": 0.05,
                "product": "NRML",
            },
            "ready_trade_plan": {
                "status": "READY",
                "last_refreshed_epoch": now_epoch,
                "side": "CE",
                "entry_plan": {"entry_limit": 142.45, "signal_price": 142.4},
                "premium_context": {"premium_return_1": 1.0, "option_atr14": 5},
                "market_context": {
                    "market_cue": {"recommended_side": "CE"},
                    "regime": {"recommended_side": "CE"},
                },
            },
            "data_quality": {"allowed": True},
            "governor": {"allowed": True},
            "market_cue": {"recommended_side": "CE"},
            "regime": {"recommended_side": "CE"},
        }

        result = service.place_real_order({
            "mode": "REAL",
            "decision": decision,
            "settings": {"mode": "REAL", "confirm_real_mode": True, "real_orders_enabled": True, "dry_run_real_only": False, "static_ip_confirmed": True},
            "kite_profile": {"user_id": "REAL1"},
            "quotes": {"123": {"ltp": 142.4, "bid": 142.35, "ask": 142.45, "option_atr14": 5, "premium_return_1": 1.0, "age_seconds": 0}},
            "broker_orders": [],
            "positions": [],
            "market_open": True,
            "instruments_valid": True,
        })

        self.assertTrue(result["real_order_sent"])
        self.assertEqual(result["order_stage"], "ENTRY_ORDER_OPEN")
        self.assertEqual(len(client.limit_orders), 1)
        order = client.limit_orders[0]
        self.assertEqual(order["transaction_type"], "BUY")
        self.assertEqual(order["tradingsymbol"], "NIFTY26JUN22500CE")
        self.assertEqual(order["quantity"], 50)
        self.assertEqual(order["price"], 142.45)
        self.assertEqual(order["exchange"], "NFO")
        self.assertEqual(order["product"], "NRML")
        self.assertEqual(order["variety"], "regular")
        self.assertEqual(order["validity"], "DAY")
        self.assertEqual(order["tag"], "OPTIONS_AUTO")
        self.assertEqual(client.stoploss_orders, [])
        self.assertEqual(len(service.low_latency_engine.calls), 1)

    def test_protection_orders_from_fill_uses_actual_fill_and_sell_sl_limit(self):
        controller = RealExecutionController()

        result = controller.protection_orders_from_fill(
            {"tradingsymbol": "NIFTY26JUN22500CE", "exchange": "NFO", "product": "NRML", "quantity": 50, "option_atr14": 4, "tick_size": 0.05},
            {"average_price": 140.0, "filled_quantity": 50},
            {"atr_stoploss_multiplier": 1.0, "min_stoploss_pct": 3.0, "minimum_stoploss_points": 2.0, "risk_reward_multiplier": 1.3},
        )

        self.assertEqual(result["actual_entry"], 140.0)
        self.assertEqual(result["stoploss"], 135.8)
        self.assertEqual(result["target"], 145.45)
        self.assertEqual(result["target_order"]["transaction_type"], "SELL")
        self.assertEqual(result["target_order"]["order_type"], "LIMIT")
        self.assertEqual(result["stoploss_order"]["transaction_type"], "SELL")
        self.assertEqual(result["stoploss_order"]["order_type"], "SL")
        self.assertEqual(result["stoploss_order"]["trigger_price"], 135.8)


if __name__ == "__main__":
    unittest.main()
