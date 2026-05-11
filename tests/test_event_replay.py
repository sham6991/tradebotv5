import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from event_logger import KILL_SWITCH_ACTIVATED, ORDER_OPEN, ORDER_PARTIAL_FILL, StructuredEventLogger
from event_replay import build_session_replay, format_replay_report, format_timeline_lines, load_session_timeline
from sqlite_store import TradingStore


class EventReplayTests(unittest.TestCase):
    def test_load_session_timeline_combines_events_and_order_history(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})
            logger = StructuredEventLogger(store, session_id="S1", source="test")
            logger.log(
                ORDER_OPEN,
                "INFO",
                "Order E1 opened",
                order_id="E1",
                status="OPEN",
            )
            store.log_order_history({
                "Session Trade No": 1,
                "Timestamp": "2026-05-10 10:00:00",
                "Instrument / Symbol": "NIFTY25000CE",
                "Action": "BUY",
                "Order Type": "LIMIT",
                "Quantity": 75,
                "Order Status": "OPEN",
                "Zerodha Order ID": "E1",
            })

            timeline = load_session_timeline(db_path, session_id="S1")

            self.assertEqual(len(timeline), 2)
            self.assertEqual({item["kind"] for item in timeline}, {"event", "order_history"})
            lines = format_timeline_lines(timeline)
            self.assertTrue(any("EVENT ORDER_OPEN" in line for line in lines))
            self.assertTrue(any("ORDER BUY OPEN E1" in line for line in lines))

    def test_build_session_replay_summarizes_and_highlights_risk_events(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})
            logger = StructuredEventLogger(store, session_id="S1", source="test")
            logger.log(
                ORDER_PARTIAL_FILL,
                "WARN",
                "Partial entry fill converted to protected position",
                order_id="E1",
                status="OPEN",
                payload={"filled_quantity": 25, "requested_quantity": 75},
            )
            logger.log(
                KILL_SWITCH_ACTIVATED,
                "CRITICAL",
                "KILL SWITCH ACTIVE: manual review",
                status="BLOCKED",
            )
            store.log_order_history({
                "Session Trade No": 1,
                "Timestamp": "2026-05-10 10:00:01",
                "Instrument / Symbol": "NIFTY25000CE",
                "Action": "BUY",
                "Order Type": "LIMIT",
                "Quantity": 75,
                "Order Status": "REJECTED",
                "Zerodha Order ID": "E2",
                "Error / Rejection Reason": "BROKER REJECTED",
            })

            replay = build_session_replay(db_path, session_id="S1")

            self.assertEqual(replay["summary"]["total_items"], 3)
            self.assertEqual(replay["summary"]["event_type_counts"][ORDER_PARTIAL_FILL], 1)
            self.assertEqual(replay["summary"]["event_type_counts"][KILL_SWITCH_ACTIVATED], 1)
            self.assertEqual(replay["summary"]["order_status_counts"]["REJECTED"], 1)
            self.assertEqual(len(replay["highlights"]["partial_events"]), 1)
            self.assertEqual(len(replay["highlights"]["critical_events"]), 1)
            self.assertEqual(len(replay["highlights"]["rejected_or_failed_orders"]), 1)

            report_lines = format_replay_report(replay)
            self.assertIn("SESSION REPLAY", report_lines[0])
            self.assertTrue(any("Critical events: 1" in line for line in report_lines))
            self.assertTrue(any("Rejected/failed orders: 1" in line for line in report_lines))

    def test_load_session_timeline_handles_missing_tables(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "empty.db")
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")
                conn.commit()

            timeline = load_session_timeline(db_path)
            replay = build_session_replay(db_path)

            self.assertEqual(timeline, [])
            self.assertEqual(replay["summary"]["total_items"], 0)


if __name__ == "__main__":
    unittest.main()
