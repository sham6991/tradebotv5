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

    def test_feed_error_metrics_include_classification(self):
        executor = Executor()

        executor._handle_feed_error(503, "service unavailable")

        metrics = executor.feed_metrics()
        self.assertEqual(metrics["feed_error_category"], "network")
        self.assertEqual(metrics["feed_error_class"], "BROKER_CONNECTION_ERROR")
        executor._cancel_reconnect_timer()

    def test_tick_enqueue_applies_backpressure_before_dropping(self):
        executor = Executor()
        executor.tick_queue_size = 1
        executor.tick_queue_put_timeout = 1.0
        processed = []

        def on_ticks(ticks):
            time.sleep(0.001)
            processed.extend(ticks)

        executor._start_tick_dispatcher(on_ticks)
        try:
            for index in range(100):
                executor._enqueue_ticks([{"instrument_token": index, "last_price": index}])
            executor.tick_queue.join()
        finally:
            executor._stop_tick_dispatcher()

        self.assertEqual(len(processed), 100)
        self.assertEqual(executor.feed_metrics()["dropped_batches"], 0)


if __name__ == "__main__":
    unittest.main()
