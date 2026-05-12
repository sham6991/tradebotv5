import json
import os
import tempfile
import unittest

from event_logger import KILL_SWITCH_ACTIVATED, StructuredEventLogger
from session_audit import build_session_audit, write_session_audit
from sqlite_store import TradingStore


class SessionAuditTests(unittest.TestCase):
    def test_build_session_audit_summarizes_events_and_order_history(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="LIVE", settings={"session_id": "S1", "lot_size": 75})
            store.start_session("LIVE", "S1", 100000)
            logger = StructuredEventLogger(store, session_id="S1", source="test")
            logger.log(
                KILL_SWITCH_ACTIVATED,
                "CRITICAL",
                "KILL SWITCH ACTIVE: test",
                status="BLOCKED",
                payload={"reason": "test"},
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

            audit = build_session_audit(db_path, session_id="S1")

            self.assertEqual(audit["totals"]["events"], 1)
            self.assertEqual(audit["totals"]["order_history_rows"], 1)
            self.assertEqual(audit["totals"]["kill_switch_events"], 1)
            self.assertEqual(audit["event_counts"][KILL_SWITCH_ACTIVATED], 1)
            self.assertEqual(audit["order_action_counts"]["BUY"], 1)
            self.assertEqual(audit["order_status_counts"]["OPEN"], 1)
            self.assertTrue(audit["settings_profile"]["settings_hash"])
            self.assertTrue(audit["settings_profile"]["settings_version"].startswith("settings-v1-"))

    def test_write_session_audit_writes_json_file(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            output_path = os.path.join(temp_dir, "audit.json")
            TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})

            audit = write_session_audit(db_path, output_path, session_id="S1")

            self.assertTrue(os.path.exists(output_path))
            with open(output_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["session_id"], "S1")
            self.assertEqual(saved["totals"], audit["totals"])


if __name__ == "__main__":
    unittest.main()
