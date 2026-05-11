import unittest
from datetime import datetime

import pandas as pd

from execution_v2 import Executor
from preflight import validate_live_preflight


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


def settings():
    return {
        "cooldown": 0,
        "balance": 100000,
        "lot_size": 1,
        "max_trades": 1,
        "profit_points": 20,
        "safety_points": 10,
        "chart_interval": "3minute",
        "bullish_threshold": 16,
        "bearish_threshold": -15,
        "rsi_bull": 55,
        "rsi_bear": 45,
        "min_buy_score": 60,
        "square_off_time": "15:20",
    }


class PreflightTests(unittest.TestCase):
    def test_valid_paper_preflight_passes(self):
        report = validate_live_preflight(
            frame(),
            [frame()],
            {1: "NIFTY", 2: "OPTION_0"},
            settings(),
            mode="PAPER",
        )

        self.assertTrue(report.ok)
        self.assertEqual(report.errors, [])

    def test_invalid_settings_fail_preflight(self):
        bad_settings = settings()
        bad_settings["lot_size"] = 0
        bad_settings["square_off_time"] = "bad"

        report = validate_live_preflight(
            frame(),
            [frame()],
            {1: "NIFTY", 2: "OPTION_0"},
            bad_settings,
            mode="PAPER",
        )

        self.assertFalse(report.ok)
        self.assertIn("INVALID_LOT_SIZE", {item["code"] for item in report.errors})
        self.assertIn("INVALID_SQUARE_OFF_TIME", {item["code"] for item in report.errors})

    def test_missing_market_inputs_fail_preflight(self):
        report = validate_live_preflight(
            pd.DataFrame(),
            [],
            {},
            settings(),
            mode="PAPER",
        )

        self.assertFalse(report.ok)
        codes = {item["code"] for item in report.errors}
        self.assertIn("MISSING_NIFTY_DATA", codes)
        self.assertIn("MISSING_OPTION_DATA", codes)
        self.assertIn("MISSING_TOKEN_MAP", codes)

    def test_real_mode_requires_zerodha_and_warns_outside_market_hours(self):
        report = validate_live_preflight(
            frame(),
            [frame()],
            {1: "NIFTY", 2: "OPTION_0"},
            settings(),
            mode="LIVE",
            zerodha=None,
            now=datetime(2026, 5, 10, 8, 0),
        )

        self.assertFalse(report.ok)
        self.assertIn("ZERODHA_NOT_CONNECTED", {item["code"] for item in report.errors})
        self.assertIn("OUTSIDE_MARKET_HOURS", {item["code"] for item in report.warnings})

    def test_executor_blocks_bad_preflight_before_starting_feed(self):
        executor = Executor()
        bad_settings = settings()
        bad_settings["profit_points"] = 0

        with self.assertRaises(ValueError):
            executor.start_live_paper_trading(
                frame(),
                [frame()],
                {1: "NIFTY", 2: "OPTION_0"},
                bad_settings,
                save_path=None,
            )

        self.assertIsNone(executor.live_paper_session)
        self.assertIsNotNone(executor.last_preflight_report)


if __name__ == "__main__":
    unittest.main()
