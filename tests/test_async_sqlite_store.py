import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from sqlite_store import AsyncTradingStore, TradingStore


class AsyncTradingStoreTests(unittest.TestCase):
    def test_async_store_flushes_queued_event_writes_on_close(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = AsyncTradingStore(
                TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})
            )

            store.log_event("INFO", "queued event", {"event_type": "TEST"})
            self.assertTrue(store.close(timeout=5))

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute("SELECT level, message, payload FROM events").fetchone()

            self.assertEqual(row[0], "INFO")
            self.assertEqual(row[1], "queued event")
            self.assertEqual(json.loads(row[2])["event_type"], "TEST")
            self.assertEqual(store.enqueued_writes, 1)
            self.assertEqual(store.completed_writes, 1)
            self.assertEqual(store.errors, [])

    def test_state_writes_remain_synchronous_for_recovery(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = AsyncTradingStore(
                TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})
            )

            store.save_state("open_position", {"order_id": "E1"})

            self.assertEqual(store.load_state("open_position"), {"order_id": "E1"})
            self.assertTrue(store.close(timeout=5))

    def test_health_exposes_queue_status(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = AsyncTradingStore(
                TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})
            )

            health = store.health()

            self.assertTrue(health["async"])
            self.assertEqual(health["queue_size"], 0)
            self.assertIn("errors", health)
            self.assertTrue(store.close(timeout=5))


if __name__ == "__main__":
    unittest.main()
