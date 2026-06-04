import unittest

from options_auto.constants import REAL_EXECUTION_DISABLED_REASON
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
    def test_paper_mode_real_preflight_is_blocked_without_static_ip_check(self):
        service = OptionsAutoTerminalService("results")

        result = service.real_preflight_check({"mode": "PAPER", "settings": {"mode": "PAPER"}})

        self.assertFalse(result["allowed"])
        self.assertEqual(result["state"], "BLOCKED_BY_MODE")
        self.assertNotIn("Static IP", "; ".join(result["blockers"]))

    def test_real_preflight_requires_static_ip_only_in_real_mode(self):
        client = FakeRealClient()
        service = OptionsAutoTerminalService("results", kite_client_provider=lambda mode: client if mode == "LIVE" else None)

        blocked = service.real_preflight_check({
            "mode": "REAL",
            "settings": {"mode": "REAL", "confirm_real_mode": True, "real_orders_enabled": True},
            "kite_profile": {"user_id": "REAL1"},
            "broker_orders": [],
            "positions": [],
            "market_open": True,
            "instruments_valid": True,
            "static_ip_confirmed": False,
        })
        allowed = service.real_preflight_check({
            "mode": "REAL",
            "settings": {"mode": "REAL", "confirm_real_mode": True, "real_orders_enabled": True, "static_ip_confirmed": True},
            "kite_profile": {"user_id": "REAL1"},
            "broker_orders": [],
            "positions": [],
            "market_open": True,
            "instruments_valid": True,
        })

        self.assertIn("Static IP", "; ".join(blocked["blockers"]))
        self.assertTrue(allowed["allowed"])
        self.assertTrue(allowed["dry_run_ready"])

    def test_real_preflight_reports_orders_disabled_as_final_guard(self):
        client = FakeRealClient()
        service = OptionsAutoTerminalService("results", kite_client_provider=lambda mode: client if mode == "LIVE" else None)

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
        self.assertIn(REAL_EXECUTION_DISABLED_REASON, result["blockers"])

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


if __name__ == "__main__":
    unittest.main()
