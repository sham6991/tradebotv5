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


if __name__ == "__main__":
    unittest.main()
