import unittest
from datetime import datetime, timedelta

import pandas as pd

from execution_v2 import LivePaperSession


def frame(count=5):
    base = datetime(2026, 5, 10, 9, 15)
    df = pd.DataFrame([
        {
            "datetime": base + timedelta(minutes=index),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": index + 1,
        }
        for index in range(count)
    ])
    df.attrs["instrument"] = "NIFTY25000CE"
    df.attrs["tradingsymbol"] = "NIFTY25000CE"
    df.attrs["option_type"] = "CE"
    return df


def session(limit=3):
    return LivePaperSession(
        frame(5),
        [frame(5), frame(5)],
        {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
        {
            "cooldown": 0,
            "balance": 100000,
            "lot_size": 1,
            "max_trades": 1,
            "square_off_time": "",
            "live_candle_memory_limit": limit,
        },
        save_path=None,
        mode="PAPER",
    )


class LiveCandleMemoryTests(unittest.TestCase):
    def test_trim_live_candles_rebuilds_maps_and_adjusts_indexes(self):
        live_session = session(limit=3)
        live_session.last_candle_index = 4
        live_session.engine.cooldown_until = 4

        trimmed = live_session._trim_live_candles_if_safe()

        self.assertTrue(trimmed)
        self.assertEqual(len(live_session.nifty), 3)
        self.assertEqual([len(option) for option in live_session.options], [3, 3])
        self.assertEqual(live_session.last_candle_index, 2)
        self.assertEqual(live_session.engine.cooldown_until, 2)
        self.assertEqual(len(live_session.nifty.attrs["datetime_index_keys"]), 3)
        self.assertEqual(
            live_session.nifty.attrs["datetime_index_map"][live_session.nifty.attrs["datetime_index_keys"][-1]],
            2,
        )

    def test_trim_is_skipped_for_open_position(self):
        live_session = session(limit=3)
        live_session.open_position = {"trade_no": 1}

        trimmed = live_session._trim_live_candles_if_safe()

        self.assertFalse(trimmed)
        self.assertEqual(len(live_session.nifty), 5)


if __name__ == "__main__":
    unittest.main()
