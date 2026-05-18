import unittest

from web_app import WebTradeBotApp


class FailingTickerClient:
    def stop_ticker(self):
        raise RuntimeError("Can't stop reactor that isn't running.")


class WebAppDisconnectTests(unittest.TestCase):
    def test_disconnect_clears_connection_and_preserves_paper_balance_when_ticker_stop_fails(self):
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["PAPER"] = FailingTickerClient()
        app.zerodha_auth_profiles["PAPER"] = {"user_name": "Test User"}
        app.zerodha_auth_login_times["PAPER"] = "2026-05-18 09:00:00"
        app.account_margins["PAPER"] = {"available": 100, "updated_at": "now", "error": ""}
        app.executor.zerodha = app.zerodha_clients_by_mode["PAPER"]

        result = app.disconnect_zerodha("PAPER")

        self.assertTrue(result["disconnected"])
        self.assertIsNone(app.zerodha_clients_by_mode["PAPER"])
        self.assertIsNone(app.executor.zerodha)
        self.assertEqual(app.zerodha_auth_profiles["PAPER"], None)
        self.assertEqual(app.account_margins["PAPER"]["available"], app.paper_balance_value())


if __name__ == "__main__":
    unittest.main()
