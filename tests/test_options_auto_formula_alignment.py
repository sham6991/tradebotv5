import unittest

import pandas as pd

from options_auto.backtest.backtest_broker import BacktestBroker
from options_auto.backtest.backtest_engine import OptionsAutoBacktestEngine
from options_auto.execution.paper_broker import PaperBroker
from options_auto.execution.paper_lifecycle import PaperLifecycleEngine
from options_auto.indicators.technicals import (
    bid_ask_spread_pct,
    candle_shape,
    ema,
    market_depth_imbalance,
    true_range,
    vwap,
    wilder_rsi,
)
from options_auto.intelligence.decision_pipeline import evaluate_options_auto_decision
from options_auto.intelligence.feature_builder import build_index_features
from options_auto.intelligence.market_cue_engine import MarketCueEngine
from tests.test_options_auto_auto_spot import index_rows


class OptionsAutoFormulaAlignmentTests(unittest.TestCase):
    def test_ema_known_values(self):
        result = ema(pd.Series([100, 102, 104]), 3)
        self.assertAlmostEqual(result.iloc[-1], 102.5, places=4)

    def test_wilder_rsi_edges(self):
        up = wilder_rsi(pd.Series(range(100, 115)), 14)
        down = wilder_rsi(pd.Series(range(114, 99, -1)), 14)
        flat = wilder_rsi(pd.Series([100] * 15), 14)

        self.assertEqual(up.iloc[-1], 100)
        self.assertEqual(down.iloc[-1], 0)
        self.assertEqual(flat.iloc[-1], 50)

    def test_true_range_vwap_and_session_reset(self):
        tr = true_range(pd.DataFrame([
            {"high": 100, "low": 100, "close": 100},
            {"high": 110, "low": 95, "close": 105},
        ]))
        self.assertEqual(tr.iloc[-1], 15)

        frame = pd.DataFrame([
            {"datetime": "2026-06-04 09:15", "high": 110, "low": 100, "close": 105, "volume": 100},
            {"datetime": "2026-06-04 09:18", "high": 120, "low": 110, "close": 115, "volume": 200},
            {"datetime": "2026-06-05 09:15", "high": 210, "low": 200, "close": 205, "volume": 100},
        ])
        result = vwap(frame)

        self.assertAlmostEqual(result.iloc[1], 111.6667, places=4)
        self.assertEqual(result.iloc[2], 205)

    def test_candle_spread_and_depth_examples(self):
        shape = candle_shape(pd.DataFrame([{"open": 100, "high": 110, "low": 95, "close": 108}])).iloc[0]

        self.assertAlmostEqual(shape["body_pct"], 53.3333, places=3)
        self.assertAlmostEqual(shape["upper_wick_pct"], 13.3333, places=3)
        self.assertAlmostEqual(shape["lower_wick_pct"], 33.3333, places=3)
        self.assertAlmostEqual(bid_ask_spread_pct(142.25, 142.45), 0.1405, places=4)
        self.assertAlmostEqual(market_depth_imbalance(1450, 1300), 5.4545, places=4)

    def test_feature_builder_trend_strength_example(self):
        frame = pd.DataFrame([
            {"open": 22400 + i, "high": 22420 + i, "low": 22390 + i, "close": 22405 + i * 2, "volume": 1000 + i * 30}
            for i in range(55)
        ])

        features = build_index_features(frame)

        self.assertEqual(features["ema_alignment"], "BULLISH")
        self.assertGreater(features["trend_strength_score"], 40)
        self.assertTrue(features["warmup_complete"])

    def test_market_cue_phase_rules_and_news_weight(self):
        payload = {
            "fii_buy_value": 5000,
            "fii_sell_value": 1000,
            "dii_buy_value": 1000,
            "dii_sell_value": 500,
            "global_cue_score": 20,
            "previous_day_trend_score": 20,
            "news_score": 100,
            "option_oi_score": 20,
        }

        premarket = MarketCueEngine().evaluate(payload, phase="PREMARKET").to_dict()
        lunch = MarketCueEngine().evaluate({**payload, "index_features": {"trend_strength_score": 0}}, phase="LUNCH").to_dict()

        self.assertGreater(premarket["components"]["fii_dii"], 0)
        self.assertEqual(lunch["components"]["fii_dii"], 0)
        self.assertLessEqual(premarket["components"]["news"], 30)

    def test_strong_bullish_example_allows_ce(self):
        result = evaluate_options_auto_decision(
            mode="PAPER",
            settings={
                "mode": "PAPER",
                "underlying": "NIFTY",
                "buy_score_threshold": 70,
                "max_capital_per_trade_pct": 60,
                "max_risk_per_trade_pct": 5,
                "paper_starting_balance": 20000,
            },
            index_history=pd.DataFrame(index_rows()),
            option_candidates=[{
                "name": "NIFTY",
                "tradingsymbol": "NIFTY26JUN22500CE",
                "instrument_token": "1",
                "instrument_type": "CE",
                "strike": 22500,
                "expiry": "2026-06-05",
                "lot_size": 50,
                "tick_size": 0.05,
            }],
            quotes={"1": {
                "ltp": 142.40,
                "bid": 142.25,
                "ask": 142.45,
                "bid_qty": 1450,
                "ask_qty": 1300,
                "volume": 85000,
                "oi": 950000,
                "premium_return_1": 1.2,
                "premium_return_3": 4.5,
                "relative_volume": 1.6,
                "option_vwap": 140,
                "option_atr14": 5,
            }},
            market_cue_payload={
                "phase": "LUNCH",
                "spot": 22540,
                "features": {
                    "close": 22540,
                    "ema9": 22520,
                    "ema20": 22480,
                    "ema50": 22400,
                    "vwap": 22490,
                    "rsi14": 64,
                    "rsi_slope_3": 5,
                    "relative_volume": 1.8,
                    "atr_pct": 0.25,
                    "body_pct": 55,
                    "upper_wick_pct": 12,
                    "lower_wick_pct": 20,
                    "trend_strength_score": 75,
                },
                "technical_score": 75,
                "option_oi_score": 25,
            },
            risk_state={},
            account_state={"available_capital": 20000},
            timestamp="2026-06-04 12:00:00",
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["selected_side"], "CE")
        self.assertEqual(result["regime"]["regime"], "strong_bullish")
        self.assertTrue(result["selected_contract"]["premium_expansion_confirmed"])
        self.assertGreater(result["theta_premium_risk"]["expected_edge"]["expected_edge_after_costs"], 0)

    def test_live_mode_requires_live_index_candles_not_legacy_features(self):
        result = evaluate_options_auto_decision(
            mode="PAPER",
            settings={"mode": "PAPER", "underlying": "NIFTY", "buy_score_threshold": 20},
            index_history=pd.DataFrame(),
            option_candidates=[],
            quotes={},
            market_cue_payload={
                "phase": "LUNCH",
                "spot": 22540,
                "features": {"trend_strength_score": 90, "close": 22540},
                "technical_score": 90,
            },
            risk_state={},
            account_state={"available_capital": 20000},
            timestamp="2026-06-04 12:00:00",
        )

        self.assertFalse(result["allowed"])
        self.assertIn("Live index candle data is unavailable.", result["blockers"])

    def test_weak_far_otm_expiry_option_blocks(self):
        result = evaluate_options_auto_decision(
            mode="PAPER",
            settings={"mode": "PAPER", "underlying": "NIFTY", "buy_score_threshold": 40, "max_capital_per_trade_pct": 100, "max_risk_per_trade_pct": 10, "paper_starting_balance": 20000},
            index_history=pd.DataFrame(index_rows()),
            option_candidates=[{"name": "NIFTY", "tradingsymbol": "NIFTY26JUN23000CE", "instrument_token": "1", "instrument_type": "CE", "strike": 23000, "expiry": "2026-06-04", "lot_size": 50}],
            quotes={"1": {"ltp": 18, "bid": 17, "ask": 18.5, "bid_qty": 100, "ask_qty": 100, "volume": 3000, "oi": 12000, "premium_return_1": -0.5, "premium_return_3": 0.2, "relative_volume": 0.6, "option_atr14": 0.5}},
            market_cue_payload={"phase": "LUNCH", "spot": 22540, "features": {"trend_strength_score": 80, "close": 22540, "vwap": 22490, "ema9": 22520, "ema20": 22480, "ema50": 22400, "rsi14": 64, "relative_volume": 1.8, "atr_pct": 0.25}, "technical_score": 80},
            risk_state={},
            account_state={"available_capital": 20000},
            timestamp="2026-06-04 12:00:00",
        )

        blockers = " ".join(result["blockers"])
        self.assertFalse(result["allowed"])
        self.assertIn("Deep OTM disabled.", blockers)
        self.assertIn("Liquidity score too low.", blockers)
        self.assertIn("Spread too wide.", blockers)
        self.assertIn("Option premium is not confirming index direction.", blockers)

    def test_high_score_cannot_bypass_governor_risk_blocker(self):
        result = evaluate_options_auto_decision(
            mode="PAPER",
            settings={"mode": "PAPER", "underlying": "NIFTY", "buy_score_threshold": 20, "max_daily_loss": 1000, "max_capital_per_trade_pct": 100, "max_risk_per_trade_pct": 10, "paper_starting_balance": 20000},
            index_history=pd.DataFrame(index_rows()),
            option_candidates=[{"name": "NIFTY", "tradingsymbol": "NIFTY26JUN22500CE", "instrument_token": "1", "instrument_type": "CE", "strike": 22500, "expiry": "2026-06-25", "lot_size": 50}],
            quotes={"1": {"ltp": 40, "bid": 39.95, "ask": 40.05, "bid_qty": 3000, "ask_qty": 3000, "volume": 100000, "oi": 1000000, "premium_return_1": 2, "premium_return_3": 5, "relative_volume": 2, "option_vwap": 39, "option_atr14": 5}},
            market_cue_payload={"phase": "LUNCH", "spot": 22540, "features": {"trend_strength_score": 80, "close": 22540, "vwap": 22490, "ema9": 22520, "ema20": 22480, "ema50": 22400, "rsi14": 64, "relative_volume": 1.8, "atr_pct": 0.25}, "technical_score": 80},
            risk_state={"realized_pnl": -1200},
            account_state={"available_capital": 20000},
            timestamp="2026-06-04 12:00:00",
        )

        self.assertFalse(result["allowed"])
        self.assertIn("Max daily loss reached.", result["blockers"])

    def test_backtest_uses_option_premium_entry_and_charges(self):
        index = pd.DataFrame([
            {"datetime": f"2026-06-04 10:{i % 60:02d}:00", "open": 22400 + i * 3, "high": 22425 + i * 3, "low": 22390 + i * 3, "close": 22410 + i * 4, "volume": 1000 + i * 80}
            for i in range(60)
        ])
        option = pd.DataFrame([
            {"datetime": f"2026-06-04 10:{i % 60:02d}:00", "tradingsymbol": "NIFTY26JUN22500CE", "instrument_token": "1", "instrument_type": "CE", "strike": 22500, "expiry": "2026-06-25", "lot_size": 50, "open": 120 + i * 2.0, "high": 128 + i * 2.0, "low": 119 + i * 2.0, "close": 124 + i * 2.0, "bid": 123.9 + i * 2.0, "ask": 124.1 + i * 2.0, "bid_qty": 3000, "ask_qty": 3000, "volume": 100000 + i * 1000, "oi": 1000000}
            for i in range(60)
        ])

        result = OptionsAutoBacktestEngine().run(index, [option], {"buy_score_threshold": 20, "market_cue_alignment_required": False, "max_capital_per_trade_pct": 100, "max_risk_per_trade_pct": 10, "paper_starting_balance": 20000})

        entries = [row for row in result["decisions"] if row["decision"] == "ENTRY"]
        self.assertTrue(entries)
        self.assertLess(entries[0]["entry_price"], 1000)
        self.assertNotEqual(entries[0]["entry_price"], index.iloc[entries[0]["row"]]["close"])
        self.assertTrue(any(trade.get("charges", 0) > 0 for trade in result["trades"]))

    def test_backtest_exit_order_conservatively_uses_sl_first(self):
        candles = pd.DataFrame([
            {"open": 40, "high": 70, "low": 35, "close": 60},
            {"open": 60, "high": 72, "low": 25, "close": 68},
        ])

        trade = BacktestBroker().simulate_long_option_trade(candles, entry_price=40, stoploss=30, target=65, quantity=50, signal_index=0)

        self.assertEqual(trade["exit_reason"], "STOPLOSS")
        self.assertGreater(trade["charges"], 0)

    def test_paper_pending_timeout_and_oco_sl_cancels_target(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        decision = {
            "allowed": True,
            "settings": {"limit_order_timeout_seconds": 5},
            "trade_plan": {"tradingsymbol": "NIFTY26JUN22500CE", "side": "CE", "entry_price": 40, "stoploss": 35, "target": 50, "quantity": 50, "lot_size": 50},
        }
        pending = lifecycle.create_pending(decision, timeout_seconds=10, now_epoch=100)
        lifecycle.approve(pending["approval_id"], now_epoch=101)
        cancelled = lifecycle.process_market({"ltp": 45, "low": 44, "now_epoch": 107})

        self.assertEqual(cancelled["updates"][0]["action"], "ENTRY_CANCELLED")

        pending = lifecycle.create_pending(decision, timeout_seconds=10, now_epoch=200)
        lifecycle.approve(pending["approval_id"], now_epoch=201)
        lifecycle.process_market({"ltp": 39, "low": 39, "high": 42, "now_epoch": 202})
        closed = lifecycle.process_market({"ltp": 34, "low": 34, "high": 36, "now_epoch": 203})

        self.assertEqual(closed["updates"][0]["action"], "SL_FILLED")
        target_order = [order for order in lifecycle.broker.orders if order["order_id"] == lifecycle.closed_trades[-1]["target_order_id"]][0]
        self.assertEqual(target_order["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
