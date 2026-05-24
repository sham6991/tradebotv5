import unittest
from datetime import datetime

import pandas as pd

from execution_v2 import Executor


class FakeZerodha:
    def historical_candles(self, instrument_token, from_date, to_date, interval="5minute"):
        self.last_interval = interval
        self.last_range = (from_date, to_date)
        return pd.DataFrame([
            {
                "datetime": datetime(2026, 5, 18, 9, 15),
                "open": 100,
                "high": 105,
                "low": 99,
                "close": 103,
                "volume": 10,
            }
        ])


class LiveHistoryFetchTests(unittest.TestCase):
    def test_fetch_live_history_uses_passed_settings_without_executor_settings(self):
        executor = Executor(zerodha=FakeZerodha())

        nifty, options = executor.fetch_live_history(
            256265,
            [{"token": 123456, "tradingsymbol": "NIFTY26MAY25000CE"}],
            days=1,
            interval="3minute",
            settings={"buy_limit_score_low": 40},
        )

        self.assertFalse(nifty.empty)
        self.assertEqual(len(options), 1)
        self.assertIn("Early Score", options[0].columns)
        self.assertEqual(options[0].attrs["tradingsymbol"], "NIFTY26MAY25000CE")


if __name__ == "__main__":
    unittest.main()
