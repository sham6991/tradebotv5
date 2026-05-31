import os
import tempfile
import time
import unittest

from ui_replay import latest_replay_database, replay_table_row


class ReplayUiHelperTests(unittest.TestCase):
    def test_latest_replay_database_returns_newest_db_file(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            older = os.path.join(temp_dir, "older.db")
            newer = os.path.join(temp_dir, "newer.db")
            ignored = os.path.join(temp_dir, "note.txt")
            for path in (older, newer, ignored):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("")
            old_time = time.time() - 10
            new_time = time.time()
            os.utime(older, (old_time, old_time))
            os.utime(newer, (new_time, new_time))

            self.assertEqual(latest_replay_database(temp_dir), newer)

    def test_latest_replay_database_searches_result_subfolders(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            paper_dir = os.path.join(temp_dir, "paper_trading")
            real_dir = os.path.join(temp_dir, "real_money_trading")
            os.makedirs(paper_dir)
            os.makedirs(real_dir)
            older = os.path.join(paper_dir, "paper_trading_20260528.db")
            newer = os.path.join(real_dir, "real_money_trading_20260528.db")
            for path in (older, newer):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("")
            old_time = time.time() - 10
            new_time = time.time()
            os.utime(older, (old_time, old_time))
            os.utime(newer, (new_time, new_time))

            self.assertEqual(latest_replay_database(temp_dir), newer)

    def test_replay_table_row_formats_event_item(self):
        row = replay_table_row({
            "kind": "event",
            "timestamp": "2026-05-10 10:00:00",
            "event_type": "KILL_SWITCH_ACTIVATED",
            "level": "CRITICAL",
            "status": "BLOCKED",
            "order_id": "OID1",
            "trade_no": 3,
            "instrument": "NIFTY25000CE",
            "quantity": 75,
            "message": "KILL SWITCH ACTIVE",
        })

        self.assertEqual(row["kind"], "EVENT")
        self.assertEqual(row["event"], "KILL_SWITCH_ACTIVATED")
        self.assertEqual(row["level_status"], "CRITICAL")
        self.assertEqual(row["trade"], 3)

    def test_replay_table_row_formats_order_history_item(self):
        row = replay_table_row({
            "kind": "order_history",
            "timestamp": "2026-05-10 10:00:01",
            "action": "BUY",
            "order_status": "REJECTED",
            "order_id": "OID2",
            "related_trade_id": "S1_1",
            "instrument": "NIFTY25000CE",
            "quantity": 75,
            "error_reason": "BROKER REJECTED",
        })

        self.assertEqual(row["kind"], "ORDER")
        self.assertEqual(row["event"], "BUY")
        self.assertEqual(row["level_status"], "REJECTED")
        self.assertEqual(row["message"], "BROKER REJECTED")


if __name__ == "__main__":
    unittest.main()
