import tempfile
import unittest

from options_auto.core.watchdog import WatchdogService
from options_auto.terminal_service import OptionsAutoTerminalService


class OptionsAutoWatchdogHealthTests(unittest.TestCase):
    def test_stale_data_triggers_degraded_mode(self):
        result = WatchdogService().evaluate(
            {"mode": "PAPER", "ui_alive": True, "data_feed_alive": True, "last_update_age_seconds": 5},
            {"quote_stale_seconds": 3},
        )

        self.assertEqual(result["mode"], "DEGRADED")
        self.assertFalse(result["new_entries_allowed"])
        self.assertIn("Last update is stale.", result["blockers"])

    def test_real_kite_disconnect_blocks_new_entries(self):
        result = WatchdogService().evaluate(
            {"mode": "REAL", "ui_alive": True, "data_feed_alive": True, "kite_connected": False},
            {},
        )

        self.assertEqual(result["mode"], "DEGRADED")
        self.assertFalse(result["new_entries_allowed"])
        self.assertIn("Real mode Kite connection is down.", result["blockers"])

    def test_high_memory_pauses_slow_tasks_without_pausing_protection(self):
        result = WatchdogService().evaluate(
            {"mode": "PAPER", "ui_alive": True, "data_feed_alive": True, "memory_pct": 88},
            {},
        )

        self.assertEqual(result["mode"], "NORMAL")
        self.assertTrue(result["new_entries_allowed"])
        self.assertTrue(result["slow_tasks_paused"])
        self.assertTrue(result["order_protection_must_continue"])

    def test_unprotected_active_position_is_critical(self):
        result = WatchdogService().evaluate(
            {"mode": "REAL", "active_position": True, "position_protected": False, "kite_connected": True},
            {},
        )

        self.assertEqual(result["mode"], "CRITICAL")
        self.assertFalse(result["new_entries_allowed"])
        self.assertIn("Active position is not protected.", result["blockers"])

    def test_health_endpoint_returns_scores_and_slow_task_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)

            result = service.health_status({"mode": "PAPER", "memory_pct": 90, "latency_log": {"decision_latency_ms": 2000}})

            self.assertEqual(result["watchdog"]["mode"], "NORMAL")
            self.assertTrue(result["slow_tasks_paused"])
            self.assertIn("decision_latency_ms latency is high.", result["watchdog"]["warnings"])
            self.assertIn("daily_readiness_score", result["watchdog"])


if __name__ == "__main__":
    unittest.main()
