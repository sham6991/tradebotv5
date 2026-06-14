import unittest
from datetime import datetime, timedelta

from options_auto.execution.execution_safety import DataQualityEngine
from options_auto.execution.real_execution_controller import RealExecutionController
from options_auto.execution.real_order_checks import build_real_entry_order_request
from options_auto.execution.real_order_lifecycle import RealOrderLifecycleEngine, UNPROTECTED_POSITION
from options_auto.intelligence.low_latency_decision_engine import LowLatencyDecisionEngine


class ProtectionAdapter:
    def place_target_sell_limit(self, tradingsymbol, quantity, price, exchange, product, tag):
        return {"ok": True, "value": "TARGET1"}

    def place_stoploss_sell_sl_limit(self, tradingsymbol, quantity, trigger_price, price, exchange, product, tag):
        return {"ok": True, "value": "SL1"}


def ready_plan():
    return {
        "status": "READY",
        "side": "CE",
        "last_refreshed_epoch": 1000.0,
        "entry_plan": {"entry_limit": 100.0, "signal_price": 100.0, "tick_size": 0.05},
        "premium_context": {"option_atr14": 10.0},
        "contract": {"tradingsymbol": "NIFTY26JUN23500CE"},
    }


def fast_settings(**overrides):
    settings = {
        "mode": "PAPER",
        "strategy_profile": "AGGRESSIVE",
        "aggressive_uses_simple_ohlcv_entry": True,
        "max_plan_age_seconds_aggressive": 3.0,
        "quote_stale_seconds": 3.0,
        "max_spread_pct": 0.60,
        "slippage_buffer_points": 5.0,
        "max_chase_points": 3.0,
        "max_chase_atr_fraction": 10.0,
    }
    settings.update(overrides)
    return settings


def trade_plan(**overrides):
    plan = {
        "tradingsymbol": "NIFTY26JUN23500CE",
        "exchange": "NFO",
        "product": "NRML",
        "quantity": 65,
        "lot_size": 65,
        "entry_price": 100.0,
        "tick_size": 0.05,
    }
    plan.update(overrides)
    return plan


def selected_contract(**overrides):
    selected = {
        "tradingsymbol": "NIFTY26JUN23500CE",
        "exchange": "NFO",
        "instrument_token": 12345,
        "lot_size": 65,
        "tick_size": 0.05,
    }
    selected.update(overrides)
    return selected


def preflight_with_margin(value=100000):
    return {"evidence": {"checks": {"available_margin": value}}}


class OptionsAutoBrokerRiskHardeningTests(unittest.TestCase):
    def test_unknown_quote_age_blocks_live_data_quality(self):
        result = DataQualityEngine().validate_quote(
            {"ltp": 100.0, "bid": 99.95, "ask": 100.05},
            {"mode": "PAPER", "quote_stale_seconds": 3.0},
        ).to_dict()

        self.assertFalse(result["allowed"])
        self.assertIn("Quote age is unknown.", result["blockers"])

    def test_unknown_quote_age_is_allowed_for_debug_diagnostics(self):
        result = DataQualityEngine().validate_quote(
            {"ltp": 100.0, "bid": 99.95, "ask": 100.05},
            {"mode": "DEBUG", "quote_stale_seconds": 3.0},
        ).to_dict()

        self.assertTrue(result["allowed"], result["blockers"])

    def test_final_validation_blocks_unknown_live_quote_age(self):
        result = LowLatencyDecisionEngine().validate_final_entry(
            ready_plan(),
            {"ltp": 100.0, "bid": 99.95, "ask": 100.05, "premium_return_1": 1.0},
            fast_settings(),
            {"market_cue": {"recommended_side": "CE"}, "regime": {"recommended_side": "CE"}, "governor_allowed": True},
            now_epoch=1001.0,
        )

        self.assertFalse(result["allowed"])
        self.assertIn("Quote age is unknown.", result["blockers"])

    def test_real_order_check_blocks_freeze_quantity_breach(self):
        _request, blockers = build_real_entry_order_request(
            selected_contract(freeze_quantity=65),
            trade_plan(quantity=130, lot_size=65),
            {"order_product": "NRML"},
            preflight_with_margin(100000),
        )

        self.assertIn("Real order quantity exceeds broker freeze quantity (65).", blockers)

    def test_real_order_check_blocks_missing_margin_evidence(self):
        _request, blockers = build_real_entry_order_request(
            selected_contract(),
            trade_plan(),
            {"order_product": "NRML"},
            {"evidence": {"checks": {}}},
        )

        self.assertIn("Available margin is unavailable for final real-order check.", blockers)

    def test_real_protection_sla_marks_position_unprotected_when_stoploss_not_confirmed(self):
        controller = RealExecutionController()
        engine = RealOrderLifecycleEngine(controller)
        engine.submit_entry(
            {"order_id": "ENTRY1", "tradingsymbol": "NIFTY26JUN23500CE", "quantity": 65, "price": 100.0, "status": "OPEN"},
            trade_plan(),
            {},
        )
        filled = engine.poll_entry_status(
            [{"order_id": "ENTRY1", "status": "COMPLETE", "quantity": 65, "filled_quantity": 65, "average_price": 100.0}],
            adapter=ProtectionAdapter(),
        )
        engine.fill["protection_started_at"] = (datetime.now() - timedelta(seconds=10)).isoformat(timespec="seconds")

        snapshot = engine.verify_protection_orders(
            [{**filled["target_order"], "status": "OPEN"}],
            settings={"protection_confirm_sla_seconds": 1},
        )

        self.assertEqual(snapshot["state"], UNPROTECTED_POSITION)
        self.assertTrue(snapshot["protection_sla"]["breached"])
        self.assertIn("Protective stoploss was not broker-confirmed", "; ".join(snapshot["blockers"]))
        self.assertTrue(controller.state.safe_mode)


if __name__ == "__main__":
    unittest.main()
