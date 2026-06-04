import unittest

from intraday.models import IntradaySettings
from intraday.order_request import entry_order, stoploss_order, target_order


class IntradayModelsAndOrdersTests(unittest.TestCase):
    def valid_payload(self):
        return {
            "mode": "PAPER",
            "stocks": ["NSE:INFY", "NSE:RELIANCE", "NSE:TCS", "NSE:HDFCBANK", "NSE:ICICIBANK"],
            "minimum_entry_score": 65,
            "minimum_risk_reward": 1.5,
        }

    def test_settings_require_exactly_five_unique_stocks(self):
        settings = IntradaySettings.from_payload(self.valid_payload())
        self.assertEqual(len(settings.stocks), 5)
        self.assertEqual(settings.stocks[0].key, "NSE:INFY")

        payload = self.valid_payload()
        payload["stocks"] = ["INFY", "INFY", "TCS", "HDFCBANK", "ICICIBANK"]
        with self.assertRaises(ValueError):
            IntradaySettings.from_payload(payload)

    def test_real_mode_requires_explicit_confirmation(self):
        payload = self.valid_payload()
        payload["mode"] = "REAL"
        with self.assertRaises(ValueError):
            IntradaySettings.from_payload(payload)
        payload["confirm_real_mode"] = True
        self.assertEqual(IntradaySettings.from_payload(payload).mode, "REAL")

    def test_entry_order_defaults_to_mis_limit(self):
        request = entry_order("INFY", "LONG", 10, 1525.5, session_id="SESSION1234567890")
        request.validate(market_orders_enabled=False)
        params = request.to_kite_params()
        self.assertEqual(params["variety"], "regular")
        self.assertEqual(params["exchange"], "NSE")
        self.assertEqual(params["tradingsymbol"], "INFY")
        self.assertEqual(params["transaction_type"], "BUY")
        self.assertEqual(params["product"], "MIS")
        self.assertEqual(params["order_type"], "LIMIT")
        self.assertEqual(params["validity"], "DAY")
        self.assertLessEqual(len(params["tag"]), 20)

    def test_stoploss_and_target_transactions_match_position_side(self):
        stop = stoploss_order("INFY", "LONG", 10, trigger_price=1518, limit_price=1517.5)
        target = target_order("INFY", "SHORT", 10, target_price=1505)
        self.assertEqual(stop.to_kite_params()["transaction_type"], "SELL")
        self.assertEqual(stop.to_kite_params()["order_type"], "SL")
        self.assertEqual(target.to_kite_params()["transaction_type"], "BUY")
        self.assertEqual(target.to_kite_params()["order_type"], "LIMIT")


if __name__ == "__main__":
    unittest.main()
