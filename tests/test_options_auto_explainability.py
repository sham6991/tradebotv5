import unittest
import tempfile

import pandas as pd

from options_auto.execution.execution_safety import DataQualityEngine
from options_auto.intelligence.decision_pipeline import evaluate_options_auto_decision
from options_auto.intelligence.entry_timing_engine import EntryTimingEngine
from options_auto.intelligence.master_governor import MasterGovernor
from options_auto.intelligence.market_context_router import MarketContextRouter
from options_auto.intelligence.trade_score_engine import TradeScoreEngine
from options_auto.terminal_service import OptionsAutoTerminalService


def index_rows(count=55):
    return [
        {
            "datetime": f"2026-06-04 10:{i % 60:02d}:00",
            "open": 22400 + i * 4,
            "high": 22418 + i * 4,
            "low": 22396 + i * 4,
            "close": 22416 + i * 5,
            "volume": 10000 + i * 700,
        }
        for i in range(count)
    ]


def candidate():
    return {
        "name": "NIFTY",
        "tradingsymbol": "NIFTY26JUN22500CE",
        "instrument_token": "1",
        "instrument_type": "CE",
        "option_type": "CE",
        "strike": 22500,
        "expiry": "2026-06-25",
        "lot_size": 50,
        "tick_size": 0.05,
    }


def settings(**overrides):
    base = {
        "mode": "PAPER",
        "underlying": "NIFTY",
        "entry_dependency_mode": "FULL_CONFIRMATION",
        "premium_expansion_required": False,
        "buy_score_threshold": 0,
        "max_spread_pct": 0.6,
        "max_capital_per_trade_pct": 100,
        "max_risk_per_trade_pct": 10,
        "paper_starting_balance": 20000,
        "market_context_enabled": True,
        "market_context_enforcement_enabled": False,
    }
    base.update(overrides)
    return base


def cue_payload(**overrides):
    base = {
        "phase": "LUNCH",
        "spot": 22540,
        "features": {
            "trend_strength_score": 80,
            "close": 22540,
            "vwap": 22490,
            "ema9": 22520,
            "ema20": 22480,
            "ema50": 22400,
            "rsi14": 64,
            "relative_volume": 1.8,
            "atr_pct": 0.25,
        },
        "technical_score": 80,
        "quote_age_seconds": 0,
    }
    base.update(overrides)
    return base


