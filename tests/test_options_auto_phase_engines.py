import os
import tempfile
import unittest

import pandas as pd

from options_auto.backtest.backtest_broker import BacktestBroker
from options_auto.data.fii_dii_loader import parse_fii_dii_csv_text
from options_auto.data.zerodha_pulse_news import NewsSentimentEngine
from options_auto.execution.kite_api_manager import KiteApiManager, RateLimiter
from options_auto.intelligence.adaptive_risk_engine import RiskEngine
from options_auto.intelligence.decision_pipeline import evaluate_options_auto_decision
from options_auto.intelligence.entry_timing_engine import EntryTimingEngine
from options_auto.intelligence.exit_manager import build_long_option_trade_plan
from options_auto.intelligence.master_governor import MasterGovernor
from options_auto.intelligence.options_greeks_risk_engine import OptionsGreeksRiskEngine
from options_auto.telegram_safety import TelegramSafety
from options_auto.terminal_service import OptionsAutoTerminalService
from tests.test_options_auto_auto_spot import FakeOptionsZerodha


def sample_payload():
    return {
        "mode": "PAPER",
        "timestamp": "2026-06-04 10:00:00",
        "spot": 22520,
        "settings": {
            "mode": "PAPER",
            "underlying": "NIFTY",
            "buy_score_threshold": 35,
            "atm_scan_strike_span": 0,
            "premium_expansion_required": False,
            "max_capital_per_trade_pct": 100,
            "max_risk_per_trade_pct": 10,
            "paper_starting_balance": 20000,
        },
        "market_cue": {"phase": "LUNCH", "technical_score": 58, "option_oi_score": 25, "news_score": 1},
        "features": {"ema_alignment_score": 25, "vwap_score": 18, "rsi_slope_score": 15, "volume_score": 12, "depth_score": 8},
        "instruments": [
            {"name": "NIFTY", "tradingsymbol": "NIFTY26JUN22500CE", "instrument_token": "1", "instrument_type": "CE", "strike": 22500, "expiry": "2026-06-25", "lot_size": 50},
        ],
        "quotes": {
            "1": {"ltp": 40, "bid": 39.95, "ask": 40.05, "bid_qty": 1500, "ask_qty": 1400, "volume": 90000, "oi": 950000, "premium_return_1": 1.2, "premium_return_3": 4.5, "relative_volume": 1.6, "option_vwap": 39, "option_atr14": 5, "momentum_score": 80},
        },
    }


