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
        "score_row": {"Buy Score": 85, "Buy Entry": "BUY"},
    }


class CountingOrderManager:
    def __init__(self, fail_first=False):
        self.calls = []
        self.next_id = 1
        self.fail_first = fail_first

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
        if self.fail_first and len(self.calls) == 1:
            return {
                "status": "FAILED: temporary timeout",
                "order_id": "",
                "log_status": "",
                "log_data": {},
                "error": "temporary timeout",
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

    def test_failed_order_is_not_cached_and_can_retry(self):
        orders = CountingOrderManager(fail_first=True)
        live_session = session_with_orders(orders)

        first = live_session._place_order("BUY", signal(), 75, order_type="LIMIT", price=100)
        second = live_session._place_order("BUY", signal(), 75, order_type="LIMIT", price=100)

        self.assertTrue(first[0].startswith("FAILED"))
        self.assertEqual(second[1], "O1")
        self.assertEqual(len(orders.calls), 2)

    def test_target_and_stoploss_have_distinct_idempotency_keys(self):
        orders = CountingOrderManager()
        live_session = session_with_orders(orders)
        sig = signal()
        live_session.open_position = {"trade_no": 1}

        target = live_session._place_order("SELL", sig, 75, order_type="LIMIT", price=120)
        stoploss = live_session._place_order("SELL", sig, 75, order_type="SL-M", trigger_price=90)

        self.assertEqual(target[1], "O1")
        self.assertEqual(stoploss[1], "O2")
        self.assertEqual(len(orders.calls), 2)


if __name__ == "__main__":
    unittest.main()
