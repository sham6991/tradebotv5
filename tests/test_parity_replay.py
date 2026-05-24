import os
import tempfile
import unittest
from datetime import datetime, timedelta

import pandas as pd

from engine import attach_datetime_index_map
from parity_replay import build_backtest_decision_replay, build_parity_report, load_candle_frames
from sqlite_store import TradingStore
from strategy import OPTION_FORMULA_COLUMNS


def settings(**overrides):
    values = {
        "balance": 100000,
        "lot_size": 1,
        "max_trades": 5,
        "profit_points": 10,
        "safety_points": 5,
        "time_exit": 3,
        "cooldown": 0,
        "chart_interval": "3minute",
        "trend_set": "Auto",
        "bullish_threshold": 16,
        "bearish_threshold": -15,
        "rsi_bull": 55,
        "rsi_bear": 45,
        "rsi_reversal_bullish": 70,
        "rsi_reversal_bearish": 20,
        "bullish_reversal_condition": -20,
        "bearish_reversal_condition": 10,
        "buy_limit_score_low": 40,
        "market_entry_score": 50,
        "large_candle_multiplier": 20,
        "move_from_low_max_multiplier": 20,
        "gap_spike_multiplier": 20,
        "max_daily_loss": 0,
        "max_daily_profit": 0,
        "max_consecutive_losses": 0,
    }
    values.update(overrides)
    return values


def nifty_frame():
    base_time = datetime(2026, 5, 12, 9, 15)
    rows = []
    for index in range(10):
        rows.append({
            "datetime": base_time + timedelta(minutes=3 * index),
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 1000 + index,
            "EMA20": 120,
            "EMA50": 100,
            "RSI": 65,
        })
    return attach_datetime_index_map(pd.DataFrame(rows))


def option_frame(option_type):
    base_time = datetime(2026, 5, 12, 9, 15)
    rows = []
    for index in range(10):
        rows.append({
            "datetime": base_time + timedelta(minutes=3 * index),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1000,
        })
    rows[6].update({"open": 108, "high": 112, "low": 107, "close": 111, "volume": 1500})
    for index in range(7, 10):
        rows[index].update({"open": 111, "high": 122, "low": 108, "close": 121, "volume": 1400})

    df = pd.DataFrame(rows)
    for column in OPTION_FORMULA_COLUMNS:
        if column not in df.columns:
            df[column] = 0
    df.attrs["instrument"] = f"NIFTY25000{option_type}"
    df.attrs["tradingsymbol"] = f"NIFTY25000{option_type}"
    df.attrs["option_type"] = option_type
    return attach_datetime_index_map(df)


class ParityReplayTests(unittest.TestCase):
    def test_parity_report_matches_identical_backtest_and_paper_entry(self):
        test_settings = settings()
        nifty = nifty_frame()
        options = [option_frame("CE"), option_frame("PE")]
        expected = build_backtest_decision_replay(nifty, options, test_settings)["entries"]
        self.assertTrue(expected)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="PAPER", settings={"session_id": "S1", **test_settings})
            store.log_order_history(_history_row(expected[0]))

            report = build_parity_report(db_path, nifty, options, test_settings, session_id="S1")

        self.assertEqual(report["summary"]["status"], "MATCH")
        self.assertEqual(report["summary"]["mismatches"], 0)

    def test_parity_report_flags_option_side_mismatch(self):
        test_settings = settings()
        nifty = nifty_frame()
        options = [option_frame("CE"), option_frame("PE")]
        expected = build_backtest_decision_replay(nifty, options, test_settings)["entries"]
        self.assertTrue(expected)
        actual = dict(expected[0])
        actual["option_type"] = "PE"

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="PAPER", settings={"session_id": "S1", **test_settings})
            store.log_order_history(_history_row(actual))

            report = build_parity_report(db_path, nifty, options, test_settings, session_id="S1")

        self.assertEqual(report["summary"]["status"], "MISMATCH")
        self.assertTrue(
            any(
                mismatch["field"] == "option_type"
                for comparison in report["mismatches"]
                for mismatch in comparison["mismatches"]
            )
        )

    def test_parity_report_can_load_candles_from_session_db(self):
        test_settings = settings()
        nifty = nifty_frame()
        options = [option_frame("CE"), option_frame("PE")]
        expected = build_backtest_decision_replay(nifty, options, test_settings)["entries"]
        self.assertTrue(expected)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = os.path.join(temp_dir, "session.db")
            store = TradingStore(db_path, mode="PAPER", settings={"session_id": "S1", **test_settings})
            _log_frame_candles(store, "NIFTY", nifty)
            _log_frame_candles(store, "OPTION_0", options[0])
            _log_frame_candles(store, "OPTION_1", options[1])
            store.log_order_history(_history_row(expected[0]))

            stored_nifty, stored_options = load_candle_frames(db_path, session_id="S1", settings=test_settings)
            report = build_parity_report(db_path, None, None, test_settings, session_id="S1")

        self.assertEqual(len(stored_nifty), len(nifty))
        self.assertEqual(len(stored_options), 2)
        self.assertEqual(report["summary"]["status"], "MATCH")
        self.assertEqual(report["summary"]["mismatches"], 0)


def _history_row(entry):
    return {
        "Session Trade No": 1,
        "Timestamp": entry["entry_time"],
        "Instrument / Symbol": entry["instrument"],
        "Option Type": entry["option_type"],
        "Action": "BUY",
        "Order Type": entry["order_type"],
        "Quantity": 1,
        "Order Status": "PAPER",
        "Entry Price": entry["entry_price"],
        "Early Score": entry["early_score"],
        "Entry Type": entry["entry_type"],
        "Target Price": entry["target_price"],
        "Stop Loss Price": entry["stoploss_price"],
        "Related Trade ID": "S1_1",
    }


def _log_frame_candles(store, stream_name, frame):
    metadata = {
        "instrument": frame.attrs.get("instrument", "NIFTY" if stream_name == "NIFTY" else ""),
        "tradingsymbol": frame.attrs.get("tradingsymbol", frame.attrs.get("instrument", "")),
        "option_type": frame.attrs.get("option_type", ""),
    }
    for _, row in frame.iterrows():
        store.log_candle(stream_name, row, metadata)


if __name__ == "__main__":
    unittest.main()