class OptionsAutoExplainabilityTests(unittest.TestCase):
    def test_no_duplicate_spread_blockers(self):
        result = evaluate_options_auto_decision(
            mode="PAPER",
            settings=settings(),
            index_history=pd.DataFrame(index_rows()),
            option_candidates=[candidate()],
            quotes={"1": {"ltp": 100, "bid": 98, "ask": 102, "bid_qty": 3000, "ask_qty": 3000, "volume": 100000, "oi": 1000000, "premium_return_1": 2, "premium_return_3": 5, "relative_volume": 2, "option_vwap": 99, "option_atr14": 5}},
            market_cue_payload=cue_payload(),
            risk_state={},
            account_state={"available_capital": 20000},
            timestamp="2026-06-04 12:00:00",
        )

        spread_blockers = [item for item in result["blockers"] if "spread" in item.lower()]
        self.assertEqual(spread_blockers, ["Quote spread is too wide."])
        self.assertEqual(result["data_quality"]["blockers"], ["Quote spread is too wide."])
        self.assertEqual(result["governor"]["primary_block_stage"], "BLOCKED_BY_DATA")

    def test_no_duplicate_chase_blockers(self):
        result = evaluate_options_auto_decision(
            mode="PAPER",
            settings=settings(max_spread_pct=2.0, max_chase_points=3),
            index_history=pd.DataFrame(index_rows()),
            option_candidates=[candidate()],
            quotes={"1": {"ltp": 110, "bid": 109.95, "ask": 110.05, "bid_qty": 3000, "ask_qty": 3000, "volume": 100000, "oi": 1000000, "premium_return_1": 2, "premium_return_3": 5, "relative_volume": 2, "option_vwap": 108, "option_atr14": 5}},
            market_cue_payload=cue_payload(intended_entry=100),
            risk_state={},
            account_state={"available_capital": 20000},
            timestamp="2026-06-04 12:00:00",
        )

        self.assertFalse(result["allowed"])
        self.assertIn("Entry is chasing premium.", result["blockers"])
        self.assertNotIn("FOMO/chase filter rejected the setup.", result["blockers"])
        self.assertEqual(result["discipline"]["blockers"], [])

    def test_market_context_not_contract_validator(self):
        context = MarketContextRouter().route(
            market_cue={"cue": "strong_bullish", "recommended_side": "CE", "confidence": 85},
            regime={"regime": "strong_bullish", "recommended_side": "CE", "confidence": 85},
            index_features={"trend_strength_score": 80, "close": 22500, "vwap": 22480, "atr_pct": 0.25},
            news_event_signal={},
            settings={"market_context_enabled": True, "market_context_enforcement_enabled": True},
            timestamp="2026-06-04 12:00:00",
            feed_health={},
        ).to_dict()

        text = " ".join(context.get("blockers") or []).lower()
        self.assertNotIn("contract", text)
        self.assertNotIn("instrument", text)
        self.assertNotIn("spread", text)
        self.assertIn(context["recommended_side"], {"CE", "PE", "WAIT"})

    def test_trade_score_soft_only(self):
        score = TradeScoreEngine().score(
            {"option_type": "CE", "spread_pct": 5.0, "total_depth": 0, "liquidity_score": 0},
            {"selected_side": "CE", "settings": {}},
        )

        self.assertIn("score", score)
        self.assertIn("breakdown", score)
        self.assertNotIn("blockers", score)
        self.assertFalse(score.get("allowed") is False)

    def test_why_no_trade_primary_reason_stage(self):
        governor = MasterGovernor().evaluate(
            {"mode": "PAPER"},
            DataQualityEngine().validate_quote({"ltp": 0}, {}).to_dict(),
            {"blockers": ["Max daily loss reached."], "warnings": []},
            {"blockers": [], "warnings": []},
            {"blockers": [], "warnings": []},
            strategy={"selected": False, "blockers": ["No selected trade candidate."]},
        )

        self.assertFalse(governor["allowed"])
        self.assertEqual(governor["state"], "BLOCKED_BY_DATA")
        self.assertEqual(governor["primary_block_stage"], "BLOCKED_BY_DATA")
        self.assertEqual(governor["primary_blocker"], "Quote LTP is unavailable.")
        self.assertEqual(governor["blocker_stages"][0]["stage"], "BLOCKED_BY_DATA")

    def test_freshness_and_explainability_are_observability_only(self):
        result = evaluate_options_auto_decision(
            mode="PAPER",
            settings=settings(),
            index_history=pd.DataFrame(index_rows()),
            option_candidates=[candidate()],
            quotes={"1": {"ltp": 100, "bid": 99.95, "ask": 100.05, "bid_qty": 3000, "ask_qty": 3000, "volume": 100000, "oi": 1000000, "premium_return_1": 2, "premium_return_3": 5, "relative_volume": 2, "option_vwap": 99, "option_atr14": 5, "age_seconds": 0}},
            market_cue_payload=cue_payload(signal_age_seconds=1),
            risk_state={},
            account_state={"available_capital": 20000},
            timestamp="2026-06-04 12:00:00",
        )

        self.assertIn("freshness", result)
        self.assertIn("explainability", result)
        self.assertEqual(result["explainability"]["order_execution_impact"], "NONE_OBSERVABILITY_ONLY")
        self.assertEqual(result["decision_snapshot"]["freshness"], result["freshness"])
        self.assertEqual(result["decision_snapshot"]["explainability"], result["explainability"])
        self.assertNotIn("Freshness", " ".join(result["blockers"]))
        self.assertNotIn("Explainability", " ".join(result["blockers"]))
        self.assertEqual(result["side_selection"]["side_contract_mismatch"], False)

    def test_runtime_freshness_tags_do_not_mutate_decision_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            decision = {
                "allowed": True,
                "blockers": [],
                "selected_side": "CE",
                "trade_plan": {"tradingsymbol": "NIFTY26JUN22500CE"},
                "freshness": {"tags": {}},
                "explainability": {"order_execution_impact": "NONE_OBSERVABILITY_ONLY"},
                "decision_snapshot": {},
            }
            before = {key: decision[key] for key in ("allowed", "blockers", "selected_side", "trade_plan")}

            service._attach_runtime_observability_locked(decision, "PAPER")

            after = {key: decision[key] for key in ("allowed", "blockers", "selected_side", "trade_plan")}
            self.assertEqual(after, before)
            self.assertIn("ready_trade_plan", decision["freshness"]["tags"])
            self.assertIn("contract_lock", decision["freshness"]["tags"])
            self.assertIn("feed_roles", decision["freshness"]["tags"])


if __name__ == "__main__":
    unittest.main()
