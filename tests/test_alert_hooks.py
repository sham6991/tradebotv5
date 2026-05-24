import unittest
from unittest.mock import patch

import pandas as pd

from execution_v2 import LivePaperSession


def frame():
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


def signal(option=None):
    option = option if option is not None else frame()
    return {
        "option": option,
        "option_index": 0,
        "type": "CE",
        "instrument": "NIFTY25000CE",
        "tradingsymbol": "NIFTY25000CE",
        "entry": 100,
        "entry_offset": -2,
        "signal_index": 4,
        "nifty_signal_index": 4,
        "entry_index": 5,
        "score_row": {"Early Score": 85, "Buy Entry": "BUY"},
    }


def session_with_alerts(alerts):
    option = frame()
    session = LivePaperSession(
        frame(),
        [option, frame()],
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
        on_alert=alerts.append,
    )
    return session, option


class UnknownStateOrderManager:
    def __init__(self):
        self.calls = []

    def place_order(self, side, tradingsymbol, quantity, product="NRML", order_type="MARKET", price=None, trigger_price=None):
        self.calls.append((side, tradingsymbol, quantity, order_type))
        return {
            "status": "FAILED: broker response unknown",
            "order_id": "",
            "log_status": "",
            "log_data": {},
            "error": "read timeout after order submit",
            "error_class": "UNKNOWN_BROKER_STATE",
            "retriable": True,
            "requires_reconciliation": True,
        }

    def lot_size(self, tradingsymbol):
        return 75


class EntryPartialOrderManager:
    def __init__(self):
        self.cancelled = []

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"cancelled": True, "error": ""}

    def order_details(self, order_id, fallback_quantity=0, fallback_price=0):
        return {
            "order_id": order_id,
            "status": "CANCELLED",
            "quantity": int(fallback_quantity or 0),
            "filled_quantity": 25,
            "pending_quantity": max(int(fallback_quantity or 0) - 25, 0),
            "cancelled_quantity": max(int(fallback_quantity or 0) - 25, 0),
            "average_price": float(fallback_price or 0),
            "is_partial": False,
            "raw": {},
        }


class FakeReconciler:
    def __init__(self, orders):
        self.orders = orders

    def reconcile(self, open_position, pending_entry):
        return [{
            "level": "ERROR",
            "code": "BROKER_POSITION_MISMATCH",
            "message": "Broker and local position differ",
            "status": "MISMATCH",
            "context": {"instrument": "NIFTY25000CE"},
        }]


class AlertHookTests(unittest.TestCase):
    def test_kill_switch_emits_structured_alert(self):
        alerts = []
        session, _option = session_with_alerts(alerts)

        session.activate_kill_switch("manual test")

        self.assertEqual(alerts[-1]["level"], "CRITICAL")
        self.assertEqual(alerts[-1]["code"], "KILL_SWITCH_ACTIVATED")
        self.assertEqual(alerts[-1]["mode"], "PAPER")
        self.assertEqual(alerts[-1]["payload"]["reason"], "manual test")

    def test_unknown_broker_state_order_failure_emits_alert(self):
        alerts = []
        session, option = session_with_alerts(alerts)
        session.mode = "LIVE"
        session.zerodha = object()
        session.orders = UnknownStateOrderManager()

        status, order_id = session._place_order("BUY", signal(option), 75, order_type="LIMIT", price=100)

        self.assertTrue(status.startswith("FAILED"))
        self.assertEqual(order_id, "")
        codes = [alert["code"] for alert in alerts]
        self.assertIn("ORDER_UNKNOWN_BROKER_STATE", codes)
        self.assertEqual(alerts[-1]["code"], "KILL_SWITCH_ACTIVATED")
        unknown_state_alert = next(alert for alert in alerts if alert["code"] == "ORDER_UNKNOWN_BROKER_STATE")
        self.assertTrue(unknown_state_alert["payload"]["requires_reconciliation"])
        self.assertEqual(unknown_state_alert["payload"]["error_class"], "UNKNOWN_BROKER_STATE")
        self.assertTrue(session.risk_guard.kill_switch_active)

    def test_partial_exit_emits_alert_before_kill_switch(self):
        alerts = []
        session, option = session_with_alerts(alerts)
        position = {
            "trade_no": 1,
            "signal": signal(option),
            "option_index": 0,
            "entry_price": 100,
            "target": 120,
            "stoploss": 90,
            "quantity": 75,
            "entry_order_id": "E1",
        }
        details = {
            "quantity": 75,
            "filled_quantity": 25,
            "pending_quantity": 50,
            "average_price": 120,
        }

        handled = session._protect_against_partial_exit(position, "TARGET", "T1", "OPEN", details)

        self.assertTrue(handled)
        self.assertEqual(alerts[0]["code"], "PARTIAL_EXIT_DETECTED")
        self.assertEqual(alerts[0]["payload"]["filled_quantity"], 25)
        self.assertEqual(alerts[-1]["code"], "KILL_SWITCH_ACTIVATED")

    def test_partial_entry_emits_warning_alert(self):
        alerts = []
        session, option = session_with_alerts(alerts)
        session.orders = EntryPartialOrderManager()
        pending = {
            "signal": signal(option),
            "option_index": 0,
            "order_id": "E1",
            "quantity": 75,
            "contract_lot_size": 75,
            "limit_price": 100,
            "placed_at": pd.to_datetime("2026-05-10 09:15:00").to_pydatetime(),
            "placed_index": 0,
        }
        details = {
            "quantity": 75,
            "filled_quantity": 25,
            "pending_quantity": 50,
            "average_price": 99.5,
        }

        handled = session._handle_partial_pending_entry(pending, details, 0, "OPEN")

        self.assertTrue(handled)
        self.assertEqual(alerts[0]["level"], "WARN")
        self.assertEqual(alerts[0]["code"], "ORDER_PARTIAL_FILL")
        self.assertEqual(alerts[0]["payload"]["filled_quantity"], 25)

    def test_startup_reconciliation_error_emits_alert(self):
        alerts = []
        session, _option = session_with_alerts(alerts)
        session.mode = "LIVE"
        session.zerodha = object()

        with patch("execution_v2.PositionReconciler", FakeReconciler):
            findings = session._reconcile_startup_state()

        self.assertEqual(findings[0]["code"], "BROKER_POSITION_MISMATCH")
        self.assertEqual(alerts[0]["code"], "RECONCILIATION_ERROR")
        self.assertEqual(alerts[0]["payload"]["error_codes"], ["BROKER_POSITION_MISMATCH"])
        self.assertEqual(alerts[-1]["code"], "KILL_SWITCH_ACTIVATED")


if __name__ == "__main__":
    unittest.main()
