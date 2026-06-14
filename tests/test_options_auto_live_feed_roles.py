import unittest
from datetime import datetime

from options_auto.data.options_live_feed import OptionsLiveFeed


class OptionsAutoLiveFeedRoleTests(unittest.TestCase):
    def test_locked_contract_subscribe_reports_missing_tick_roles(self):
        feed = OptionsLiveFeed()

        snapshot = feed.subscribe_locked_contracts(
            1001,
            {"instrument_token": 2001, "tradingsymbol": "NIFTY26JUN22500CE", "exchange": "NFO"},
            {"instrument_token": 2002, "tradingsymbol": "NIFTY26JUN22500PE", "exchange": "NFO"},
        )

        self.assertEqual(snapshot["health"]["expected_roles"], ["INDEX", "CE", "PE"])
        self.assertEqual(snapshot["health"]["missing_roles"], ["INDEX", "CE", "PE"])
        self.assertTrue(snapshot["health"]["role_statuses"]["CE"]["missing"])
        self.assertTrue(snapshot["health"]["role_statuses"]["PE"]["missing"])

    def test_missing_locked_option_roles_clear_as_ticks_arrive(self):
        feed = OptionsLiveFeed()
        feed.subscribe_locked_contracts(
            1001,
            {"instrument_token": 2001, "tradingsymbol": "NIFTY26JUN22500CE", "exchange": "NFO"},
            {"instrument_token": 2002, "tradingsymbol": "NIFTY26JUN22500PE", "exchange": "NFO"},
        )
        now = datetime.now()

        feed.on_tick({"instrument_token": 1001, "last_price": 23350, "timestamp": now}, role="INDEX")
        snapshot = feed.snapshot({"max_tick_age_seconds": 3})

        self.assertEqual(snapshot["health"]["missing_roles"], ["CE", "PE"])
        self.assertIn("INDEX", snapshot["health"]["fresh_roles"])
        self.assertFalse(snapshot["health"]["all_expected_roles_fresh"])


if __name__ == "__main__":
    unittest.main()
