import unittest
from datetime import datetime, timedelta

from intraday.models import IntradaySettings
from intraday.stock_candle_builder import StockTickCandleBuilder
from intraday.stock_data_readiness import evaluate_stock_data_readiness
from intraday.stock_strategy_profile_policy import resolve_intraday_strategy_profile


def valid_payload(profile="BALANCED"):
    return {
        "mode": "PAPER",
        "strategy_profile": profile,
        "stocks": ["NSE:INFY", "NSE:RELIANCE", "NSE:TCS", "NSE:HDFCBANK", "NSE:ICICIBANK"],
    }


class IntradayProfileAndLiveDataTests(unittest.TestCase):
    def test_intraday_settings_has_strategy_profile_and_thresholds(self):
        conservative = IntradaySettings.from_payload(valid_payload("CONSERVATIVE"))
        balanced = IntradaySettings.from_payload(valid_payload("BALANCED"))
        aggressive = IntradaySettings.from_payload(valid_payload("AGGRESSIVE"))

        self.assertEqual(conservative.strategy_profile, "CONSERVATIVE")
        self.assertEqual(conservative.minimum_entry_score, 80)
        self.assertEqual(balanced.minimum_entry_score, 70)
        self.assertEqual(aggressive.minimum_entry_score, 62)
        self.assertGreater(conservative.relative_volume_threshold, aggressive.relative_volume_threshold)
        self.assertTrue(conservative.higher_timeframe_confirmation)
        self.assertFalse(aggressive.higher_timeframe_confirmation)

    def test_profile_policy_exposes_hard_safety_overrides(self):
        settings = IntradaySettings.from_payload(valid_payload("AGGRESSIVE"))
        policy = resolve_intraday_strategy_profile(settings)

        self.assertEqual(policy["strategy_profile"], "AGGRESSIVE")
        self.assertTrue(policy["allow_forming_candle_entry"])
        self.assertTrue(policy["hard_safety_overrides"]["no_stale_live_data"])
        self.assertTrue(policy["hard_safety_overrides"]["kill_switch_always_active"])

    def test_live_ticks_build_stock_candles(self):
        builder = StockTickCandleBuilder(interval="3minute")
        start = datetime(2026, 6, 5, 10, 0, 12)

        builder.add_tick("INFY", {"last_price": 1500, "timestamp": start})
        result = builder.add_tick("INFY", {"last_price": 1508, "timestamp": start + timedelta(minutes=3)})

        rows = builder.rows("INFY")
        self.assertTrue(result["completed_candle"])
        self.assertEqual(rows[0]["open"], 1500)
        self.assertEqual(rows[0]["close"], 1500)
        self.assertEqual(rows[-1]["close"], 1508)

    def test_stale_or_missing_live_data_pauses_new_entries(self):
        settings = IntradaySettings.from_payload(valid_payload("BALANCED"))
        now = datetime(2026, 6, 5, 10, 18)
        old = now - timedelta(minutes=12)
        market_data = {
            "INFY": {
                "source": "zerodha_paper_data",
                "source_status": "OK",
                "candles": [{"timestamp": old.isoformat(timespec="seconds"), "open": 1, "high": 1, "low": 1, "close": 1}],
            }
        }

        health = evaluate_stock_data_readiness(settings, market_data, now=now)

        self.assertFalse(health["new_entries_allowed"])
        self.assertIn("INFY live stock candles are stale.", health["blockers"])
        self.assertTrue(health["warnings"])


if __name__ == "__main__":
    unittest.main()
