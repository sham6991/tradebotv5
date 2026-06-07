import tempfile
import time
import unittest

from options_auto.terminal_service import OptionsAutoTerminalService
from tests.test_options_auto_auto_spot import index_rows


def fixed_options_payload():
    return {
        "mode": "BACKTEST",
        "settings": {
            "mode": "BACKTEST",
            "underlying": "NIFTY",
            "buy_score_threshold": 70,
            "max_capital_per_trade_pct": 60,
            "max_risk_per_trade_pct": 5,
            "paper_starting_balance": 20000,
            "runtime_state_persistence_enabled": True,
        },
        "index_history": index_rows(),
        "instruments": [{
            "name": "NIFTY",
            "tradingsymbol": "NIFTY26JUN22500CE",
            "instrument_token": "1",
            "instrument_type": "CE",
            "strike": 22500,
            "expiry": "2026-06-05",
            "lot_size": 50,
            "tick_size": 0.05,
        }],
        "quotes": {"1": {
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
        "market_cue": {
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
        "risk_state": {},
        "timestamp": "2026-06-04 12:00:00",
    }


def decision_fields(result):
    selected = result.get("selected_contract") or {}
    plan = result.get("trade_plan") or {}
    return {
        "allowed": result.get("allowed"),
        "blockers": result.get("blockers") or [],
        "selected_symbol": selected.get("tradingsymbol"),
        "option_type": selected.get("instrument_type"),
        "strike": selected.get("strike"),
        "expiry": str(selected.get("expiry") or ""),
        "entry_price": plan.get("entry_price"),
        "stoploss": plan.get("stoploss"),
        "target": plan.get("target"),
        "quantity": plan.get("quantity"),
        "risk_reward": plan.get("risk_reward"),
        "mode": result.get("mode"),
    }


class OptionsAutoPerformanceNoLogicChangeTests(unittest.TestCase):
    def test_decision_fields_unchanged_for_fixed_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            payload = fixed_options_payload()

            first = service.evaluate(payload)
            second = service.evaluate(payload)

            self.assertEqual(decision_fields(first), decision_fields(second))
            self.assertEqual(decision_fields(first)["selected_symbol"], "NIFTY26JUN22500CE")

    def test_status_keeps_existing_keys_and_adds_latency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)

            status = service.status()

            for key in ("settings", "session", "paper_account", "live_scan", "performance", "api_budget"):
                self.assertIn(key, status)
            self.assertIn("latency", status)
            self.assertIn("options_auto.status_full", status["latency"])

    def test_ui_summary_is_lightweight_and_compatible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)

            summary = service.ui_summary_snapshot()

            self.assertIn("settings", summary)
            self.assertIn("session", summary)
            self.assertIn("latency", summary)
            self.assertNotIn("logs", summary)
            self.assertNotIn("shadow_report", summary)

    def test_noncritical_runtime_persistence_is_batched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            service.settings["options_auto_runtime_persist_min_interval_seconds"] = 30.0

            service._persist_runtime_state_locked("live_scan_cycle")
            service._persist_runtime_state_locked("live_scan_cycle")

            self.assertEqual(service._runtime_state_skipped_count, 1)

    def test_critical_runtime_persistence_is_immediate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            service.settings["options_auto_runtime_persist_min_interval_seconds"] = 30.0

            service._persist_runtime_state_locked("live_scan_cycle")
            first_epoch = service._runtime_state_last_save_epoch
            time.sleep(0.01)
            service._persist_runtime_state_locked("kill_switch")

            self.assertGreater(service._runtime_state_last_save_epoch, first_epoch)
            self.assertEqual(service._runtime_state_skipped_count, 0)


if __name__ == "__main__":
    unittest.main()
