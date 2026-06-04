import os
import tempfile
import unittest

import pandas as pd

from options_auto.backtest.backtest_broker import BacktestBroker
from options_auto.data.fii_dii_loader import parse_fii_dii_csv_text
from options_auto.data.zerodha_pulse_news import NewsSentimentEngine
from options_auto.execution.kite_api_manager import KiteApiManager, RateLimiter
from options_auto.intelligence.adaptive_risk_engine import RiskEngine
from options_auto.intelligence.entry_timing_engine import EntryTimingEngine
from options_auto.intelligence.master_governor import MasterGovernor
from options_auto.intelligence.options_greeks_risk_engine import OptionsGreeksRiskEngine
from options_auto.telegram_safety import TelegramSafety
from options_auto.terminal_service import OptionsAutoTerminalService


def sample_payload():
    return {
        "mode": "PAPER",
        "timestamp": "2026-06-04 10:00:00",
        "spot": 22520,
        "settings": {
            "mode": "PAPER",
            "underlying": "NIFTY",
            "buy_score_threshold": 35,
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
        service = OptionsAutoTerminalService("results")

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


if __name__ == "__main__":
    unittest.main()
