import unittest

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


def signal():
    option = frame()
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


class CountingOrderManager:
    def __init__(self, fail_attempts=0, failure_requires_reconciliation=False, failure_retriable=True):
        self.calls = []
        self.next_id = 1
        self.fail_attempts = int(fail_attempts)
        self.failure_requires_reconciliation = failure_requires_reconciliation
        self.failure_retriable = failure_retriable

    def place_order(self, side, tradingsymbol, quantity, product="NRML", order_type="MARKET", price=None, trigger_price=None):
        self.calls.append({
            "side": side,
            "tradingsymbol": tradingsymbol,
            "quantity": quantity,
            "product": product,
            "order_type": order_type,
            "price": price,
            "trigger_price": trigger_price,
        })
        if len(self.calls) <= self.fail_attempts:
            return {
                "status": "FAILED: temporary timeout",
                "order_id": "",
                "log_status": "",
                "log_data": {},
                "error": "temporary timeout",
                "requires_reconciliation": self.failure_requires_reconciliation,
                "retriable": self.failure_retriable,
            }
        order_id = f"O{self.next_id}"
        self.next_id += 1
        return {
            "status": f"{side} {order_type} ORDER PLACED",
            "order_id": order_id,
            "log_status": f"{order_type} PLACED",
            "log_data": {"quantity": quantity, "price": price, "trigger_price": trigger_price},
            "error": "",
        }

    def lot_size(self, tradingsymbol):
        return 75


def session_with_orders(order_manager):
    session = LivePaperSession(
        frame(),
        [frame(), frame()],
        {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
        {
            "cooldown": 0,
            "balance": 100000,
            "lot_size": 1,
            "max_trades": 1,
            "profit_points": 20,
            "safety_points": 10,
            "square_off_time": "",
            "order_placement_retry_delay_seconds": 0,
        },
        save_path=None,
        mode="PAPER",
    )
    session.mode = "LIVE"
    session.zerodha = object()
    session.orders = order_manager
    return session


class OrderIdempotencyTests(unittest.TestCase):
    def test_duplicate_intended_order_returns_original_order_id(self):
        orders = CountingOrderManager()
        live_session = session_with_orders(orders)

        first = live_session._place_order("BUY", signal(), 75, order_type="LIMIT", price=100)
        second = live_session._place_order("BUY", signal(), 75, order_type="LIMIT", price=100)

        self.assertEqual(first, second)
        self.assertEqual(first[1], "O1")
        self.assertEqual(len(orders.calls), 1)
        self.assertEqual(live_session.duplicate_order_suppressed, 1)

    def test_failed_order_retries_until_third_attempt_inside_same_order_request(self):
        orders = CountingOrderManager(fail_attempts=2)
        live_session = session_with_orders(orders)

        result = live_session._place_order("BUY", signal(), 75, order_type="LIMIT", price=100)

        self.assertEqual(result[1], "O1")
        self.assertEqual(len(orders.calls), 3)

    def test_order_request_stops_after_three_failed_placement_attempts(self):
        orders = CountingOrderManager(fail_attempts=3)
        live_session = session_with_orders(orders)

        result = live_session._place_order("BUY", signal(), 75, order_type="LIMIT", price=100)

        self.assertTrue(result[0].startswith("FAILED"))
        self.assertEqual(result[1], "")
        self.assertEqual(len(orders.calls), 3)

    def test_unknown_broker_state_is_not_retried_to_avoid_duplicate_order(self):
        orders = CountingOrderManager(fail_attempts=1, failure_requires_reconciliation=True)
        live_session = session_with_orders(orders)

        result = live_session._place_order("BUY", signal(), 75, order_type="LIMIT", price=100)

        self.assertTrue(result[0].startswith("FAILED"))
        self.assertEqual(len(orders.calls), 1)
        self.assertTrue(live_session.risk_guard.kill_switch_active)
        self.assertIn("UNKNOWN_BROKER_STATE", live_session.risk_guard.kill_switch_reason)

    def test_non_retriable_order_failure_is_not_retried(self):
        orders = CountingOrderManager(fail_attempts=1, failure_retriable=False)
        live_session = session_with_orders(orders)

        result = live_session._place_order("BUY", signal(), 75, order_type="LIMIT", price=100)

        self.assertTrue(result[0].startswith("FAILED"))
        self.assertEqual(len(orders.calls), 1)
        self.assertFalse(live_session.risk_guard.kill_switch_active)

    def test_target_and_stoploss_have_distinct_idempotency_keys(self):
        orders = CountingOrderManager()
        live_session = session_with_orders(orders)
        sig = signal()
        live_session.open_position = {"trade_no": 1}

        target = live_session._place_order("SELL", sig, 75, order_type="LIMIT", price=120)
        stoploss = live_session._place_order("SELL", sig, 75, order_type="SL", price=88, trigger_price=90)

        self.assertEqual(target[1], "O1")
        self.assertEqual(stoploss[1], "O2")
        self.assertEqual(len(orders.calls), 2)


if __name__ == "__main__":
    unittest.main()