class OptionsAutoPhaseEngineTests(unittest.TestCase):
    def test_risk_and_governor_block_max_daily_loss(self):
        risk = RiskEngine().evaluate({"max_daily_loss": 1000, "max_daily_profit_lock": 5000, "max_trades_per_day": 3, "max_open_trades": 1, "max_consecutive_losses": 2}, {"realized_pnl": -1200})

        result = MasterGovernor().evaluate(
            {"mode": "PAPER"},
            {"blockers": []},
            risk,
            {"blockers": []},
            {"blockers": []},
            strategy={"selected": True, "blockers": []},
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["state"], "BLOCKED_BY_RISK")

    def test_paper_execute_plan_simulates_local_order_only(self):
        service = OptionsAutoTerminalService("results", kite_client_provider=lambda _mode: FakeOptionsZerodha(spot=22520, option_price=40))

        result = service.execute_paper_plan(sample_payload())

        self.assertTrue(result["allowed"])
        self.assertEqual(result["paper_order"]["status"], "OPEN")
        self.assertEqual(result["pending_entry"]["status"], "ENTRY_PENDING")
        self.assertTrue(str(result["paper_order"]["order_id"]).startswith("PAPER-"))

    def test_backtest_broker_ignores_same_candle_target_but_allows_stop(self):
        candles = pd.DataFrame([
            {"open": 40, "high": 70, "low": 35, "close": 60},
            {"open": 60, "high": 72, "low": 55, "close": 68},
        ])

        target_trade = BacktestBroker().simulate_long_option_trade(candles, entry_price=40, stoploss=30, target=65, quantity=50, signal_index=0)
        stop_trade = BacktestBroker().simulate_long_option_trade(candles, entry_price=40, stoploss=36, target=65, quantity=50, signal_index=0)

        self.assertEqual(target_trade["exit_reason"], "TARGET")
        self.assertEqual(target_trade["exit_index"], 1)
        self.assertEqual(stop_trade["exit_reason"], "STOPLOSS_SAME_CANDLE")

    def test_backtest_writes_report_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            result = service.backtest({
                "candles": [
                    {"datetime": "2026-06-01 09:15", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100},
                    {"datetime": "2026-06-01 09:18", "open": 2, "high": 3, "low": 2, "close": 3, "volume": 120},
                ]
            })

            self.assertTrue(os.path.exists(result["report"]["audit_json"]))
            self.assertTrue(os.path.exists(result["report"]["decisions_csv"]))

    def test_news_and_fii_dii_parsers_degrade_cleanly(self):
        news = NewsSentimentEngine().classify_items([
            {"title": "Nifty rally after strong inflow"},
            {"title": "RBI rate inflation warning hits market"},
        ])
        fii_dii = parse_fii_dii_csv_text("category,buy,sell,net\nFII,100,50,50\nDII,20,70,-50\n")

        self.assertIn(news["sentiment"], {"positive", "negative", "neutral"})
        self.assertEqual(fii_dii["status"], "OK")
        self.assertEqual(fii_dii["fii_net"], 50)

    def test_rate_limiter_and_telegram_safety(self):
        api = KiteApiManager(limiter=RateLimiter(max_calls=1, per_seconds=60))
        self.assertTrue(api.call("first", lambda: "ok")["ok"])
        self.assertFalse(api.call("second", lambda: "blocked")["ok"])

        telegram = TelegramSafety().validate("disable_sl", "U1", {"telegram_allowed_user_ids": ["U1"]})
        self.assertFalse(telegram["allowed"])

    def test_entry_timing_and_theta_risk_block_bad_setups(self):
        timing = EntryTimingEngine().evaluate(
            {"high": 120, "low": 90, "atr14": 5},
            {"ltp": 110, "intended_entry": 100},
            {"max_chase_points": 3},
        )
        risk = OptionsGreeksRiskEngine().evaluate(
            {"moneyness": "OTM", "expiry": "2026-06-04", "spread_pct": 0.1},
            {"max_spread_pct": 0.6},
            today=pd.Timestamp("2026-06-04").date(),
        )

        self.assertFalse(timing["allowed"])
        self.assertFalse(risk["allowed"])

    def test_no_new_trade_after_blocks_entry_timing(self):
        timing = EntryTimingEngine().evaluate(
            {"high": 120, "low": 110, "open": 112, "close": 118},
            {"ltp": 118, "intended_entry": 118},
            {"timestamp": "2026-06-04 15:01:00", "no_new_trade_after": "15:00"},
        )

        self.assertFalse(timing["allowed"])
        self.assertIn("No new trades after configured cutoff.", timing["blockers"])

    def test_cooldown_settings_use_risk_state_timestamps(self):
        risk = RiskEngine().evaluate(
            {
                "max_daily_loss": 1000,
                "max_daily_profit_lock": 5000,
                "max_trades_per_day": 3,
                "max_open_trades": 1,
                "max_consecutive_losses": 5,
                "cooldown_after_loss_seconds": 600,
                "cooldown_after_rejection_seconds": 180,
                "cooldown_after_api_error_seconds": 300,
            },
            {
                "consecutive_losses": 1,
                "last_loss_epoch": 100,
                "rejected_orders": 1,
                "last_rejection_epoch": 200,
                "api_failures": 1,
                "last_api_error_epoch": 250,
            },
            now_epoch=300,
        )

        self.assertFalse(risk["allowed"])
        self.assertIn("Loss cooldown is active.", risk["blockers"])
        self.assertIn("Order rejection cooldown is active.", risk["blockers"])
        self.assertIn("Broker/API error cooldown is active.", risk["blockers"])

    def test_market_context_settings_adjust_threshold_and_trade_plan(self):
        payload = sample_payload()
        payload["settings"] = {
            **payload["settings"],
            "market_context_enabled": True,
            "market_context_dynamic_thresholds_enabled": True,
            "market_context_exit_policy_enabled": True,
            "buy_score_threshold": 70,
        }

        decision = evaluate_options_auto_decision(
            "PAPER",
            payload["settings"],
            pd.DataFrame(payload["candles"]) if payload.get("candles") else pd.DataFrame([{"datetime": "2026-06-04 10:00", "open": 22500, "high": 22580, "low": 22480, "close": 22550, "volume": 100000}]),
            payload["instruments"],
            payload["quotes"],
            payload,
            {},
            {"available_margin": 20000},
            payload["timestamp"],
        )

        self.assertIn("market_context", decision)
        self.assertIn("trade_candidate_validation", decision)

        plan = build_long_option_trade_plan(
            {"tradingsymbol": "NIFTY26JUN22500CE", "instrument_token": 1, "option_type": "CE", "ask": 100, "ltp": 100, "tick_size": 0.05, "lot_size": 50},
            {"quantity": 50, "lots": 1},
            {"regime": "strong_bullish", "target_multiplier": 1.6},
            {"market_context_target_multiplier_adjustment": 0.15, "market_context_stoploss_multiplier_adjustment": -0.15},
        )
        self.assertGreater(plan["risk_reward"], 1.6)
        self.assertLess(plan["stop_distance"], 3.0)


if __name__ == "__main__":
    unittest.main()
