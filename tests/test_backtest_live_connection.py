import unittest

from web_app import WebTradeBotApp


class BacktestLiveClient:
    def get_nifty50_token(self):
        return 256265

    def find_option_contract(self, option_type=None, strike=None, expiry=None, name="NIFTY"):
        return {
            "tradingsymbol": f"NIFTY{expiry}{strike}{option_type}",
            "instrument_token": 123456,
            "instrument_type": option_type,
            "strike": strike,
            "expiry": expiry,
        }

    def stop_ticker(self):
        return None


class BacktestLiveConnectionTests(unittest.TestCase):
    def test_virtual_connection_blocks_real_live_login(self):
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["PAPER"] = BacktestLiveClient()

        self.assertTrue(app.connection_status("LIVE")["blocked"])
        with self.assertRaisesRegex(ValueError, "Virtual/Paper Data is already connected"):
            app.start_login({"mode": "LIVE", "api_key": "key", "api_secret": "secret"})

    def test_real_live_connection_blocks_virtual_login(self):
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["LIVE"] = BacktestLiveClient()

        self.assertTrue(app.connection_status("BACKTEST")["blocked"])
        with self.assertRaisesRegex(ValueError, "Real Money Zerodha is already connected"):
            app.start_login({"mode": "BACKTEST", "api_key": "key", "api_secret": "secret"})

    def test_backtest_optimizer_must_use_backtest_live_mode(self):
        app = WebTradeBotApp()

        with self.assertRaisesRegex(ValueError, "Virtual/Paper Zerodha"):
            app.run_live_backtest_optimizer_job({"mode": "LIVE"})

    def test_status_payload_exposes_only_two_zerodha_connections(self):
        app = WebTradeBotApp()
        payload = app.status_payload()

        self.assertEqual(set(payload["connections"]), {"PAPER", "LIVE"})
        self.assertEqual(set(payload["account_margins"]), {"PAPER", "LIVE"})
        self.assertNotIn("BACKTEST", payload["connections"])

    def test_backtest_live_fetch_uses_virtual_connection(self):
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["PAPER"] = BacktestLiveClient()

        self.assertEqual(app.fetch_nifty_token("BACKTEST")["token"], 256265)

    def test_fetch_option_returns_token_alias(self):
        app = WebTradeBotApp()
        app.zerodha_clients_by_mode["PAPER"] = BacktestLiveClient()

        contract = app.fetch_option_contract("BACKTEST", {
            "option_type": "CE",
            "strike": "25000",
            "expiry": "2026-05-26",
        })

        self.assertEqual(contract["instrument_token"], 123456)
        self.assertEqual(contract["token"], 123456)

    def test_nifty_optimizer_does_not_expose_profile_apply_method(self):
        app = WebTradeBotApp()

        self.assertFalse(hasattr(app, "apply_latest_optimizer_settings"))


if __name__ == "__main__":
    unittest.main()
