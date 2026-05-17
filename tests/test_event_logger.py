import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

import pandas as pd

from event_logger import (
    ENTRY_FILLED,
    KILL_SWITCH_ACTIVATED,
    ORDER_CANCELLED,
    ORDER_COMPLETE,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    ORDER_REJECTED,
    PROTECTIVE_ORDER_PLACED,
    PROTECTIVE_ORDER_VERIFICATION_FAILED,
    PROTECTIVE_ORDER_VERIFICATION_PASSED,
    StructuredEventLogger,
    normalize_event,
)
from execution_v2 import LivePaperSession
from sqlite_store import TradingStore


def option_frame():
    df = pd.DataFrame([
        {
            "datetime": "2026-05-10 09:15:00",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
        }
    ])
    df.attrs["instrument"] = "NIFTY25000CE"
    df.attrs["tradingsymbol"] = "NIFTY25000CE"
    df.attrs["option_type"] = "CE"
    return df


def signal(option):
    return {
        "option": option,
        "option_index": 0,
        "type": "CE",
        "instrument": "NIFTY25000CE",
        "tradingsymbol": "NIFTY25000CE",
        "entry": 100,
        "entry_offset": -2,
        "entry_index": 0,
        "score_row": {"Buy Score": 85, "Buy Entry": "BUY"},
    }


