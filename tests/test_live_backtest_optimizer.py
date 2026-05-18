import os
import tempfile
import unittest
from datetime import datetime, timedelta

import pandas as pd

from live_backtest_optimizer import OPTIMIZED_SETTING_KEYS, run_live_backtest_optimizer, setting_candidates
from tests.test_strategy_regression import settings


class FakeZerodhaHistory:
    def __init__(self):
        self.intervals = []

    def historical_candles(self, instrument_token, from_date, to_date, interval="5minute"):
        self.intervals.append(interval)
        rows = []
        base = datetime.combine(from_date.date(), datetime.min.time()).replace(hour=9, minute=15)
        token = int(instrument_token)
        for index in range(18):
            if token == 256265:
                open_price = 100 + index
                close_price = open_price + 1
                volume = 1000 + index
            else:
                open_price = 100
                close_price = 111 if index >= 7 else 101
                volume = 6000 if index >= 6 else 1000
            rows.append({
                "datetime": base + timedelta(minutes=3 * index),
                "open": open_price,
                "high": max(open_price, close_price) + 2,
                "low": min(open_price, close_price) - 2,
                "close": close_price,
                "volume": volume,
            })
        return pd.DataFrame(rows)


class LiveBacktestOptimizerTests(unittest.TestCase):
    def test_setting_candidates_include_lower_default_and_upper(self):
        self.assertEqual(setting_candidates("min_buy_score", 60), [50.0, 60.0, 70.0])
        self.assertEqual(setting_candidates("rsi_bull", 55), [45.0, 55.0, 65.0])

    def test_default_optimizer_uses_only_core_trade_settings(self):
        self.assertNotIn("entry_offset", OPTIMIZED_SETTING_KEYS)
        self.assertNotIn("cooldown", OPTIMIZED_SETTING_KEYS)
        self.assertNotIn("profit_points", OPTIMIZED_SETTING_KEYS)
        self.assertIn("rsi_bull", OPTIMIZED_SETTING_KEYS)
        self.assertIn("rsi_bear", OPTIMIZED_SETTING_KEYS)
        self.assertIn("rsi_reversal_bullish", OPTIMIZED_SETTING_KEYS)
        self.assertIn("rsi_reversal_bearish", OPTIMIZED_SETTING_KEYS)

    def test_optimizer_exports_livebacktesting_workbook(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            zerodha = FakeZerodhaHistory()
            result = run_live_backtest_optimizer(
                zerodha,
                256265,
                [
                    {"token": 111, "tradingsymbol": "NIFTY26MAY25000CE", "option_type": "CE", "strike": "25000", "expiry": "2026-05-26"},
                    {"token": 222, "tradingsymbol": "NIFTY26MAY25000PE", "option_type": "PE", "strike": "25000", "expiry": "2026-05-26"},
                ],
                "2026-05-18",
                "2026-05-18",
                "3minute",
                settings(balance=10000, lot_size=1, max_trades=2, min_buy_score=60),
                temp_dir,
            )

            self.assertTrue(os.path.basename(result["output_path"]).startswith("livebacktesting_"))
            self.assertTrue(os.path.exists(result["output_path"]))
            self.assertEqual(result["days_used"], 1)
            self.assertGreater(result["runs"], 1)
            self.assertIn("min_buy_score", result["best_settings"])
            self.assertEqual(result["best_settings"]["chart_interval"], "3minute")
            self.assertEqual(set(zerodha.intervals), {"3minute"})


if __name__ == "__main__":
    unittest.main()
