import unittest

from config_profile import build_settings_profile, normalize_settings


class ConfigProfileTests(unittest.TestCase):
    def test_settings_hash_ignores_session_id_and_secret_like_keys(self):
        base = {
            "balance": 100000,
            "lot_size": 75,
            "session_id": "S1",
            "api_secret": "one",
            "access_token": "token-one",
            "zerodha_margin_fetched": "true",
            "available_margin": 1000,
        }
        changed_runtime_values = {
            **base,
            "session_id": "S2",
            "api_secret": "two",
            "access_token": "token-two",
            "zerodha_margin_fetched": "false",
            "available_margin": 999999,
        }

        self.assertEqual(
            build_settings_profile(base)["settings_hash"],
            build_settings_profile(changed_runtime_values)["settings_hash"],
        )
        self.assertNotIn("api_secret", normalize_settings(base))
        self.assertNotIn("access_token", normalize_settings(base))
        self.assertNotIn("available_margin", normalize_settings(base))

    def test_settings_hash_changes_when_trade_setting_changes(self):
        first = build_settings_profile({"lot_size": 75, "profit_points": 20})
        second = build_settings_profile({"lot_size": 75, "profit_points": 25})

        self.assertNotEqual(first["settings_hash"], second["settings_hash"])
        self.assertTrue(first["settings_version"].startswith("settings-v2-"))


if __name__ == "__main__":
    unittest.main()
