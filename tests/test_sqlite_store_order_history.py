import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime

from sqlite_store import TradingStore


class TradingStoreOrderHistoryTests(unittest.TestCase):
    def test_order_history_logs_partial_fill_columns(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})

            store.log_order_history({
                "Session Trade No": 1,
                "Timestamp": "2026-05-10 10:00:00",
                "Instrument / Symbol": "NIFTY25000CE",
                "Option Type": "CE",
                "Action": "BUY",
                "Order Type": "LIMIT",
                "Quantity": 75,
                "Ordered Quantity": 75,
                "Filled Quantity": 25,
                "Pending Quantity": 50,
                "Cancelled Quantity": 0,
                "Is Partial Fill": "YES",
                "Order Status": "OPEN",
                "Entry Price": 100,
                "Early Score": 80,
                "Zerodha Order ID": "OID1",
                "Related Trade ID": "T1",
            })

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT ordered_quantity, filled_quantity, pending_quantity,
                           cancelled_quantity, is_partial_fill
                    FROM order_history
                    """
                ).fetchone()

            self.assertEqual(row, (75, 25, 50, 0, 1))

    def test_existing_order_history_table_is_migrated(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE order_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        session_id TEXT
                    )
                    """
                )

            TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})

            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(order_history)").fetchall()
                }

            self.assertIn("ordered_quantity", columns)
            self.assertIn("filled_quantity", columns)
            self.assertIn("pending_quantity", columns)
            self.assertIn("cancelled_quantity", columns)
            self.assertIn("is_partial_fill", columns)

    def test_session_events_and_order_history_include_settings_profile(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(
                db_path,
                mode="LIVE",
                settings={"session_id": "S1", "balance": 100000, "lot_size": 75},
            )
            store.start_session("LIVE", "S1", 100000)
            store.log_event("INFO", "profiled event", {"event_type": "TEST"})
            store.log_order_history({
                "Session Trade No": 1,
                "Timestamp": "2026-05-10 10:00:00",
                "Action": "BUY",
                "Order Status": "OPEN",
            })

            with closing(sqlite3.connect(db_path)) as conn:
                session_row = conn.execute(
                    "SELECT settings_hash, settings_version, settings_schema_version FROM live_sessions"
                ).fetchone()
                event_payload = conn.execute("SELECT payload FROM events").fetchone()[0]
                order_payload = conn.execute("SELECT data FROM order_history").fetchone()[0]

            self.assertTrue(session_row[0])
            self.assertTrue(session_row[1].startswith("settings-v2-"))
            self.assertEqual(session_row[2], 2)
            self.assertIn(session_row[0], event_payload)
            self.assertIn(session_row[0], order_payload)

    def test_logs_completed_candles_with_upsert(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="PAPER", settings={"session_id": "S1"})

            row = {
                "datetime": datetime(2026, 5, 12, 9, 15),
                "open": 100,
                "high": 105,
                "low": 99,
                "close": 104,
                "volume": 1000,
            }
            metadata = {
                "instrument": "NIFTY25000CE",
                "tradingsymbol": "NIFTY25000CE",
                "option_type": "CE",
            }
            store.log_candle("OPTION_0", row, metadata)
            store.log_candle("OPTION_0", {**row, "close": 106}, metadata)

            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT session_id, stream_name, instrument, option_type, candle_time, close
                    FROM candles
                    """
                ).fetchall()

        self.assertEqual(rows, [("S1", "OPTION_0", "NIFTY25000CE", "CE", "2026-05-12 09:15:00", 106)])


if __name__ == "__main__":
    unittest.main()
