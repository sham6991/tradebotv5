import unittest
from datetime import datetime

from candle_builder import CandleBuilder


class CandleBuilderTests(unittest.TestCase):
    def test_builds_ohlcv_and_completes_on_next_bucket(self):
        builder = CandleBuilder(1)

        self.assertIsNone(builder.add_tick("NIFTY", 100, datetime(2026, 5, 10, 9, 15, 1), 1000))
        self.assertIsNone(builder.add_tick("NIFTY", 102, datetime(2026, 5, 10, 9, 15, 20), 1010))
        self.assertIsNone(builder.add_tick("NIFTY", 99, datetime(2026, 5, 10, 9, 15, 50), 1020))
        completed = builder.add_tick("NIFTY", 101, datetime(2026, 5, 10, 9, 16, 0), 1030)

        self.assertEqual(completed["datetime"], datetime(2026, 5, 10, 9, 15))
        self.assertEqual(completed["open"], 100)
        self.assertEqual(completed["high"], 102)
        self.assertEqual(completed["low"], 99)
        self.assertEqual(completed["close"], 99)
        self.assertEqual(completed["volume"], 20)
        self.assertEqual(builder.snapshot("NIFTY")["open"], 101)

    def test_drops_invalid_ticks_without_breaking_active_candle(self):
        builder = CandleBuilder(1)
        builder.add_tick("NIFTY", 100, datetime(2026, 5, 10, 9, 15, 1), 1000)

        self.assertIsNone(builder.add_tick("NIFTY", "bad-price", datetime(2026, 5, 10, 9, 15, 2), 1001))
        self.assertIsNone(builder.add_tick("NIFTY", 0, datetime(2026, 5, 10, 9, 15, 3), 1002))
        self.assertIsNone(builder.add_tick("", 101, datetime(2026, 5, 10, 9, 15, 4), 1003))

        self.assertEqual(builder.stats["invalid_ticks"], 3)
        self.assertEqual(builder.snapshot("NIFTY")["close"], 100)

    def test_drops_out_of_order_ticks_to_protect_close_price(self):
        builder = CandleBuilder(1)
        builder.add_tick("NIFTY", 100, datetime(2026, 5, 10, 9, 15, 30), 1000)
        builder.add_tick("NIFTY", 105, datetime(2026, 5, 10, 9, 15, 10), 1010)

        self.assertEqual(builder.stats["out_of_order_ticks"], 1)
        self.assertEqual(builder.snapshot("NIFTY")["close"], 100)

    def test_flush_completed_closes_stale_buckets(self):
        builder = CandleBuilder(1)
        builder.add_tick("NIFTY", 100, datetime(2026, 5, 10, 9, 15, 10), 1000)
        builder.add_tick("OPTION_0", 50, datetime(2026, 5, 10, 9, 15, 20), 2000)

        completed = builder.flush_completed(datetime(2026, 5, 10, 9, 16, 5))

        self.assertEqual([key for key, _row in completed], ["NIFTY", "OPTION_0"])
        self.assertEqual(builder.snapshot(), {})

    def test_volume_reset_is_conservative(self):
        builder = CandleBuilder(1)
        builder.add_tick("NIFTY", 100, datetime(2026, 5, 10, 9, 15, 1), 1000)
        builder.add_tick("NIFTY", 101, datetime(2026, 5, 10, 9, 15, 2), 1010)
        builder.add_tick("NIFTY", 102, datetime(2026, 5, 10, 9, 15, 3), 5)

        self.assertEqual(builder.snapshot("NIFTY")["volume"], 10)
        self.assertEqual(builder.stats["volume_reset_ticks"], 1)

    def test_max_keys_limits_unbounded_state_growth(self):
        builder = CandleBuilder(1, max_keys=2)
        builder.add_tick("A", 100, datetime(2026, 5, 10, 9, 15, 1), 1)
        builder.add_tick("B", 100, datetime(2026, 5, 10, 9, 15, 1), 1)
        builder.add_tick("C", 100, datetime(2026, 5, 10, 9, 15, 1), 1)

        self.assertEqual(set(builder.snapshot().keys()), {"A", "B"})
        self.assertEqual(builder.stats["dropped_key_limit_ticks"], 1)


if __name__ == "__main__":
    unittest.main()
