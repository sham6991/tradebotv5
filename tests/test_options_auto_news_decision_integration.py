import unittest

import pandas as pd

from options_auto.config.options_auto_defaults import normalize_settings
from options_auto.constants import MODE_PAPER
from options_auto.intelligence.decision_pipeline import evaluate_options_auto_decision


class OptionsAutoNewsDecisionIntegrationTests(unittest.TestCase):
    def test_news_signal_reaches_market_context_router(self):
        index_history = pd.DataFrame([
            {"datetime": "2026-06-14 10:00", "open": 22500, "high": 22550, "low": 22480, "close": 22520, "volume": 1000},
            {"datetime": "2026-06-14 10:03", "open": 22520, "high": 22600, "low": 22510, "close": 22590, "volume": 1500},
            {"datetime": "2026-06-14 10:06", "open": 22590, "high": 22640, "low": 22570, "close": 22620, "volume": 1700},
        ])
        settings = normalize_settings({
            "market_context_enforcement_enabled": True,
            "news_event_enabled": True,
            "news_event_min_score_for_shock": 70,
        })
        decision = evaluate_options_auto_decision(
            mode=MODE_PAPER,
            settings=settings,
            index_history=index_history,
            option_candidates=[],
            quotes={},
            market_cue_payload={
                "technical_score": 80,
                "option_oi_score": 25,
                "news_event_signal": {"status": "NEWS_EVENT_SHOCK", "score": 90, "market_confirmation": True},
            },
            risk_state={},
            account_state={"available_capital": 100000},
            timestamp="2026-06-14T10:06:00",
        )

        self.assertEqual(decision["news_event_signal"]["status"], "NEWS_EVENT_SHOCK")
        self.assertEqual(decision["market_context"]["market_type"], "NEWS_EVENT_SHOCK")
        self.assertIn("Market context", "; ".join(decision["blockers"]))


if __name__ == "__main__":
    unittest.main()
