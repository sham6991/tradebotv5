import unittest

import pandas as pd

from options_auto.indicators.option_metrics import intrinsic_value, moneyness
from options_auto.indicators.technicals import enrich_technicals, market_depth_imbalance


class OptionsAutoIndicatorsTests(unittest.TestCase):
    def test_enrich_technicals_adds_required_columns_without_oi(self):
        frame = pd.DataFrame({
            "datetime": pd.date_range("2026-06-01 09:15", periods=30, freq="3min"),
            "open": [100 + index for index in range(30)],
            "high": [101 + index for index in range(30)],
            "low": [99 + index for index in range(30)],
            "close": [100.5 + index for index in range(30)],
            "volume": [1000 + index * 10 for index in range(30)],
        })

        enriched = enrich_technicals(frame)

        for column in ("ema9", "ema20", "ema50", "vwap", "rsi14", "atr14", "relative_volume"):
            self.assertIn(column, enriched.columns)
        self.assertNotIn("oi_change", enriched.columns)

    def test_option_metrics_are_directional(self):
        self.assertEqual(intrinsic_value(22550, 22500, "CE"), 50)
        self.assertEqual(intrinsic_value(22450, 22500, "PE"), 50)
        self.assertEqual(moneyness(22500, 22500, "CE"), "ATM")
        self.assertEqual(moneyness(22500, 22400, "CE"), "ITM")
        self.assertEqual(moneyness(22500, 22600, "PE"), "ITM")

    def test_depth_imbalance_handles_missing_depth(self):
        self.assertEqual(market_depth_imbalance("", None), 0.0)


if __name__ == "__main__":
    unittest.main()

