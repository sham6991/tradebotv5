import unittest

import pandas as pd

from options_auto.intelligence.decision_pipeline import evaluate_options_auto_decision
from tests.test_options_auto_auto_spot import index_rows


def _candidate():
    return {
        "name": "NIFTY",
        "tradingsymbol": "NIFTY26JUN22500CE",
        "instrument_token": "1",
        "instrument_type": "CE",
        "strike": 22500,
        "expiry": "2026-06-25",
        "lot_size": 50,
        "tick_size": 0.05,
    }


def _quote():
    return {
        "ltp": 100,
        "last_price": 100,
        "bid": 99.95,
        "ask": 100.05,
        "bid_qty": 2500,
        "ask_qty": 2500,
        "volume": 100000,
        "oi": 500000,
        "premium_return_1": 1.2,
        "premium_return_3": 2.4,
        "option_vwap": 98,
        "option_atr14": 5,
        "age_seconds": 0,
    }


class OptionsAutoMarketContextIntegrationTests(unittest.TestCase):
    def decision(self, **settings):
        merged_settings = {
            "mode": "PAPER",
            "underlying": "NIFTY",
            "buy_score_threshold": 40,
            "entry_dependency_mode": "OHLCV_VOLUME_PROFILE",
            "max_capital_per_trade_pct": 100,
            "max_risk_per_trade_pct": 10,
            "paper_starting_balance": 20000,
            **settings,
        }
        return evaluate_options_auto_decision(
            mode="PAPER",
            settings=merged_settings,
            index_history=pd.DataFrame(index_rows()),
            option_candidates=[_candidate()],
            quotes={"1": _quote(), "NIFTY26JUN22500CE": _quote()},
            market_cue_payload={"side": "CE", "quote_age_seconds": 0},
            risk_state={},
            account_state={"available_capital": 20000},
            timestamp="2026-06-13T10:30:00",
        )

    def test_report_only_market_context_does_not_mutate_trade_output(self):
        baseline = self.decision(market_context_enabled=False)
        report_only = self.decision(market_context_enabled=True, market_context_enforcement_enabled=False)

        for key in ("allowed", "selected_side", "selected_contract", "trade_plan", "blockers"):
            self.assertEqual(report_only[key], baseline[key], key)
        self.assertEqual(report_only["market_context"]["enforcement"], "REPORT_ONLY")
        self.assertIn("trade_candidate_validation", report_only)

    def test_enforced_sideways_context_blocks_without_changing_contract(self):
        result = self.decision(
            market_context_enforcement_enabled=True,
            entry_dependency_mode="FULL_CONFIRMATION",
            buy_score_threshold=1,
        )

        self.assertIn("market_context", result)
        if result["market_context"]["would_block"]:
            self.assertTrue(any("Market context blocked trade" in item for item in result["blockers"]))


if __name__ == "__main__":
    unittest.main()
