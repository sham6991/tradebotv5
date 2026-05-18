import unittest
from datetime import datetime, timedelta

from execution_v2 import LivePaperSession
from tests.test_strategy_regression import nifty_frame, option_frame, settings


class LiveEntryActiveCandleTests(unittest.TestCase):
    def test_live_paper_uses_active_candle_as_next_entry_candle(self):
        test_settings = settings(entry_offset=0, max_trades=1, lot_size=1)
        nifty = nifty_frame("bearish", count=2)
        ce = option_frame("CE", buy_score=20, count=2)
        pe = option_frame("PE", buy_score=85, count=2)
        session = LivePaperSession(
            nifty,
            [ce, pe],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            test_settings,
            save_path=None,
            mode="PAPER",
        )

        active_time = datetime(2026, 5, 12, 9, 21)
        session.candle_builder.add_tick("NIFTY", 101, timestamp=active_time, volume=100)
        session.candle_builder.add_tick("OPTION_0", 100, timestamp=active_time, volume=100)
        session.candle_builder.add_tick("OPTION_1", 104, timestamp=active_time, volume=100)

        session._try_entry(1)

        self.assertIsNotNone(session.open_position)
        self.assertEqual(session.open_position["signal"]["type"], "PE")
        self.assertEqual(session.open_position["entry_price"], 104)


if __name__ == "__main__":
    unittest.main()
