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


class LiveKillSwitchTests(unittest.TestCase):
    def test_manual_kill_switch_blocks_session_entries(self):
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

        reason = session.activate_kill_switch("test block")

        self.assertEqual(reason, "KILL SWITCH ACTIVE: test block")
        self.assertTrue(session._trading_blocked())
        self.assertEqual(session.trading_blocked_reason, "KILL SWITCH ACTIVE: test block")


if __name__ == "__main__":
    unittest.main()
