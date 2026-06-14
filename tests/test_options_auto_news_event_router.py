import unittest

from options_auto.config.options_auto_defaults import normalize_settings
from options_auto.intelligence.news_event_router import NewsEventRouter


class OptionsAutoNewsEventRouterTests(unittest.TestCase):
    def route(self, items, **settings):
        return NewsEventRouter().route(
            {
                "provider": "ZERODHA_PULSE",
                "status": "OK",
                "items": items,
                "fetched_at": "2026-06-14T10:00:00+00:00",
                "fetched_at_epoch": 1000,
            },
            normalize_settings(settings),
            market_context={"index_features": {"trend_strength_score": 80, "atr_pct": 0.55}},
        ).to_dict()

    def test_confirmed_high_impact_headline_becomes_news_shock(self):
        signal = self.route([
            {"title": "Nifty crashes after surprise RBI rate hike", "age_minutes": 2},
        ])

        self.assertEqual(signal["status"], "NEWS_EVENT_SHOCK")
        self.assertTrue(signal["would_block"])
        self.assertTrue(signal["market_confirmation"])
        self.assertEqual(signal["event_type"], "RATE_POLICY")

    def test_fetch_failure_fails_open(self):
        signal = NewsEventRouter().route(
            {"provider": "ZERODHA_PULSE", "status": "FETCH_FAILED", "error": "timeout"},
            normalize_settings({"news_event_fail_open": True}),
            market_context={},
        ).to_dict()

        self.assertEqual(signal["status"], "FETCH_FAILED")
        self.assertFalse(signal["would_block"])


if __name__ == "__main__":
    unittest.main()
