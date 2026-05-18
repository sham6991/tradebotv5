import unittest

from execution_v2 import LivePaperSession
from tests.test_strategy_regression import nifty_frame, option_frame, settings


def entry_signal(option):
    return {
        "option": option,
        "option_index": 0,
        "type": "CE",
        "instrument": option.attrs["instrument"],
        "tradingsymbol": option.attrs["tradingsymbol"],
        "entry": 100,
        "entry_offset": 0,
        "entry_index": 2,
        "signal_index": 1,
        "nifty_signal_index": 1,
        "target": 110,
        "stoploss": 95,
        "score_row": {"Buy Score": 85, "Buy Entry": "BUY"},
    }


class ClosedTradeUiStateTests(unittest.TestCase):
    def test_stoploss_close_clears_active_orders_and_forces_complete_snapshot(self):
        updates = []
        option = option_frame("CE", buy_score=85, exit_mode="stoploss", count=4)
        session = LivePaperSession(
            nifty_frame("bullish", count=4),
            [option, option_frame("PE", buy_score=20, count=4)],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            settings(
                entry_offset=0,
                lot_size=1,
                max_trades=1,
                profit_points=10,
                safety_points=5,
                ui_update_interval=999,
            ),
            save_path=None,
            mode="PAPER",
            on_order_update=updates.append,
        )

        session._open_position_from_fill(entry_signal(option), 75, "", 100, 75)
        self.assertTrue(session.active_orders)

        updates.clear()
        trade = session._close_position(2, "STOPLOSS", 95)

        self.assertIsNotNone(trade)
        self.assertIsNone(session.open_position)
        self.assertEqual(session.active_orders, {})
        self.assertGreaterEqual(len(updates), 2)
        self.assertEqual(updates[0]["order_event"]["Action"], "STOPLOSS SELL")
        self.assertEqual(updates[0]["active_orders"], [])
        self.assertEqual(updates[-1]["active_orders"], [])
        self.assertEqual(updates[-1]["live_trade"]["Status"], "COMPLETE")
        self.assertEqual(updates[-1]["live_trade"]["Current Trade Side"], "WAITING")


if __name__ == "__main__":
    unittest.main()
