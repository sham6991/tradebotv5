import os
import tempfile
import unittest
from datetime import datetime, timedelta

from intraday.database import IntradayDatabase
from intraday.models import IntradaySettings
from intraday.order_lifecycle import IntradayOrderLifecycle
from intraday.order_request import emergency_exit_order


SYMBOLS = ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"]


def settings(**overrides):
    payload = {
        "mode": "PAPER",
        "stocks": SYMBOLS,
        "allow_simulated_fallback": True,
        "require_live_data_for_paper": False,
        "minimum_risk_reward": 1.5,
        "limit_order_timeout_seconds": 30,
    }
    payload.update(overrides)
    return IntradaySettings.from_payload(payload)


class DummyBroker:
    def __init__(self):
        self.cancelled = []

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"order_id": order_id, "status": "CANCELLED"}


class IntradayOrderLifecyclePolicyTests(unittest.TestCase):
    def lifecycle(self, temp_dir, **setting_overrides):
        db = IntradayDatabase(os.path.join(temp_dir, "intraday.sqlite"))
        return IntradayOrderLifecycle(
            DummyBroker(),
            db,
            settings(**setting_overrides),
            "S1",
            instrument_rows={"NSE:INFY": {"tick_size": 0.05}},
        )

    def pending_entry(self, created_at):
        return {
            "local_order_id": "ENT1",
            "broker_order_id": "BROKER1",
            "role": "ENTRY",
            "status": "PENDING",
            "symbol": "INFY",
            "exchange": "NSE",
            "side": "LONG",
            "transaction_type": "BUY",
            "price": 100.0,
            "quantity": 10,
            "signal_stoploss": 98.0,
            "signal_target": 103.0,
            "created_at": created_at.isoformat(timespec="seconds"),
            "updated_at": created_at.isoformat(timespec="seconds"),
        }

    def test_limit_order_timeout_seconds_is_respected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lifecycle = self.lifecycle(temp_dir, limit_order_timeout_seconds=30)
            created = datetime(2026, 6, 5, 10, 0, 0)
            order = self.pending_entry(created)
            lifecycle.order_history.append(order)

            market = {"INFY": {"candles": [{"high": 120, "low": 110, "close": 115}]}}
            lifecycle._process_pending_entries(market, created + timedelta(seconds=29))
            self.assertEqual(order["status"], "PENDING")

            lifecycle._process_pending_entries(market, created + timedelta(seconds=30))
            self.assertEqual(order["status"], "CANCELLED")
            self.assertIn("30 seconds", order["status_message"])

    def test_exit_plan_recalculates_from_actual_fill_price(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lifecycle = self.lifecycle(temp_dir, minimum_risk_reward=1.5)

            plan = lifecycle._exit_plan_from_fill(
                {"side": "LONG", "symbol": "INFY", "exchange": "NSE", "price": 100.0, "signal_stoploss": 98.0, "signal_target": 103.0},
                101.0,
                {"average_range_14": 1.2},
            )

            self.assertEqual(plan["source"], "ACTUAL_FILL_RECALCULATED")
            self.assertEqual(plan["stoploss"], 99.0)
            self.assertEqual(plan["target"], 104.0)

    def test_sl_and_target_modification_throttles_work(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lifecycle = self.lifecycle(temp_dir, min_seconds_between_sl_modifications=15, min_seconds_between_target_modifications=15)
            now = datetime(2026, 6, 5, 10, 0, 5)
            trade = {
                "symbol": "INFY",
                "exchange": "NSE",
                "side": "LONG",
                "quantity": 10,
                "stoploss_trigger": 95.0,
                "stoploss_limit": 94.95,
                "target": 110.0,
                "management": {
                    "last_sl_modified_at": "2026-06-05T10:00:00",
                    "last_target_modified_at": "2026-06-05T10:00:00",
                },
            }
            sl_decision = {"details": {}}
            target_decision = {"details": {}}

            self.assertFalse(lifecycle._modify_stoploss(trade, 100.0, sl_decision, now))
            self.assertFalse(lifecycle._modify_target(trade, 115.0, target_decision, now))
            self.assertIn("throttle", sl_decision["details"])
            self.assertIn("throttle", target_decision["details"])

    def test_real_partial_exit_is_clearly_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lifecycle = self.lifecycle(temp_dir, mode="REAL", confirm_real_mode=True)
            trade = {
                "trade_id": "T1",
                "symbol": "INFY",
                "exchange": "NSE",
                "side": "LONG",
                "quantity": 10,
                "entry_price": 100.0,
                "margin_required": 1000.0,
                "management": {},
            }

            lifecycle._partial_exit(trade, 5, 105.0, {"action": "PARTIAL_EXIT", "details": {}}, datetime(2026, 6, 5, 10, 0, 0))
            events = lifecycle.database.table_rows("intraday_trade_management_events", "S1")

            self.assertEqual(events[-1]["status"], "REAL_PARTIAL_EXIT_BLOCKED")
            self.assertIn("not implemented", events[-1]["reason"])

    def test_emergency_default_uses_aggressive_limit(self):
        request = emergency_exit_order("INFY", 10, settings=settings(), ltp=100.0, session_id="S1")

        self.assertEqual(request.order_type, "LIMIT")
        self.assertEqual(request.transaction_type, "SELL")
        self.assertLess(request.price, 100.0)


if __name__ == "__main__":
    unittest.main()
