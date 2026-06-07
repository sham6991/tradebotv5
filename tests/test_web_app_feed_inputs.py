import unittest

from web_app import WebTradeBotApp, parse_instrument_token


class WebAppFeedInputTests(unittest.TestCase):
    def test_parse_instrument_token_accepts_numeric_strings(self):
        self.assertEqual(parse_instrument_token("256265", "NIFTY token"), 256265)
        self.assertEqual(parse_instrument_token("1,234", "Call token"), 1234)

    def test_parse_instrument_token_rejects_blank_and_symbol_values(self):
        with self.assertRaisesRegex(ValueError, "NIFTY token is required"):
            parse_instrument_token("", "NIFTY token")

        with self.assertRaisesRegex(ValueError, "Call token must be a numeric"):
            parse_instrument_token("NIFTY26MAY25000CE", "Call token")

    def test_token_map_from_payload_names_invalid_option_token(self):
        app = WebTradeBotApp()

        payload = {
            "nifty_token": "256265",
            "options": [
                {"tradingsymbol": "NIFTY26MAY25000CE", "token": "NIFTY26MAY25000CE"},
                {"tradingsymbol": "NIFTY26MAY25000PE", "token": "123456"},
            ],
        }

        with self.assertRaisesRegex(ValueError, "Call token must be a numeric"):
            app.token_map_from_payload(payload)

    def test_status_summary_payload_contains_command_center_keys(self):
        app = WebTradeBotApp()

        summary = app.status_summary_payload()

        for key in (
            "app_mode",
            "host_mode",
            "broker_update_mode",
            "postback_required",
            "postback_enabled",
            "public_callback_required",
            "market_status",
            "paper_connected",
            "real_connected",
            "feed_health",
            "current_mode",
            "real_money_state",
            "kill_switch",
            "today_pnl",
            "active_orders_count",
        ):
            self.assertIn(key, summary)
        self.assertEqual(summary["app_mode"], "LOCAL")
        self.assertEqual(summary["host_mode"], "LOCALHOST")
        self.assertEqual(summary["broker_update_mode"], "POLLING_AND_RECONCILIATION")
        self.assertFalse(summary["postback_required"])
        self.assertFalse(summary["postback_enabled"])
        self.assertFalse(summary["public_callback_required"])


if __name__ == "__main__":
    unittest.main()
