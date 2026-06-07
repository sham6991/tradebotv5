import unittest

from web_core.latency_tracker import LatencyTracker


class LatencyTrackerTests(unittest.TestCase):
    def test_empty_snapshot_is_safe(self):
        self.assertEqual(LatencyTracker().snapshot(), {})

    def test_records_p50_p95_and_max(self):
        tracker = LatencyTracker(max_records=10)
        for value in [10, 20, 30, 40, 50]:
            tracker.record("evaluate", value)

        row = tracker.snapshot()["evaluate"]

        self.assertEqual(row["count"], 5)
        self.assertEqual(row["last_ms"], 50)
        self.assertEqual(row["p50_ms"], 30)
        self.assertGreaterEqual(row["p95_ms"], 48)
        self.assertEqual(row["max_ms"], 50)


if __name__ == "__main__":
    unittest.main()
