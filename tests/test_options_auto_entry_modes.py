import unittest

from options_auto.config.options_auto_defaults import normalize_settings
from options_auto.intelligence.simple_ohlcv_entry import resolve_entry_dependency_mode


class OptionsAutoEntryModeTests(unittest.TestCase):
    def test_profile_and_unknown_normalize_to_full_confirmation(self):
        self.assertEqual(normalize_settings({"entry_dependency_mode": "PROFILE"})["entry_dependency_mode"], "FULL_CONFIRMATION")
        self.assertEqual(normalize_settings({"entry_dependency_mode": "anything-new"})["entry_dependency_mode"], "FULL_CONFIRMATION")
        self.assertEqual(resolve_entry_dependency_mode({"entry_dependency_mode": "PROFILE"}), "FULL_CONFIRMATION")

    def test_ohlcv_aliases_normalize_to_ohlcv_volume_profile(self):
        for value in ("SIMPLE", "SIMPLE_OHLCV", "MAIN_APP_STYLE", "OHLCV_VOLUME", "OHLCV_VOLUME_PROFILE"):
            with self.subTest(value=value):
                self.assertEqual(normalize_settings({"entry_dependency_mode": value})["entry_dependency_mode"], "OHLCV_VOLUME_PROFILE")
                self.assertEqual(resolve_entry_dependency_mode({"entry_dependency_mode": value}), "OHLCV_VOLUME_PROFILE")

    def test_aggressive_profile_does_not_override_explicit_full_confirmation(self):
        settings = {
            "strategy_profile": "AGGRESSIVE",
            "entry_dependency_mode": "FULL_CONFIRMATION",
            "aggressive_uses_simple_ohlcv_entry": True,
        }

        self.assertEqual(normalize_settings(settings)["entry_dependency_mode"], "FULL_CONFIRMATION")
        self.assertEqual(resolve_entry_dependency_mode(settings), "FULL_CONFIRMATION")

    def test_market_playbook_aliases_normalize_to_market_context_settings(self):
        settings = normalize_settings({
            "market_playbook_enabled": False,
            "market_playbook_enforcement_enabled": True,
            "market_playbook_dynamic_thresholds_enabled": True,
            "market_playbook_position_sizing_enabled": True,
            "market_playbook_exit_policy_enabled": False,
            "market_playbook_expiry_scalp_enabled": True,
        })

        self.assertFalse(settings["market_context_enabled"])
        self.assertTrue(settings["market_context_enforcement_enabled"])
        self.assertTrue(settings["market_context_dynamic_thresholds_enabled"])
        self.assertTrue(settings["market_context_position_sizing_enabled"])
        self.assertFalse(settings["market_context_exit_policy_enabled"])
        self.assertTrue(settings["market_context_expiry_scalp_enabled"])


if __name__ == "__main__":
    unittest.main()
