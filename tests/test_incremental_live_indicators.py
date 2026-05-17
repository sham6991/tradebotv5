import unittest
from datetime import datetime, timedelta

import pandas as pd

from indicators import append_clean_candle, clean_and_add_indicators
from strategy import append_option_formula_row, ensure_option_formula_columns, has_option_formula_columns


def candle_rows(count):
    base = datetime(2026, 5, 10, 9, 15)
    rows = []
    for index in range(count):
        open_price = 100 + index * 0.5
        close = open_price + ((index % 5) - 2) * 0.4
        rows.append({
            "datetime": base + timedelta(minutes=index),
            "open": open_price,
            "high": max(open_price, close) + 1 + (index % 3) * 0.2,
            "low": min(open_price, close) - 1 - (index % 2) * 0.2,
            "close": close,
            "volume": 1000 + index * 17,
        })
    return rows


class IncrementalLiveIndicatorTests(unittest.TestCase):
    def test_rsi_uses_wilder_smoothing_after_first_period(self):
        closes = [100, 101, 102, 101, 103, 104, 103, 105, 106, 107, 106, 108, 109, 110, 111, 109]
        rows = candle_rows(len(closes))
        for row, close in zip(rows, closes):
            row["close"] = close
            row["open"] = close
            row["high"] = close
            row["low"] = close

        frame = clean_and_add_indicators(pd.DataFrame(rows))
        first_avg_gain = 14 / 14
        first_avg_loss = 3 / 14
        smoothed_gain = ((first_avg_gain * 13) + 0) / 14
        smoothed_loss = ((first_avg_loss * 13) + 2) / 14
        expected = 100 - (100 / (1 + (smoothed_gain / smoothed_loss)))

        self.assertAlmostEqual(float(frame.iloc[15]["RSI"]), expected, places=8)

    def test_incremental_nifty_append_matches_full_indicator_recompute(self):
        rows = candle_rows(30)
        incremental = clean_and_add_indicators(pd.DataFrame(rows[:-1]))
        incremental = append_clean_candle(incremental, rows[-1])
        full = clean_and_add_indicators(pd.DataFrame(rows))

        for column in ("EMA20", "EMA50", "RSI"):
            self.assertAlmostEqual(
                float(incremental.iloc[-1][column]),
                float(full.iloc[-1][column]),
                places=8,
                msg=column,
            )

    def test_incremental_option_append_matches_full_formula_recompute(self):
        rows = candle_rows(30)
        incremental = ensure_option_formula_columns(clean_and_add_indicators(pd.DataFrame(rows[:-1])))
        incremental = append_clean_candle(incremental, rows[-1])
        incremental = append_option_formula_row(incremental)
        full = ensure_option_formula_columns(clean_and_add_indicators(pd.DataFrame(rows)))

        self.assertTrue(has_option_formula_columns(incremental))
        for column in (
            "Candle Body",
            "Candle Range",
            "Close Position Score",
            "Volume Ratio",
            "Breakout Score",
            "Compression Score",
            "Expansion Score",
            "Buy Score",
            "Sell Score",
            "Momentum Acceleration Score",
            "Early Breakout Probability Score",
        ):
            self.assertAlmostEqual(
                float(incremental.iloc[-1][column]),
                float(full.iloc[-1][column]),
                places=8,
                msg=column,
            )
        self.assertEqual(incremental.iloc[-1]["Buy Entry"], full.iloc[-1]["Buy Entry"])
        self.assertEqual(incremental.iloc[-1]["Sell Entry"], full.iloc[-1]["Sell Entry"])
        self.assertEqual(incremental.iloc[-1]["High Probability Buy"], full.iloc[-1]["High Probability Buy"])


if __name__ == "__main__":
    unittest.main()
