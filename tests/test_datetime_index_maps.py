import unittest
from datetime import datetime

import pandas as pd

from engine import TradingEngine, attach_datetime_index_map
from execution_v2 import LivePaperSession


def frame(rows=None):
    rows = rows or [
        {
            "datetime": datetime(2026, 5, 10, 9, 15),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
        }
    ]
    df = pd.DataFrame(rows)
    df.attrs["instrument"] = "NIFTY25000CE"
    df.attrs["tradingsymbol"] = "NIFTY25000CE"
    df.attrs["option_type"] = "CE"
    return df


class DatetimeIndexMapTests(unittest.TestCase):
    def test_aligned_option_index_uses_exact_datetime_map(self):
        nifty = attach_datetime_index_map(frame([
            {"datetime": datetime(2026, 5, 10, 9, 15), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"datetime": datetime(2026, 5, 10, 9, 18), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        ]))
        option = attach_datetime_index_map(frame([
            {"datetime": datetime(2026, 5, 10, 9, 15), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"datetime": datetime(2026, 5, 10, 9, 18), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        ]))

        index = TradingEngine(0)._aligned_option_index(
            nifty,
            option,
            1,
            {"chart_interval": "3minute"},
        )

        self.assertEqual(index, 1)

    def test_aligned_option_index_uses_map_fallback_within_interval(self):
        nifty = attach_datetime_index_map(frame([
            {"datetime": datetime(2026, 5, 10, 9, 18), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        ]))
        option = attach_datetime_index_map(frame([
            {"datetime": datetime(2026, 5, 10, 9, 14), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"datetime": datetime(2026, 5, 10, 9, 16), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        ]))

        index = TradingEngine(0)._aligned_option_index(
            nifty,
            option,
            0,
            {"chart_interval": "3minute"},
        )

        self.assertEqual(index, 1)

    def test_live_append_updates_datetime_index_map(self):
        session = LivePaperSession(
            frame(),
            [frame(), frame()],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            {
                "cooldown": 0,
                "balance": 100000,
                "lot_size": 1,
                "max_trades": 1,
                "square_off_time": "",
            },
            save_path=None,
            mode="PAPER",
        )

        appended = session._append_completed_candle(
            "OPTION_0",
            {
                "datetime": datetime(2026, 5, 10, 9, 18),
                "open": 101,
                "high": 102,
                "low": 100,
                "close": 101,
                "volume": 10,
            },
        )

        self.assertTrue(appended)
        keys = session.options[0].attrs["datetime_index_keys"]
        mapping = session.options[0].attrs["datetime_index_map"]
        self.assertEqual(mapping[keys[-1]], len(session.options[0]) - 1)
        self.assertEqual(session.options[0].attrs["last_datetime_key"], keys[-1])


if __name__ == "__main__":
    unittest.main()
