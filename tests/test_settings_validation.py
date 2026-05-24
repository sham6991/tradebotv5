import unittest
import os
import tempfile

import settings_service
from settings_validation import raise_for_fast_ohlcv_settings, validate_fast_ohlcv_settings
from ui_shared import DEFAULT_SETTINGS


class FastOhlcvSettingsValidationTests(unittest.TestCase):
    def test_default_fast_settings_are_valid(self):
        self.assertEqual(validate_fast_ohlcv_settings(DEFAULT_SETTINGS), [])

    def test_market_score_must_be_above_buy_limit_score(self):
        values = dict(DEFAULT_SETTINGS)
        values["buy_limit_score_low"] = "50"
        values["market_entry_score"] = "40"

        with self.assertRaises(ValueError):
            raise_for_fast_ohlcv_settings(values)

    def test_hard_rejection_wick_must_be_above_trigger_wick(self):
        values = dict(DEFAULT_SETTINGS)
        values["trigger_upper_wick_max"] = "55"
        values["hard_rejection_upper_wick_max"] = "50"

        errors = validate_fast_ohlcv_settings(values)

        self.assertTrue(any("Hard Rejection Wick Max" in error for error in errors))

    def test_trend_set_must_be_known_value(self):
        values = dict(DEFAULT_SETTINGS)
        values["trend_set"] = "Sideways"

        errors = validate_fast_ohlcv_settings(values)

        self.assertTrue(any("Trend Set" in error for error in errors))

    def test_trailing_stop_requires_target_above_ten_points(self):
        values = dict(DEFAULT_SETTINGS)
        values["trailing_sl_enabled"] = "true"
        values["profit_points"] = "10"

        errors = validate_fast_ohlcv_settings(values)

        self.assertIn("Trailing Stop Loss requires target/profit points greater than 10.", errors)

    def test_enabled_disabled_boolean_words_are_accepted(self):
        values = dict(DEFAULT_SETTINGS)
        values["trailing_sl_enabled"] = "Enabled"
        values["profit_points"] = "20"

        parsed = settings_service.settings_from_values(values)

        self.assertTrue(parsed["trailing_sl_enabled"])
        values["trailing_sl_enabled"] = "Disabled"
        parsed = settings_service.settings_from_values(values)
        self.assertFalse(parsed["trailing_sl_enabled"])

    def test_stoploss_limit_buffer_default_loads_and_persists(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            path = os.path.join(temp_dir, "settings_profiles.json")

            loaded = settings_service.load_settings_profiles(path)
            self.assertEqual(loaded["paper"]["stoploss_limit_buffer_points"], "2")

            saved = settings_service.save_settings_profile(
                "paper",
                {**loaded["paper"], "stoploss_limit_buffer_points": "3"},
                path,
            )
            reloaded = settings_service.load_settings_profiles(path)

        self.assertEqual(saved["stoploss_limit_buffer_points"], "3")
        self.assertEqual(reloaded["paper"]["stoploss_limit_buffer_points"], "3")

    def test_live_market_entry_limit_settings_load_and_persist(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            path = os.path.join(temp_dir, "settings_profiles.json")

            loaded = settings_service.load_settings_profiles(path)
            self.assertEqual(loaded["backtest"]["live_option_market_entry_as_limit_enabled"], "false")
            self.assertEqual(loaded["backtest"]["live_option_market_entry_limit_buffer_points"], "2")

            saved = settings_service.save_settings_profile(
                "backtest",
                {
                    **loaded["backtest"],
                    "live_option_market_entry_as_limit_enabled": "Enabled",
                    "live_option_market_entry_limit_buffer_points": "3.5",
                },
                path,
            )
            reloaded = settings_service.load_settings_profiles(path)

        self.assertEqual(saved["live_option_market_entry_as_limit_enabled"], "Enabled")
        self.assertEqual(saved["live_option_market_entry_limit_buffer_points"], "3.5")
        self.assertEqual(reloaded["backtest"]["live_option_market_entry_as_limit_enabled"], "Enabled")
        self.assertEqual(reloaded["backtest"]["live_option_market_entry_limit_buffer_points"], "3.5")

    def test_live_market_entry_limit_settings_persist_for_each_risk_profile(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            path = os.path.join(temp_dir, "settings_profiles.json")

            for profile in ("backtest", "paper", "real"):
                loaded = settings_service.load_settings_profiles(path)
                saved = settings_service.save_settings_profile(
                    profile,
                    {
                        **loaded[profile],
                        "live_option_market_entry_as_limit_enabled": "Enabled",
                        "live_option_market_entry_limit_buffer_points": "4.25",
                    },
                    path,
                )
                reloaded = settings_service.load_settings_profiles(path)

                self.assertEqual(saved["live_option_market_entry_as_limit_enabled"], "Enabled")
                self.assertEqual(saved["live_option_market_entry_limit_buffer_points"], "4.25")
                self.assertEqual(reloaded[profile]["live_option_market_entry_as_limit_enabled"], "Enabled")
                self.assertEqual(reloaded[profile]["live_option_market_entry_limit_buffer_points"], "4.25")


if __name__ == "__main__":
    unittest.main()
