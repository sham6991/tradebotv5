import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

import pandas as pd

from backtest import run_backtest
from engine import attach_datetime_index_map
from strategy import build_scoring_row
from web_app import apply_backtest_settings_to_live, save_settings_profile, settings_from_values


def backtest_settings(**overrides):
    values = {
        "cooldown": 0,
        "balance": 100000,
        "lot_size": 1,
        "max_trades": 1,
        "profit_points": 10,
        "safety_points": 5,
        "entry_offset": 0,
        "time_exit": 2,
        "chart_interval": "3minute",
        "bullish_threshold": 16,
        "bearish_threshold": -15,
        "rsi_bull": 55,
        "rsi_bear": 45,
        "rsi_reversal_bullish": 70,
        "rsi_reversal_bearish": 20,
        "watch_buy_score": 60,
        "min_buy_score": 60,
        "strong_buy_score": 80,
        "min_volume_ratio": 1.2,
        "min_option_volume": 0,
        "aggression_score_cap": 55,
        "compression_range_ratio": 0.7,
        "expansion_range_ratio": 1.8,
        "max_chase_range_ratio": 2.5,
        "failed_breakout_penalty": -15,
        "early_breakout_min_score": 60,
        "max_daily_loss": 0,
        "max_daily_profit": 0,
        "max_consecutive_losses": 0,
    }
    values.update(overrides)
    return values


def nifty_frame(count=10):
    base_time = datetime(2026, 5, 12, 9, 15)
    rows = []
    for index in range(count):
        rows.append({
            "datetime": base_time + timedelta(minutes=3 * index),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": 1000 + index,
            "EMA20": 120,
            "EMA50": 100,
            "RSI": 65,
        })
    return attach_datetime_index_map(pd.DataFrame(rows))


def option_frame(count=10):
    base_time = datetime(2026, 5, 12, 9, 15)
    rows = []
    for index in range(count):
        rows.append({
            "datetime": base_time + timedelta(minutes=3 * index),
            "open": 100,
            "high": 104 + index,
            "low": 97,
            "close": 101 + index,
            "volume": 1000 + (index * 250),
        })
    df = attach_datetime_index_map(pd.DataFrame(rows))
    df.attrs["instrument"] = "NIFTY25000CE"
    df.attrs["tradingsymbol"] = "NIFTY25000CE"
    df.attrs["option_type"] = "CE"
    return df


class BacktestExportTests(unittest.TestCase):
    def test_backtest_exports_option_score_calculations(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            path = os.path.join(temp_dir, "backtest.xlsx")

            run_backtest(nifty_frame(), [option_frame()], backtest_settings(), path)

            workbook = pd.ExcelFile(path)
            self.assertIn("Option Scores", workbook.sheet_names)
            self.assertIn("Score Formula", workbook.sheet_names)
            option_scores = pd.read_excel(path, sheet_name="Option Scores")
            self.assertIn("Range Ratio", option_scores.columns)
            self.assertIn("Aggression Score Calculation", option_scores.columns)
            self.assertIn("Buy Score Calculation", option_scores.columns)
            self.assertIn("Early Breakout Probability Score", option_scores.columns)
            self.assertIn("Early Breakout Probability Calculation", option_scores.columns)
            self.assertIn("High Probability Buy", option_scores.columns)
            self.assertIn("High Probability Buy Calculation", option_scores.columns)
            self.assertIn("Capped Aggression", str(option_scores["Buy Score Calculation"].iloc[-1]))
            score_formula = pd.read_excel(path, sheet_name="Score Formula")
            self.assertIn("Buy Score", set(score_formula["Field"]))

            with sqlite3.connect(path.replace(".xlsx", ".db")) as conn:
                sqlite_columns = pd.read_sql_query("SELECT * FROM option_scores LIMIT 1", conn).columns
                formula_columns = pd.read_sql_query("SELECT * FROM score_formula LIMIT 1", conn).columns
            self.assertIn("Buy Score", sqlite_columns)
            self.assertIn("Buy Score Calculation", sqlite_columns)
            self.assertIn("Entry Block Reason", sqlite_columns)
            self.assertIn("Formula", formula_columns)

    def test_blank_saved_scoring_settings_fall_back_to_defaults(self):
        settings = settings_from_values({
            "min_volume_ratio": "",
            "min_option_volume": "",
            "aggression_score_cap": "",
            "compression_range_ratio": "",
            "expansion_range_ratio": "",
            "max_chase_range_ratio": "",
            "failed_breakout_penalty": "",
            "early_breakout_min_score": "",
        })

        self.assertEqual(settings["min_volume_ratio"], 1.2)
        self.assertEqual(settings["early_breakout_min_score"], 60)

    def test_scoring_calculation_text_is_opt_in_for_backtest_export(self):
        score_row = build_scoring_row(
            option_frame(),
            6,
            data_kind="option",
            min_buy_score=60,
            scoring_settings=backtest_settings(),
        )
        self.assertNotIn("Buy Score Calculation", score_row)

        export_row = build_scoring_row(
            option_frame(),
            6,
            data_kind="option",
            min_buy_score=60,
            scoring_settings=backtest_settings(),
            include_calculations=True,
        )
        self.assertIn("Buy Score Calculation", export_row)

    def test_saved_web_profile_replaces_blank_scoring_settings(self):
        import web_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_path = web_app.SETTINGS_PROFILE_PATH
            web_app.SETTINGS_PROFILE_PATH = os.path.join(temp_dir, "settings_profiles.json")
            try:
                saved = save_settings_profile("backtest", {
                    "balance": "10000",
                    "min_buy_score": "60",
                    "min_volume_ratio": "",
                    "early_breakout_min_score": "",
                })
            finally:
                web_app.SETTINGS_PROFILE_PATH = original_path

        self.assertEqual(saved["min_volume_ratio"], "1.2")
        self.assertEqual(saved["early_breakout_min_score"], "60")

    def test_apply_backtest_settings_to_live_updates_paper_and_preserves_real_margin(self):
        import json
        import web_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_path = web_app.SETTINGS_PROFILE_PATH
            web_app.SETTINGS_PROFILE_PATH = os.path.join(temp_dir, "settings_profiles.json")
            try:
                with open(web_app.SETTINGS_PROFILE_PATH, "w", encoding="utf-8") as handle:
                    json.dump({
                        "backtest": {
                            "balance": "10000",
                            "profit_points": "12",
                            "min_buy_score": "61",
                            "min_volume_ratio": "1.4",
                        },
                        "paper": {
                            "balance": "100000",
                            "profit_points": "5",
                        },
                        "real": {
                            "balance": "518.80",
                            "profit_points": "5",
                            "zerodha_margin_fetched": "true",
                        },
                    }, handle)

                profiles = apply_backtest_settings_to_live()
            finally:
                web_app.SETTINGS_PROFILE_PATH = original_path

        self.assertEqual(profiles["paper"]["balance"], "10000")
        self.assertEqual(profiles["paper"]["profit_points"], "12")
        self.assertEqual(profiles["paper"]["min_volume_ratio"], "1.4")
        self.assertEqual(profiles["real"]["balance"], "518.80")
        self.assertEqual(profiles["real"]["profit_points"], "12")
        self.assertEqual(profiles["real"]["zerodha_margin_fetched"], "true")


if __name__ == "__main__":
    unittest.main()
