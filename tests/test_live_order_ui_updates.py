import unittest

from execution_v2 import LivePaperSession
from tests.test_live_entry_active_candle import fast_option_frame
from tests.test_strategy_regression import nifty_frame, settings


class FilledLimitOrderManager:
    def __init__(self):
        self.calls = []

    def lot_size(self, tradingsymbol):
        return 75

    def available_margin(self):
        return 100000

    def place_order(self, side, tradingsymbol, quantity, product="NRML", order_type="LIMIT", price=None, trigger_price=None):
        self.calls.append((side, tradingsymbol, quantity, order_type))
        order_id = "ENTRY1" if side == "BUY" else f"EXIT{len(self.calls)}"
        return {
            "status": f"{side} {order_type} ORDER PLACED",
            "order_id": order_id,
            "log_status": f"{order_type} PLACED",
            "log_data": {"quantity": quantity},
            "error": "",
        }

    def order_status(self, order_id, fallback="UNKNOWN"):
        if str(order_id).startswith("EXIT"):
            return "OPEN"
        return fallback

    def order_details(self, order_id, fallback_quantity=0, fallback_price=0):
        if str(order_id).startswith("ENTRY"):
            return {
                "order_id": order_id,
                "status": "COMPLETE",
                "quantity": fallback_quantity,
                "filled_quantity": fallback_quantity,
                "pending_quantity": 0,
                "average_price": fallback_price,
                "cancelled_quantity": 0,
                "is_partial": False,
            }
        return {
            "order_id": order_id,
            "status": "OPEN",
            "quantity": fallback_quantity,
            "filled_quantity": 0,
            "pending_quantity": fallback_quantity,
            "cancelled_quantity": 0,
            "is_partial": False,
        }

    def average_price(self, order_id, fallback):
        return fallback

    def filled_quantity(self, order_id, fallback):
        return fallback


class LiveOrderUiUpdateTests(unittest.TestCase):
    def test_live_limit_entry_emits_order_event_before_trade_snapshot(self):
        updates = []
        session = LivePaperSession(
            nifty_frame("bearish", count=11),
            [fast_option_frame("CE"), fast_option_frame("PE", entry_type="limit")],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            settings(entry_offset=0, max_trades=1, lot_size=1, enforce_market_hours=0),
            save_path=None,
            mode="LIVE",
            zerodha=object(),
            on_order_update=updates.append,
        )
        session.orders = FilledLimitOrderManager()

        session._try_entry(10)

        self.assertGreaterEqual(len(updates), 1)
        self.assertEqual(updates[0]["order_event"]["Action"], "BUY")
        self.assertEqual(updates[0]["order_event"]["Order Status"], "OPEN")
        self.assertEqual(updates[0]["order_event"]["Zerodha Order ID"], "ENTRY1")
        self.assertEqual(updates[0]["live_trade"], {})
        if updates[-1].get("live_trade"):
            self.assertEqual(updates[-1]["live_trade"]["Status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
