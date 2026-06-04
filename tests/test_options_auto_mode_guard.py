import unittest

from options_auto.constants import REAL_EXECUTION_DISABLED_REASON
from options_auto.core.mode_guard import ModeGuard
from options_auto.terminal_service import OptionsAutoTerminalService


class OptionsAutoModeGuardTests(unittest.TestCase):
    def test_paper_mode_blocks_real_order_api(self):
        guard = ModeGuard(mode="PAPER")

        with self.assertRaisesRegex(PermissionError, "Paper mode cannot call real order APIs"):
            guard.assert_no_real_order_in_paper()

    def test_real_order_execution_is_disabled_in_foundation_phase(self):
        guard = ModeGuard(mode="REAL", real_mode_confirmed=True, real_orders_enabled=False)

        with self.assertRaisesRegex(PermissionError, "disabled"):
            guard.assert_real_order_allowed()

        self.assertEqual(guard.audit_log[-1].reason, REAL_EXECUTION_DISABLED_REASON)

    def test_service_real_dry_run_does_not_enable_real_orders(self):
        service = OptionsAutoTerminalService("results", kite_client_provider=lambda _mode: object())
        result = service.real_dry_run({"settings": {"confirm_real_mode": True}, "spot": 22500})

        self.assertFalse(result["real_execution_enabled"])
        self.assertIn("disabled", result["real_execution_reason"])
        self.assertEqual(result["session"]["status"], "REAL_DRY_RUN_ONLY")


if __name__ == "__main__":
    unittest.main()

