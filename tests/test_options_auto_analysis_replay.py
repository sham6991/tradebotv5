import os
import tempfile
import unittest

from options_auto.core.promotion import PromotionManager
from options_auto.intelligence.missed_trade_learning import MissedTradeLearning
from options_auto.intelligence.strategy_drift import StrategyDriftMonitor
from options_auto.telegram_safety import TelegramSafety
from options_auto.terminal_service import OptionsAutoTerminalService


def shadow_payload():
    return {
        "mode": "SHADOW",
        "spot": 22520,
        "settings": {
            "mode": "SHADOW",
            "underlying": "NIFTY",
            "buy_score_threshold": 35,
            "max_capital_per_trade_pct": 100,
        },
        "market_cue": {"phase": "LUNCH", "technical_score": 50, "option_oi_score": 25, "news_score": 1},
        "features": {"ema_alignment_score": 25, "vwap_score": 18, "rsi_slope_score": 15, "volume_score": 12, "depth_score": 8},
        "instruments": [
            {"name": "NIFTY", "tradingsymbol": "NIFTY26JUN22500CE", "instrument_token": "1", "instrument_type": "CE", "strike": 22500, "lot_size": 50},
        ],
        "quotes": {
            "1": {"ltp": 40, "bid": 39.95, "ask": 40.05, "bid_qty": 1500, "ask_qty": 1400, "volume": 90000, "oi": 950000, "momentum_score": 80},
        },
    }


class OptionsAutoAnalysisReplayTests(unittest.TestCase):
    def test_shadow_report_is_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            service.start_shadow(shadow_payload())

            report = service.shadow_report()

            self.assertEqual(report["signals"], 1)
            self.assertTrue(os.path.exists(report["saved_report"]))

    def test_promotion_cannot_jump_to_aggressive_without_force_override(self):
        metrics = {"current_stage": "LEARNING", "requested_stage": "AGGRESSIVE", "sessions_completed": 5, "net_pnl": 1000, "max_drawdown_pct": 3}

        blocked = PromotionManager().evaluate(metrics)
        forced = PromotionManager().evaluate({**metrics, "force_override": True})

        self.assertFalse(blocked["promotion_allowed"])
        self.assertIn("jump stages", blocked["blockers"][0])
        self.assertTrue(forced["promotion_allowed"])
        self.assertIn("Force override", forced["warnings"][0])

    def test_strategy_drift_reports_reductions_and_analysis_only(self):
        trades = [{"pnl": -100, "false_signal": True, "slippage_points": 3, "premium_response_score": 30} for _ in range(10)]

        result = StrategyDriftMonitor().evaluate(trades)

        self.assertEqual(result["state"], "DRIFT_DETECTED")
        self.assertEqual(result["suggested_size_multiplier"], 0.5)
        self.assertTrue(result["analysis_only"])
        self.assertTrue(result["lock_engine"])

    def test_missed_trade_learning_classifies_rejected_winners(self):
        result = MissedTradeLearning().evaluate([
            {"allowed": True, "actual_pnl": 120},
            {"allowed": False, "actual_pnl": 80, "reason": "Spread too wide"},
            {"allowed": False, "actual_pnl": -40},
        ])

        self.assertEqual(result["accepted_won"], 1)
        self.assertEqual(result["rejected_would_have_won"], 1)
        self.assertEqual(result["live_parameter_changes"], [])

    def test_market_replay_is_analysis_only_and_saves_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            result = service.market_replay({
                "candles": [
                    {"datetime": "2026-06-04 09:15", "open": 1, "high": 2, "low": 1, "close": 2},
                    {"datetime": "2026-06-04 09:18", "open": 2, "high": 3, "low": 2, "close": 3},
                ],
                "decisions": [{"decision": "WAIT", "reason": "Replay only"}, {"decision": "WAIT"}],
            })

            self.assertEqual(result["rows"], 2)
            self.assertEqual(result["orders_placed"], 0)
            self.assertTrue(result["analysis_only"])
            self.assertTrue(os.path.exists(result["saved_report"]))

    def test_telegram_emergency_requires_confirmation_and_whitelist(self):
        telegram = TelegramSafety()
        settings = {"telegram_allowed_user_ids": ["U1"], "telegram_command_cooldown_seconds": 0, "telegram_duplicate_window_seconds": 0}
        unconfirmed = telegram.validate("emergency_exit", "U1", settings, confirmed=False, now_epoch=100, position_snapshot=[])
        confirmed = telegram.validate("emergency_exit", "U1", settings, confirmed=True, now_epoch=101, position_snapshot=[])
        blocked_user = telegram.validate("status", "BAD", settings, now_epoch=102)

        self.assertFalse(unconfirmed["allowed"])
        self.assertTrue(confirmed["allowed"])
        self.assertFalse(blocked_user["allowed"])

    def test_telegram_cooldown_duplicate_and_logging(self):
        telegram = TelegramSafety()
        settings = {"telegram_allowed_user_ids": ["U1"], "telegram_command_cooldown_seconds": 5, "telegram_duplicate_window_seconds": 30}

        first = telegram.validate("status", "U1", settings, command_id="C1", now_epoch=100)
        second = telegram.validate("status", "U1", settings, command_id="C2", now_epoch=102)
        duplicate = telegram.validate("status", "U1", {**settings, "telegram_command_cooldown_seconds": 0}, command_id="C1", now_epoch=110)

        self.assertTrue(first["allowed"])
        self.assertFalse(second["allowed"])
        self.assertIn("cooldown", second["blockers"][0])
        self.assertFalse(duplicate["allowed"])
        self.assertIn("Duplicate", duplicate["blockers"][0])
        self.assertEqual(len(duplicate["command_log"]), 3)


if __name__ == "__main__":
    unittest.main()
