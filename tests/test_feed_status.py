import time
import unittest

from execution_v2 import Executor


class FeedStatusTests(unittest.TestCase):
    def test_effective_status_is_connected_when_recent_ticks_arrived(self):
        executor = Executor()
        executor.feed_should_run = True
        executor.feed_status = "connecting"
        executor.last_tick_received_at = time.time()

        self.assertEqual(executor.feed_metrics()["feed_status"], "connected")

    def test_effective_status_uses_raw_status_when_ticks_are_stale(self):
        executor = Executor()
        executor.feed_should_run = True
        executor.feed_status = "reconnecting_in_2s"
        executor.last_tick_received_at = time.time() - executor.feed_stale_after_seconds - 1

        self.assertEqual(executor.feed_metrics()["feed_status"], "reconnecting_in_2s")

    def test_connect_cancels_pending_reconnect_timer(self):
        executor = Executor()
        executor.feed_should_run = True
        executor._schedule_reconnect("test")

        executor._handle_feed_connect({})

        self.assertEqual(executor.feed_status, "connected")
        self.assertIsNone(executor.feed_reconnect_timer)


if __name__ == "__main__":
    unittest.main()
