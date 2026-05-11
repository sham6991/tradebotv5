import os
import time
import unittest
from datetime import datetime, timedelta

from candle_builder import CandleBuilder


class CandleBuilderStressTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("RUN_CANDLE_BUILDER_STRESS") == "1",
        "set RUN_CANDLE_BUILDER_STRESS=1 to run the large candle builder stress test",
    )
    def test_ten_million_tick_stream(self):
        tick_count = int(os.environ.get("CANDLE_BUILDER_STRESS_TICKS", "10000000"))
        key_count = int(os.environ.get("CANDLE_BUILDER_STRESS_KEYS", "64"))
        builder = CandleBuilder(1, max_keys=key_count)
        base = datetime(2026, 5, 10, 9, 15)
        keys = [f"TOKEN_{index}" for index in range(key_count)]

        started = time.perf_counter()
        for index in range(tick_count):
            key_index = index % key_count
            builder.add_tick(
                keys[key_index],
                100 + (index % 100) * 0.05,
                base + timedelta(seconds=index // key_count),
                index,
            )
        elapsed = time.perf_counter() - started

        self.assertEqual(builder.stats["received_ticks"], tick_count)
        self.assertEqual(builder.stats["accepted_ticks"], tick_count)
        self.assertEqual(builder.stats["invalid_ticks"], 0)
        self.assertEqual(builder.stats["out_of_order_ticks"], 0)
        self.assertLessEqual(len(builder.current), key_count)
        print(f"CandleBuilder stress: {tick_count} ticks, {key_count} keys, {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
