import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
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
        "bullish_reversal_condition": -20,
        "bearish_reversal_condition": 10,
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

            with pd.ExcelFile(path) as workbook:
                self.assertIn("Run Metadata", workbook.sheet_names)
                self.assertIn("Option Scores", workbook.sheet_names)
                self.assertIn("Score Formula", workbook.sheet_names)
            metadata = pd.read_excel(path, sheet_name="Run Metadata")
            metadata_values = dict(zip(metadata["Field"], metadata["Value"]))
            self.assertEqual(metadata_values["Mode"], "BACKTEST")
            self.assertEqual(metadata_values["Profile Name"], "backtest")
            self.assertIn("Settings Hash", metadata_values)
            self.assertIn("Trades", str(metadata_values["Sheet Guide"]))
            option_scores = pd.read_excel(path, sheet_name="Option Scores")
            self.assertIn("Early Score", option_scores.columns)
            self.assertIn("Entry Block Reason", option_scores.columns)
            self.assertIn("Early Score Calculation", option_scores.columns)
            self.assertIn("Main Fast Trigger Calculation", option_scores.columns)
            self.assertIn("Active Fast Settings", option_scores.columns)
            score_formula = pd.read_excel(path, sheet_name="Score Formula")
            self.assertIn("Early Score", set(score_formula["Field"]))

            with closing(sqlite3.connect(path.replace(".xlsx", ".db"))) as conn:
                sqlite_columns = pd.read_sql_query("SELECT * FROM option_scores LIMIT 1", conn).columns
                formula_columns = pd.read_sql_query("SELECT * FROM score_formula LIMIT 1", conn).columns
            self.assertIn("Early Score", sqlite_columns)
            self.assertIn("Early Score Calculation", sqlite_columns)
            self.assertIn("Entry Block Reason", sqlite_columns)
            self.assertIn("Formula", formula_columns)

    def test_blank_saved_fast_ohlcv_settings_fall_back_to_defaults(self):
        settings = settings_from_values({
            "volume_previous_multiplier": "",
            "market_entry_score": "",
            "buy_limit_score_low": "",
        })

        self.assertEqual(settings["volume_previous_multiplier"], 0.8)
        self.assertEqual(settings["market_entry_score"], 50)
        self.assertEqual(settings["buy_limit_score_low"], 40)
        self.assertEqual(settings["bullish_reversal_condition"], -20)
        self.assertEqual(settings["bearish_reversal_condition"], 10)

    def test_web_settings_normalise_trend_set(self):
        self.assertEqual(settings_from_values({"trend_set": "Bearish"})["trend_set"], "Bearish")
        self.assertEqual(settings_from_values({"trend_set": "CE"})["trend_set"], "Bullish")
        self.assertEqual(settings_from_values({"trend_set": ""})["trend_set"], "Auto")

    def test_scoring_calculation_text_is_opt_in_for_backtest_export(self):
        score_row = build_scoring_row(
            option_frame(),
            6,
            data_kind="option",
            entry_score_threshold=40,
            scoring_settings=backtest_settings(),
        )
        self.assertNotIn("Early Score Calculation", score_row)

        export_row = build_scoring_row(
            option_frame(),
            6,
            data_kind="option",
            entry_score_threshold=40,
            scoring_settings=backtest_settings(),
            include_calculations=True,
        )
        self.assertIn("Early Score Calculation", export_row)

    def test_saved_web_profile_replaces_blank_fast_ohlcv_settings(self):
        import web_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_path = web_app.SETTINGS_PROFILE_PATH
            web_app.SETTINGS_PROFILE_PATH = os.path.join(temp_dir, "settings_profiles.json")
            try:
                saved = save_settings_profile("backtest", {
                    "balance": "10000",
                    "volume_previous_multiplier": "",
                    "market_entry_score": "",
                })
            finally:
                web_app.SETTINGS_PROFILE_PATH = original_path

        self.assertEqual(saved["volume_previous_multiplier"], "0.80")
        self.assertEqual(saved["market_entry_score"], "50")

    def test_apply_backtest_settings_to_live_preserves_paper_balance_and_removes_real_margin_runtime_fields(self):
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
                            "market_entry_score": "55",
                            "volume_previous_multiplier": "1.4",
                        },
                        "paper": {
                            "balance": "100000",
                            "profit_points": "5",
                            "chart_interval": "5 min",
                        },
                        "real": {
                            "balance": "518.80",
                            "profit_points": "5",
                            "zerodha_margin_fetched": "true",
                            "broker_user_id": "AB1234",
                            "chart_interval": "1 min",
                        },
                    }, handle)

                profiles = apply_backtest_settings_to_live()
                with open(web_app.SETTINGS_PROFILE_PATH, "r", encoding="utf-8") as handle:
                    saved_file = json.load(handle)
            finally:
                web_app.SETTINGS_PROFILE_PATH = original_path

        self.assertEqual(profiles["paper"]["balance"], "100000")
        self.assertEqual(profiles["paper"]["profit_points"], "12")
        self.assertEqual(profiles["paper"]["volume_previous_multiplier"], "1.4")
        self.assertEqual(profiles["paper"]["chart_interval"], "5 min")
        self.assertEqual(profiles["real"]["balance"], "0")
        self.assertEqual(profiles["real"]["profit_points"], "12")
        self.assertEqual(profiles["real"]["chart_interval"], "1 min")
        self.assertNotIn("balance", saved_file["real"])
        self.assertNotIn("zerodha_margin_fetched", saved_file["real"])
        self.assertNotIn("broker_user_id", saved_file["real"])

    def test_real_margin_refresh_writes_ignored_runtime_snapshot_not_settings_profile(self):
        import json
        import web_app

        class FakeClient:
            def available_margin(self):
                return 12345.67

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_path = web_app.SETTINGS_PROFILE_PATH
            web_app.SETTINGS_PROFILE_PATH = os.path.join(temp_dir, "settings_profiles.json")
            try:
                with open(web_app.SETTINGS_PROFILE_PATH, "w", encoding="utf-8") as handle:
                    json.dump({"real": {"profit_points": "8"}}, handle)
                app = web_app.WebTradeBotApp()
                app.zerodha_clients_by_mode["LIVE"] = FakeClient()
                app.zerodha_auth_profiles["LIVE"] = {"user_id": "AB1234"}

                snapshot = app.refresh_margin("LIVE")
                runtime_snapshot = web_app.load_real_account_snapshot()
                profiles = web_app.load_settings_profiles()
                with open(web_app.SETTINGS_PROFILE_PATH, "r", encoding="utf-8") as handle:
                    saved_file = json.load(handle)
            finally:
                web_app.SETTINGS_PROFILE_PATH = original_path

        self.assertEqual(snapshot["available"], 12345.67)
        self.assertEqual(runtime_snapshot["available_margin"], 12345.67)
        self.assertEqual(runtime_snapshot["broker_user_id"], "AB1234")
        self.assertEqual(runtime_snapshot["source"], "Zerodha")
        self.assertEqual(profiles["real"]["balance"], "0")
        self.assertNotIn("balance", saved_file["real"])

    def test_web_app_saves_paper_balance_as_simulated_account(self):
        import json
        import web_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_path = web_app.SETTINGS_PROFILE_PATH
            web_app.SETTINGS_PROFILE_PATH = os.path.join(temp_dir, "settings_profiles.json")
            try:
                with open(web_app.SETTINGS_PROFILE_PATH, "w", encoding="utf-8") as handle:
                    json.dump({
                        "paper": {
                            "balance": "10000",
                            "profit_points": "5",
                        },
                    }, handle)

                app = web_app.WebTradeBotApp()
                app.save_paper_balance(9665.25)
                profiles = web_app.load_settings_profiles()
            finally:
                web_app.SETTINGS_PROFILE_PATH = original_path

        self.assertEqual(profiles["paper"]["balance"], "9665.25")

    def test_normalized_profile_uses_current_fast_ohlcv_defaults(self):
        import web_app

        saved = web_app.normalized_settings_profile({
            "market_entry_score": "",
            "volume_previous_multiplier": "",
        })
        runtime = settings_from_values(saved)

        self.assertEqual(saved["market_entry_score"], "50")
        self.assertEqual(runtime["market_entry_score"], 50)
        self.assertEqual(runtime["volume_previous_multiplier"], 0.8)


if __name__ == "__main__":
    unittest.main()
