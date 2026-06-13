import unittest

from options_auto.config.options_auto_defaults import normalize_settings
from options_auto.intelligence.market_context_router import MarketContextRouter


class OptionsAutoMarketContextRouterTests(unittest.TestCase):
    def route(self, **overrides):
        payload = {
            "market_cue": {"recommended_side": "CE", "confidence": 88, "cue": "strong_bullish"},
            "regime": {"recommended_side": "CE", "confidence": 90, "regime": "strong_bullish", "score": 80},
            "index_features": {"trend_strength_score": 80, "close": 22500, "vwap": 22440, "relative_volume": 1.8},
            "news_event_signal": {},
            "settings": normalize_settings({}),
            "timestamp": "2026-06-13T10:30:00",
            "feed_health": {},
        }
        payload.update(overrides)
        return MarketContextRouter().route(**payload).to_dict()

    def test_strong_bull_maps_to_ce_momentum_report_only(self):
        result = self.route()

        self.assertEqual(result["market_type"], "STRONG_BULL_TREND")
        self.assertEqual(result["playbook"], "LONG_CE_MOMENTUM")
        self.assertEqual(result["recommended_side"], "CE")
        self.assertEqual(result["permission"], "ALLOW")
        self.assertEqual(result["enforcement"], "REPORT_ONLY")
        self.assertFalse(result["would_block"])

    def test_strong_bear_maps_to_pe_momentum(self):
        result = self.route(
            market_cue={"recommended_side": "PE", "confidence": 84, "cue": "strong_bearish"},
            regime={"recommended_side": "PE", "confidence": 86, "regime": "strong_bearish", "score": -82},
            index_features={"trend_strength_score": -80, "close": 22400, "vwap": 22480},
        )

        self.assertEqual(result["market_type"], "STRONG_BEAR_TREND")
        self.assertEqual(result["playbook"], "LONG_PE_MOMENTUM")
        self.assertEqual(result["recommended_side"], "PE")

    def test_sideways_waits(self):
        result = self.route(
            market_cue={"recommended_side": "WAIT", "confidence": 70, "cue": "neutral_sideways"},
            regime={"recommended_side": "WAIT", "confidence": 72, "regime": "neutral_sideways", "score": 5},
            index_features={"trend_strength_score": 5, "close": 22500, "vwap": 22498},
        )

        self.assertEqual(result["market_type"], "SIDEWAYS_RANGE")
        self.assertEqual(result["playbook"], "WAIT_NO_TRADE")
        self.assertEqual(result["recommended_side"], "WAIT")
        self.assertTrue(result["would_block"])

    def test_low_liquidity_overrides_trend(self):
        result = self.route(feed_health={"feed_stale": True, "valid_quote_count": 0})

        self.assertEqual(result["market_type"], "LOW_LIQUIDITY")
        self.assertEqual(result["playbook"], "WAIT_LOW_LIQUIDITY")
        self.assertEqual(result["recommended_side"], "WAIT")

    def test_news_warning_without_confirmation_does_not_become_shock(self):
        result = self.route(news_event_signal={"status": "NEWS_EVENT_SHOCK", "score": 90, "market_confirmation": False})

        self.assertEqual(result["market_type"], "STRONG_BULL_TREND")
        self.assertIn("news_event_signal", result)

    def test_ce_pe_conflict_waits_without_confidence_gap(self):
        result = self.route(
            market_cue={"recommended_side": "CE", "confidence": 80, "cue": "strong_bullish"},
            regime={"recommended_side": "PE", "confidence": 78, "regime": "strong_bearish", "score": -70},
            index_features={"trend_strength_score": 0, "close": 22500, "vwap": 22500},
        )

        self.assertEqual(result["recommended_side"], "WAIT")
        self.assertEqual(result["playbook"], "WAIT_NO_TRADE")


if __name__ == "__main__":
    unittest.main()
