import tempfile
import unittest
from datetime import datetime, timedelta

from intraday.database import IntradayDatabase
from intraday.execution_safeguards import (
    matching_broker_order,
    normalize_order_request_prices,
    real_execution_blockers,
    round_price_to_tick,
    validate_stoploss_limit_relationship,
)
from intraday.models import IntradaySettings, Signal
from intraday.order_lifecycle import IntradayOrderLifecycle
from intraday.order_request import entry_order, stoploss_order
from intraday.zerodha_broker import ZerodhaBroker


def real_settings():
    return IntradaySettings.from_payload({
        "mode": "REAL",
        "confirm_real_mode": True,
        "stocks": ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"],
        "minimum_entry_score": 1,
        "minimum_risk_reward": 1.1,
    })


def signal(**overrides):
    data = {
        "session_id": "SESSION1234567890",
        "symbol": "INFY",
        "exchange": "NSE",
        "side": "LONG",
        "setup_name": "Long VWAP reclaim",
        "score": 90,
        "score_breakdown": {},
        "entry_price": 100.03,
        "stoploss": 99.91,
        "target": 100.41,
        "risk_reward": 2,
        "confidence": 90,
        "explanation": "test",
    }
    data.update(overrides)
    return Signal(**data)


class IntradayExecutionSafeguardsTests(unittest.TestCase):
    def test_prices_round_to_tick_size(self):
        self.assertEqual(round_price_to_tick(100.03, 0.05), 100.05)
        self.assertEqual(round_price_to_tick(100.02, 0.05), 100.0)

    def test_sl_limit_relationship_is_normalized_and_validated(self):
        sell_stop = stoploss_order("INFY", "LONG", 10, trigger_price=100.03, limit_price=100.03)
        sell_stop = normalize_order_request_prices(sell_stop, 0.05)
        self.assertEqual(sell_stop.trigger_price, 100.05)
        self.assertEqual(sell_stop.price, 100.0)
        self.assertEqual(validate_stoploss_limit_relationship(sell_stop, 0.05), [])

        buy_stop = stoploss_order("INFY", "SHORT", 10, trigger_price=100.03, limit_price=100.03)
        buy_stop = normalize_order_request_prices(buy_stop, 0.05)
        self.assertEqual(buy_stop.trigger_price, 100.05)
        self.assertEqual(buy_stop.price, 100.1)
        self.assertEqual(validate_stoploss_limit_relationship(buy_stop, 0.05), [])

    def test_matching_broker_order_prevents_duplicate_send_by_tag_and_payload(self):
        request = entry_order("INFY", "LONG", 10, 100.05, session_id="SESSION1234567890")
        params = request.to_kite_params()
        orders = [
            {**params, "order_id": "OLD", "status": "CANCELLED"},
            {**params, "order_id": "LIVE", "status": "OPEN"},
        ]
        match = matching_broker_order(request, orders, 0.05)
        self.assertEqual(match["order_id"], "LIVE")

    def test_real_execution_blocks_stale_or_non_live_data(self):
        settings = real_settings()
        now = datetime(2026, 6, 3, 10, 0, 0)
        blockers = real_execution_blockers(
            signal(),
            {"source": "provided", "last_candle_time": now.isoformat()},
            settings,
            now=now,
        )
        self.assertTrue(any("simulated/provided data" in blocker for blocker in blockers))

        good_row = {
            "source": "real_zerodha_live",
            "ltp": 100,
            "last_candle_time": now.isoformat(),
            "last_tick_time": now.isoformat(),
            "depth_source": "zerodha_quote",
            "depth": {"buy": [{"price": 99.95, "quantity": 1000}], "sell": [{"price": 100.05, "quantity": 1000}]},
            "lower_circuit_limit": 80,
            "upper_circuit_limit": 120,
            "ohlc": {"open": 100},
        }
        self.assertEqual(real_execution_blockers(signal(), good_row, settings, now=now), [])

        stale_row = {**good_row, "last_tick_time": (now - timedelta(seconds=30)).isoformat()}
        self.assertTrue(any("tick/depth data is stale" in blocker for blocker in real_execution_blockers(signal(), stale_row, settings, now=now)))

    def test_real_execution_blocks_circuit_and_abnormal_move_risk(self):
        settings = real_settings()
        now = datetime(2026, 6, 3, 10, 0, 0)
        row = {
            "source": "real_zerodha_live",
            "ltp": 110,
            "last_candle_time": now.isoformat(),
            "last_tick_time": now.isoformat(),
            "depth_source": "zerodha_quote",
            "depth": {"buy": [{"price": 109.95, "quantity": 1000}], "sell": [{"price": 110.05, "quantity": 1000}]},
            "lower_circuit_limit": 80,
            "upper_circuit_limit": 100.5,
            "ohlc": {"open": 100},
        }
        blockers = real_execution_blockers(signal(entry_price=100.4, stoploss=99.5, target=100.45), row, settings, now=now)
        self.assertTrue(any("circuit" in blocker for blocker in blockers))
        self.assertTrue(any("moving abnormally" in blocker for blocker in blockers))

    def test_zerodha_broker_api_failure_pauses_real_orders(self):
        class BadKite:
            def quote(self, _symbols):
                raise RuntimeError("quote outage")

        class BadClient:
            kite = BadKite()

            def available_margin(self):
                return 100000

            def instruments(self, _exchange=None):
                return []

            def orders(self):
                return []

        broker = ZerodhaBroker(BadClient())
        with self.assertRaises(RuntimeError):
            broker.get_quote(["NSE:INFY"])
        self.assertTrue(broker.api_health_blockers())
        self.assertIn("quote failure", broker.api_health_blockers()[0])

    def test_lifecycle_reuses_existing_broker_order_without_duplicate_place(self):
        class FakeBroker:
            def __init__(self):
                self.place_calls = 0
                self.existing = None

            def api_health_blockers(self):
                return []

            def get_funds(self):
                return {"available": 100000}

            def calculate_margin(self, request):
                return {"required": request.quantity * 100, "available": 100000, "ok": True}

            def find_matching_order(self, request, _tick_size):
                params = request.to_kite_params()
                return {**params, "order_id": "EXISTING123", "status": "OPEN"}

            def place_order(self, _request):
                self.place_calls += 1
                return {"order_id": "NEW", "status": "PLACED"}

        with tempfile.TemporaryDirectory() as temp_dir:
            broker = FakeBroker()
            lifecycle = IntradayOrderLifecycle(
                broker,
                IntradayDatabase(f"{temp_dir}/intraday.sqlite"),
                real_settings(),
                "SESSION1234567890",
                instrument_rows={"NSE:INFY": {"tick_size": 0.05}},
            )
            result = lifecycle.submit_entry(signal(), quantity=10)
            self.assertTrue(result["ok"])
            self.assertEqual(result["order"]["broker_order_id"], "EXISTING123")
            self.assertEqual(broker.place_calls, 0)

    def test_real_active_manager_exit_waits_for_broker_exit_fill(self):
        class FakeBroker:
            def __init__(self):
                self.cancelled = []
                self.orders = {
                    "TGT1": {"order_id": "TGT1", "status": "OPEN"},
                    "SL1": {"order_id": "SL1", "status": "OPEN"},
                }

            def api_health_blockers(self):
                return []

            def place_emergency_order(self, request):
                self.orders["EXIT1"] = {
                    "order_id": "EXIT1",
                    "status": "OPEN",
                    "quantity": request.quantity,
                    "average_price": 101.0,
                }
                return {"order_id": "EXIT1", "status": "OPEN"}

            def cancel_order(self, order_id):
                self.cancelled.append(order_id)
                self.orders[order_id]["status"] = "CANCELLED"
                return {"order_id": order_id, "status": "CANCELLED"}

            def get_orders(self):
                return list(self.orders.values())

            def get_trades(self):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            broker = FakeBroker()
            lifecycle = IntradayOrderLifecycle(
                broker,
                IntradayDatabase(f"{temp_dir}/intraday.sqlite"),
                real_settings(),
                "SESSION1234567890",
                instrument_rows={"NSE:INFY": {"tick_size": 0.05}},
            )
            trade = {
                "trade_id": "TRADE1",
                "symbol": "INFY",
                "exchange": "NSE",
                "side": "LONG",
                "quantity": 10,
                "entry_time": "2026-06-03T10:00:00",
                "entry_price": 100.0,
                "stoploss_trigger": 99.0,
                "stoploss_limit": 98.95,
                "target": 102.0,
                "stoploss_order_id": "SLLOCAL",
                "stoploss_broker_order_id": "SL1",
                "target_order_id": "TGTLOCAL",
                "target_broker_order_id": "TGT1",
                "margin_required": 1000.0,
                "status": "OPEN",
                "management": {},
            }
            lifecycle.active_trades[trade["trade_id"]] = trade
            lifecycle.active_trade = trade
            lifecycle.order_history.extend([
                {"local_order_id": "SLLOCAL", "broker_order_id": "SL1", "role": "STOPLOSS", "status": "OPEN", "quantity": 10},
                {"local_order_id": "TGTLOCAL", "broker_order_id": "TGT1", "role": "TARGET", "status": "OPEN", "quantity": 10},
            ])

            lifecycle._apply_management_decision(trade, {
                "action": "FULL_EXIT",
                "health_score": 20,
                "r_multiple": 0.2,
                "exit_price": 101.0,
                "reason": "test",
            }, datetime(2026, 6, 3, 10, 1, 0))

            self.assertEqual(trade["status"], "OPEN")
            self.assertTrue(trade["management"]["real_exit_pending"])
            self.assertEqual(broker.cancelled, ["TGT1"])
            exit_order = [row for row in lifecycle.order_history if row.get("role") == "ACTIVE_EXIT"][0]
            self.assertEqual(exit_order["broker_order_id"], "EXIT1")

            broker.orders["EXIT1"]["status"] = "COMPLETE"
            lifecycle.process_market_data({}, now=datetime(2026, 6, 3, 10, 1, 5), snapshots=[])
            self.assertEqual(trade["status"], "CLOSED")
            self.assertEqual(trade["exit_reason"], "ACTIVE_MANAGER_EXIT")
            self.assertIn("SL1", broker.cancelled)

    def test_failed_real_stoploss_placement_requests_emergency_exit(self):
        class FakeBroker:
            def __init__(self):
                self.calls = []

            def place_order(self, request):
                self.calls.append(("place_order", request.order_type))
                raise RuntimeError("SL rejected")

            def place_emergency_order(self, request):
                self.calls.append(("emergency", request.order_type, request.transaction_type, request.quantity))
                return {"order_id": "EMG1", "status": "OPEN"}

            def cancel_order(self, _order_id):
                return {"status": "CANCELLED"}

        with tempfile.TemporaryDirectory() as temp_dir:
            lifecycle = IntradayOrderLifecycle(
                FakeBroker(),
                IntradayDatabase(f"{temp_dir}/intraday.sqlite"),
                real_settings(),
                "SESSION1234567890",
                instrument_rows={"NSE:INFY": {"tick_size": 0.05}},
            )
            entry = {
                "local_order_id": "ENTRY1",
                "symbol": "INFY",
                "exchange": "NSE",
                "side": "LONG",
                "quantity": 10,
                "signal_stoploss": 99.0,
                "signal_target": 102.0,
                "margin_required": 1000.0,
                "setup_name": "test",
                "score": 90,
            }
            lifecycle._open_trade(entry, 100.0, {}, datetime(2026, 6, 3, 10, 0, 0), filled_quantity=10)

            emergency_orders = [row for row in lifecycle.order_history if row.get("role") == "EMERGENCY_EXIT"]
            self.assertEqual(len(emergency_orders), 1)
            self.assertEqual(emergency_orders[0]["broker_order_id"], "EMG1")
            self.assertTrue(lifecycle.active_trade["management"]["real_exit_pending"])


if __name__ == "__main__":
    unittest.main()
