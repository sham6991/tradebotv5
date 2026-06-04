import unittest

from options_auto.intelligence.exit_manager import ExitManager
from options_auto.intelligence.position_manager import PositionManager
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
            "partial_exit_enabled": True,
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


class OptionsAutoExitManagerTests(unittest.TestCase):
    def test_duplicate_stoploss_orders_require_manual_attention(self):
        trade = {"tradingsymbol": "NIFTY26JUN22500CE", "entry_price": 100, "stoploss": 80, "target": 130, "quantity": 50}
        broker_orders = [
            {"order_id": "SL1", "tradingsymbol": "NIFTY26JUN22500CE", "transaction_type": "SELL", "order_type": "SL", "status": "OPEN"},
            {"order_id": "SL2", "tradingsymbol": "NIFTY26JUN22500CE", "transaction_type": "SELL", "order_type": "SL", "status": "OPEN"},
        ]

        decision = ExitManager().evaluate(trade, {"ltp": 110, "broker_orders": broker_orders}, {"mode": "REAL"})

        self.assertEqual(decision["action"], "MANUAL_ATTENTION")
        self.assertIn("Duplicate live stoploss orders detected.", decision["blockers"])

    def test_real_stoploss_modify_requires_existing_broker_order_id(self):
        trade = {"tradingsymbol": "NIFTY26JUN22500CE", "entry_price": 100, "stoploss": 80, "target": 140, "quantity": 50}

        decision = ExitManager().evaluate(trade, {"ltp": 125}, {"mode": "REAL", "break_even_sl_enabled": True, "trailing_stop_enabled": True})

        self.assertEqual(decision["action"], "MANUAL_ATTENTION")
        self.assertIn("broker SL order id is missing", "; ".join(decision["blockers"]))

    def test_trailing_stop_respects_modification_throttle(self):
        trade = {
            "tradingsymbol": "NIFTY26JUN22500CE",
            "entry_price": 100,
            "stoploss": 80,
            "target": 140,
            "quantity": 50,
            "stoploss_order_id": "SL1",
            "last_stoploss_modified_epoch": 100,
        }

        decision = ExitManager().evaluate(
            trade,
            {"ltp": 125, "now_epoch": 105},
            {"mode": "REAL", "break_even_sl_enabled": True, "trailing_stop_enabled": True, "sl_modify_throttle_seconds": 10},
        )

        self.assertFalse(decision["stoploss_change"])
        self.assertIn("Stoploss modification throttle is active.", decision["warnings"])

    def test_theta_and_max_holding_exits_are_prioritized(self):
        trade = {"tradingsymbol": "NIFTY26JUN22500CE", "entry_price": 100, "stoploss": 80, "target": 150, "quantity": 50, "minutes_in_trade": 60}

        theta = ExitManager().evaluate(trade, {"ltp": 105, "theta_risk_score": 90}, {"theta_exit_risk_score": 80, "time_exit_enabled": True, "max_holding_minutes": 45})
        time_exit = ExitManager().evaluate({**trade, "minutes_in_trade": 60}, {"ltp": 105}, {"time_exit_enabled": True, "max_holding_minutes": 45})

        self.assertEqual(theta["action"], "THETA_EXIT")
        self.assertEqual(time_exit["action"], "TIME_EXIT")

    def test_partial_exit_quantity_respects_lot_size(self):
        trade = {"tradingsymbol": "NIFTY26JUN22500CE", "entry_price": 100, "stoploss": 80, "target": 150, "quantity": 100, "lot_size": 50}

        decision = ExitManager().evaluate(trade, {"ltp": 122}, {"partial_exit_enabled": True})

        self.assertEqual(decision["action"], "PARTIAL_EXIT")
        self.assertEqual(decision["partial_quantity"], 50)

    def test_position_manager_uses_actual_fill_price_for_protection(self):
        plan = {"tradingsymbol": "NIFTY26JUN22500CE", "entry_price": 100, "stoploss": 80, "target": 130, "quantity": 50}
        order = {"order_id": "E1", "tradingsymbol": "NIFTY26JUN22500CE", "average_price": 103, "filled_quantity": 50}

        trade = PositionManager().open_from_fill(plan, order)

        self.assertEqual(trade["entry_price"], 103)
        self.assertEqual(trade["stoploss"], 83)
        self.assertEqual(trade["target"], 133)

    def test_position_manager_blocks_averaging_down_losing_option(self):
        trade = {"quantity": 50, "average_price": 100}

        result = PositionManager().validate_add_quantity(trade, 50, {"ltp": 95})

        self.assertFalse(result["allowed"])
        self.assertIn("Averaging down", result["blockers"][0])

    def test_paper_market_process_applies_theta_exit_without_real_orders(self):
        service = OptionsAutoTerminalService("results")
        service.execute_paper_plan(sample_payload())
        service.process_paper_market({"market": {"ltp": 39.5, "high": 41, "low": 39.5}})

        result = service.process_paper_market({"market": {"ltp": 41, "high": 41, "low": 41, "theta_risk_score": 95}})

        self.assertEqual(result["exit_updates"][0]["decision"]["action"], "THETA_EXIT")
        self.assertEqual(result["session"]["status"], "PAPER_IDLE")
        self.assertEqual(result["paper_account"]["orders"][-1]["transaction_type"], "SELL")

    def test_paper_market_process_can_move_sl_to_breakeven(self):
        service = OptionsAutoTerminalService("results")
        service.execute_paper_plan(sample_payload())
        service.process_paper_market({"market": {"ltp": 39.5, "high": 41, "low": 39.5}})

        result = service.process_paper_market({"market": {"ltp": 49, "high": 49, "low": 49}})

        self.assertEqual(result["exit_updates"][0]["decision"]["action"], "PARTIAL_EXIT")
        self.assertGreaterEqual(result["snapshot"]["active_trades"][0]["stoploss"], 40)


if __name__ == "__main__":
    unittest.main()