def session_with_store(store):
    option = option_frame()
    session = LivePaperSession(
        option_frame(),
        [option, option_frame()],
        {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
        {
            "cooldown": 0,
            "balance": 100000,
            "lot_size": 1,
            "max_trades": 1,
            "profit_points": 20,
            "safety_points": 10,
            "square_off_time": "",
        },
        save_path=None,
        mode="PAPER",
    )
    session.store = store
    session.event_logger = StructuredEventLogger(
        store,
        session_id=session.session_id,
        source="LivePaperSession",
    )
    return session, option


def event_payloads(db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT payload FROM events ORDER BY id").fetchall()
    return [json.loads(row[0]) for row in rows]


class EventLoggerTests(unittest.TestCase):
    def test_normalize_event_builds_queryable_payload_shape(self):
        event = normalize_event(
            ORDER_PARTIAL_FILL,
            "warn",
            "Partial entry fill converted to protected position",
            session_id="S1",
            order_id="OID1",
            trade_no=7,
            status="OPEN",
            side="BUY",
            instrument="NIFTY25000CE",
            quantity=75,
            payload={"filled_quantity": 25, "pending_quantity": 50},
            source="test",
        )

        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["event_type"], ORDER_PARTIAL_FILL)
        self.assertTrue(event["known_event_type"])
        self.assertEqual(event["level"], "WARN")
        self.assertEqual(event["session_id"], "S1")
        self.assertEqual(event["order_id"], "OID1")
        self.assertEqual(event["trade_no"], 7)
        self.assertEqual(event["status"], "OPEN")
        self.assertEqual(event["side"], "BUY")
        self.assertEqual(event["instrument"], "NIFTY25000CE")
        self.assertEqual(event["quantity"], 75)
        self.assertEqual(event["payload"]["filled_quantity"], 25)

    def test_structured_logger_writes_payload_to_existing_events_table(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})
            logger = StructuredEventLogger(store, session_id="S1", source="test")

            logger.log(
                KILL_SWITCH_ACTIVATED,
                "CRITICAL",
                "KILL SWITCH ACTIVE: test block",
                status="BLOCKED",
                payload={"reason": "test block"},
            )

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute("SELECT level, message, payload FROM events").fetchone()

            payload = json.loads(row[2])
            self.assertEqual(row[0], "CRITICAL")
            self.assertEqual(row[1], "KILL SWITCH ACTIVE: test block")
            self.assertEqual(payload["event_type"], KILL_SWITCH_ACTIVATED)
            self.assertEqual(payload["session_id"], "S1")
            self.assertEqual(payload["status"], "BLOCKED")
            self.assertEqual(payload["payload"]["reason"], "test block")

    def test_entry_fill_logs_structured_lifecycle_event(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="PAPER", settings={"session_id": "S1"})
            session, option = session_with_store(store)

            session._open_position_from_fill(signal(option), 75, "E1", 100, 25)

            entry_events = [
                payload for payload in event_payloads(db_path)
                if payload.get("event_type") == ENTRY_FILLED
            ]
            self.assertEqual(len(entry_events), 1)
            event = entry_events[0]
            self.assertEqual(event["order_id"], "E1")
            self.assertEqual(event["status"], "COMPLETE")
            self.assertEqual(event["side"], "BUY")
            self.assertEqual(event["instrument"], "NIFTY25000CE")
            self.assertEqual(event["quantity"], 25)
            self.assertEqual(event["payload"]["entry_price"], 100)
            self.assertEqual(event["payload"]["target_price"], 120)
            self.assertEqual(event["payload"]["stoploss_price"], 90)

    def test_protective_order_logs_structured_lifecycle_event(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="PAPER", settings={"session_id": "S1"})
            session, option = session_with_store(store)
            position = {
                "trade_no": 1,
                "signal": signal(option),
                "entry_time": "2026-05-10 09:15:00",
                "entry_price": 100,
                "target": 120,
                "stoploss": 90,
                "quantity": 25,
                "contract_lot_size": 75,
                "entry_order_id": "E1",
            }

            session._record_exit_order_placed(
                position,
                "TARGET SELL LIMIT PLACED",
                "T1",
                120,
                "",
                "SELL LIMIT",
            )

            protective_events = [
                payload for payload in event_payloads(db_path)
                if payload.get("event_type") == PROTECTIVE_ORDER_PLACED
            ]
            self.assertEqual(len(protective_events), 1)
            event = protective_events[0]
            self.assertEqual(event["order_id"], "T1")
            self.assertEqual(event["status"], "OPEN")
            self.assertEqual(event["side"], "SELL")
            self.assertEqual(event["instrument"], "NIFTY25000CE")
            self.assertEqual(event["quantity"], 25)
            self.assertEqual(event["payload"]["entry_order_id"], "E1")
            self.assertEqual(event["payload"]["limit_price"], 120)
            self.assertEqual(event["payload"]["order_kind"], "SELL LIMIT")

    def test_protective_order_verification_logs_passed_event(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})
            session, option = session_with_store(store)
            session.mode = "LIVE"
            session.zerodha = object()
            position = {
                "trade_no": 1,
                "signal": signal(option),
                "entry_price": 100,
                "target": 120,
                "stoploss": 90,
                "quantity": 25,
                "entry_order_id": "E1",
                "target_order_id": "T1",
                "stoploss_order_id": "S1",
            }
            session.last_order_status_by_id.update({"T1": "OPEN", "S1": "TRIGGER PENDING"})

            self.assertTrue(session._verify_protective_orders(position))

            verification_events = [
                payload for payload in event_payloads(db_path)
                if payload.get("event_type") == PROTECTIVE_ORDER_VERIFICATION_PASSED
            ]
            self.assertEqual(len(verification_events), 1)
            event = verification_events[0]
            self.assertEqual(event["status"], "PASSED")
            self.assertEqual(event["payload"]["target_order_id"], "T1")
            self.assertEqual(event["payload"]["stoploss_order_id"], "S1")

    def test_protective_order_verification_logs_failed_event(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})
            session, option = session_with_store(store)
            session.mode = "LIVE"
            session.zerodha = object()
            position = {
                "trade_no": 1,
                "signal": signal(option),
                "entry_price": 100,
                "target": 120,
                "stoploss": 90,
                "quantity": 25,
                "entry_order_id": "E1",
                "target_order_id": "",
                "stoploss_order_id": "S1",
            }
            session.last_order_status_by_id["S1"] = "TRIGGER PENDING"

            self.assertFalse(session._verify_protective_orders(position))

            verification_events = [
                payload for payload in event_payloads(db_path)
                if payload.get("event_type") == PROTECTIVE_ORDER_VERIFICATION_FAILED
            ]
            self.assertEqual(len(verification_events), 1)
            self.assertIn("target order id missing", verification_events[0]["payload"]["findings"])

    def test_pending_entry_order_records_buy_history_event(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="PAPER", settings={"session_id": "S1"})
            session, option = session_with_store(store)

            session._record_pending_entry_order(signal(option), 0, "OPEN", "E1", 75, 75)

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT action, order_type, quantity, zerodha_order_id
                    FROM order_history
                    """
                ).fetchone()

            self.assertEqual(row, ("BUY", "LIMIT", 75, "E1"))

    def test_exit_failure_records_sell_history_event(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="PAPER", settings={"session_id": "S1"})
            session, option = session_with_store(store)
            position = {
                "trade_no": 1,
                "signal": signal(option),
                "entry_time": "2026-05-10 09:15:00",
                "entry_price": 100,
                "quantity": 25,
                "contract_lot_size": 75,
                "entry_order_id": "E1",
            }

            session._record_exit_failure(position, 0, "MANUAL SQUARE OFF", "FAILED: broker timeout")

            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT action, order_type, quantity, parent_order_id, exit_reason
                    FROM order_history
                    """
                ).fetchone()

            self.assertEqual(row, ("MANUAL SELL", "MARKET", 25, "E1", "EXIT FAILED: MANUAL SQUARE OFF"))

    def test_order_status_change_maps_to_structured_event_types(self):
        store = None
        session, _option = session_with_store(store)

        cases = [
            ("OPEN", ORDER_OPEN, "INFO"),
            ("TRIGGER PENDING", ORDER_OPEN, "INFO"),
            ("COMPLETE", ORDER_COMPLETE, "INFO"),
            ("FILLED", ORDER_COMPLETE, "INFO"),
            ("REJECTED", ORDER_REJECTED, "ERROR"),
            ("CANCELLED", ORDER_CANCELLED, "WARN"),
        ]
        for status, event_type, level in cases:
            with self.subTest(status=status):
                self.assertEqual(session._event_type_for_order_status(status), event_type)
                self.assertEqual(session._event_level_for_order_status(status), level)

    def test_order_status_change_logs_structured_lifecycle_event(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="LIVE", settings={"session_id": "S1"})
            session, option = session_with_store(store)
            session.last_order_status_by_id["E1"] = "OPEN"

            session._record_order_status_change(
                "E1",
                "COMPLETE",
                signal(option),
                "ENTRY ORDER FILLED",
                entry_order_id="E1",
                quantity=75,
                lot_size=75,
                entry=100,
            )

            complete_events = [
                payload for payload in event_payloads(db_path)
                if payload.get("event_type") == ORDER_COMPLETE
            ]
            self.assertEqual(len(complete_events), 1)
            event = complete_events[0]
            self.assertEqual(event["order_id"], "E1")
            self.assertEqual(event["status"], "COMPLETE")
            self.assertEqual(event["side"], "BUY")
            self.assertEqual(event["instrument"], "NIFTY25000CE")
            self.assertEqual(event["quantity"], 75)
            self.assertEqual(event["payload"]["previous_status"], "OPEN")
            self.assertEqual(event["payload"]["new_status"], "COMPLETE")
            self.assertEqual(event["payload"]["entry_order_id"], "E1")
            self.assertEqual(event["payload"]["reason"], "ENTRY ORDER FILLED")
            self.assertEqual(event["payload"]["entry_price"], 100)
            self.assertEqual(event["payload"]["lot_size"], 75)


if __name__ == "__main__":
    unittest.main()
