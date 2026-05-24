import unittest
from datetime import datetime, timedelta

import pandas as pd

from fast_ohlcv_entry import decide_entry_type


def base_rows():
    start = datetime(2026, 5, 21, 9, 15)
    return [
        {
            "datetime": start + timedelta(minutes=3 * index),
            "open": 100,
            "high": 110,
            "low": 90,
            "close": 101,
            "volume": 1000,
        }
        for index in range(10)
    ]


class FastOhlcvEntryTests(unittest.TestCase):
    def test_market_entry_requires_main_trigger_and_no_rejection(self):
        rows = base_rows()
        rows.append({
            "datetime": datetime(2026, 5, 21, 9, 45),
            "open": 100,
            "high": 112,
            "low": 99,
            "close": 111,
            "volume": 1500,
        })

        decision = decide_entry_type(pd.DataFrame(rows), 10, {})

        self.assertEqual(decision["Final Decision"], "MARKET ENTRY")
        self.assertEqual(decision["Entry Type"], "MARKET")
        self.assertEqual(decision["Early Score"], 60)
        self.assertEqual(decision["Main Fast Trigger Passed"], "YES")
        self.assertEqual(decision["Rejection Active"], "NO")

    def test_buy_limit_entry_uses_bounded_offset(self):
        rows = base_rows()
        rows.append({
            "datetime": datetime(2026, 5, 21, 9, 45),
            "open": 96,
            "high": 112,
            "low": 94,
            "close": 104,
            "volume": 900,
        })

        decision = decide_entry_type(pd.DataFrame(rows), 10, {})

        self.assertEqual(decision["Final Decision"], "BUY LIMIT ENTRY")
        self.assertEqual(decision["Entry Type"], "BUY LIMIT")
        self.assertEqual(decision["Early Score"], 45)
        self.assertEqual(decision["Main Fast Trigger Passed"], "YES")
        self.assertEqual(decision["Buy Limit Price"], 102)

    def test_high_score_that_misses_market_shape_falls_back_to_buy_limit(self):
        rows = base_rows()
        rows.append({
            "datetime": datetime(2026, 5, 21, 9, 45),
            "open": 96,
            "high": 112,
            "low": 94,
            "close": 104,
            "volume": 1500,
        })

        decision = decide_entry_type(pd.DataFrame(rows), 10, {})

        self.assertEqual(decision["Early Score"], 55)
        self.assertEqual(decision["Main Fast Trigger Passed"], "YES")
        self.assertEqual(decision["Rejection Active"], "NO")
        self.assertLess(decision["ClosePosition"], 60)
        self.assertEqual(decision["Final Decision"], "BUY LIMIT ENTRY")
        self.assertEqual(decision["Entry Type"], "BUY LIMIT")

    def test_buy_limit_offset_is_rounded_to_two_decimals(self):
        rows = base_rows()
        rows.append({
            "datetime": datetime(2026, 5, 21, 9, 45),
            "open": 96,
            "high": 112,
            "low": 94,
            "close": 104,
            "volume": 900,
        })
        settings = {
            "buy_limit_offset_multiplier": 0.0617,
            "minimum_offset": 0,
            "maximum_offset": 10,
        }

        decision = decide_entry_type(pd.DataFrame(rows), 10, settings)

        self.assertEqual(decision["Final Decision"], "BUY LIMIT ENTRY")
        self.assertEqual(decision["Limit Offset"], 1.23)
        self.assertAlmostEqual(decision["Buy Limit Price"], 102.77)

    def test_live_candle_with_aggressive_off_can_only_form_setup(self):
        rows = base_rows()
        rows.append({
            "datetime": datetime(2026, 5, 21, 9, 45),
            "open": 100,
            "high": 112,
            "low": 99,
            "close": 111,
            "volume": 1500,
        })

        decision = decide_entry_type(pd.DataFrame(rows), 10, {}, current_candle_closed=False)

        self.assertEqual(decision["Final Decision"], "WAIT")
        self.assertEqual(decision["Setup Status"], "SETUP FORMING")
        self.assertEqual(decision["Buy Entry"], "")

    def test_hard_upper_wick_rejection_blocks_trade(self):
        rows = base_rows()
        rows.append({
            "datetime": datetime(2026, 5, 21, 9, 45),
            "open": 100,
            "high": 130,
            "low": 99,
            "close": 105,
            "volume": 1500,
        })

        decision = decide_entry_type(pd.DataFrame(rows), 10, {})

        self.assertEqual(decision["Final Decision"], "NO TRADE")
        self.assertEqual(decision["Rejection Active"], "YES")
        self.assertIn("hard_upper_wick_rejection", decision["Rejection Reason"])


if __name__ == "__main__":
    unittest.main()
