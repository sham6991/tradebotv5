import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from web_app import WebTradeBotApp


class FakeMarginClient:
    def __init__(self, margin=100000):
        self.margin = margin
        self.calls = 0

    def available_margin(self):
        self.calls += 1
        return self.margin


class FakeHealthClient(FakeMarginClient):
    def __init__(self, margin=100000, fail_orders=False):
        super().__init__(margin)
        self.fail_orders = fail_orders
        self.profile_calls = 0
        self.order_calls = 0

    def profile(self):
        self.profile_calls += 1
        return {"user_id": "TEST"}

    def orders(self):
        self.order_calls += 1
        if self.fail_orders:
            raise RuntimeError("static IP configuration required")
        return []


def checked_at(seconds_ago=0):
    return (datetime.now() - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%d %H:%M:%S")


def network_status(**overrides):
    value = {
        "mode": "LIVE",
        "status": "Connected",
        "quality": "Good",
        "checked_at": checked_at(),
        "steps": [{"name": "Zerodha Margin", "status": "OK", "duration_ms": 10, "error": ""}],
    }
    value.update(overrides)
    return value


def recovery_status(**overrides):
    value = {
        "mode": "LIVE",
        "status": "Safe To Trade",
        "severity": "Good",
        "checked_at": checked_at(),
        "findings": [],
        "checks": [],
    }
    value.update(overrides)
    return value


class LiveStartSafetyTests(unittest.TestCase):
    def test_real_live_start_requires_fresh_network_and_recovery_checks(self):
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["LIVE"] = FakeMarginClient()

        with self.assertRaisesRegex(ValueError, "network health check"):
            app.require_real_live_start_safety()

    def test_real_live_start_safety_passes_with_fresh_checks_and_margin(self):
        client = FakeMarginClient(12345)
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["LIVE"] = client
        app.network_health["LIVE"] = network_status()
        app.recovery_status["LIVE"] = recovery_status()

        self.assertTrue(app.require_real_live_start_safety())
        self.assertEqual(client.calls, 1)
        self.assertEqual(app.account_margins["LIVE"]["available"], 12345.0)

    def test_real_live_start_blocks_stale_recovery_check(self):
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["LIVE"] = FakeMarginClient()
        app.network_health["LIVE"] = network_status()
        app.recovery_status["LIVE"] = recovery_status(checked_at=checked_at(seconds_ago=600))

        with self.assertRaisesRegex(ValueError, "recovery check"):
            app.require_real_live_start_safety()

    def test_paper_network_health_does_not_run_order_book_static_ip_check(self):
        client = FakeHealthClient(fail_orders=True)
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["PAPER"] = client
        app.check_zerodha_api_reachable = lambda: True

        result = app.run_network_health_check("PAPER")

        self.assertEqual(result["status"], "Connected")
        self.assertEqual(client.profile_calls, 1)
        self.assertEqual(client.calls, 1)
        self.assertEqual(client.order_calls, 0)
        self.assertNotIn("Zerodha Order Book", {step["name"] for step in result["steps"]})

    def test_live_network_health_keeps_order_book_static_ip_check(self):
        client = FakeHealthClient()
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["LIVE"] = client
        app.check_zerodha_api_reachable = lambda: True

        result = app.run_network_health_check("LIVE")

        self.assertEqual(result["status"], "Connected")
        self.assertEqual(client.order_calls, 1)
        self.assertIn("Zerodha Order Book", {step["name"] for step in result["steps"]})

    def test_recovery_check_blocks_restored_active_kill_switch_state(self):
        import web_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_results = web_app.RESULT_FOLDER
            web_app.RESULT_FOLDER = temp_dir
            try:
                path = os.path.join(temp_dir, "LIVE_20260523_091500_kill_switch.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({"active": True, "reason": "previous unknown broker state"}, handle)

                app = web_app.WebTradeBotApp()
                app.zerodha_clients_by_mode["LIVE"] = FakeMarginClient()

                result = app.run_recovery_check("LIVE")
            finally:
                web_app.RESULT_FOLDER = original_results

        self.assertEqual(result["severity"], "Danger")
        self.assertEqual(result["status"], "Do Not Start New Trade")
        self.assertIn("RESTORED_KILL_SWITCH_ACTIVE", {item["code"] for item in result["findings"]})

    def test_recovery_state_reads_live_subfolder_before_legacy_root(self):
        import web_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_results = web_app.RESULT_FOLDER
            web_app.RESULT_FOLDER = temp_dir
            try:
                live_dir = os.path.join(temp_dir, "real_money_trading")
                os.makedirs(live_dir)
                subfolder_path = os.path.join(live_dir, "live_open_position.json")
                with open(subfolder_path, "w", encoding="utf-8") as handle:
                    json.dump({"trade_no": "SUBFOLDER"}, handle)
                with open(os.path.join(temp_dir, "live_open_position.json"), "w", encoding="utf-8") as handle:
                    json.dump({"trade_no": "LEGACY"}, handle)

                app = web_app.WebTradeBotApp()
                position = app.read_recovery_json("LIVE", "open_position")
                rows = app.recovery_file_rows("LIVE")
            finally:
                web_app.RESULT_FOLDER = original_results

        self.assertEqual(position["trade_no"], "SUBFOLDER")
        self.assertIn(subfolder_path, {row["path"] for row in rows})


if __name__ == "__main__":
    unittest.main()
