import os
import tempfile
import unittest
from datetime import datetime, timedelta

from intraday.active_trade_manager import (
    ACTION_FULL_EXIT,
    ACTION_MOVE_SL_TO_BREAKEVEN,
    ACTION_NO_ACTION,
    ACTION_PARTIAL_EXIT,
    ACTION_TRAIL_SL,
    ActiveTradeManager,
)
from intraday.database import IntradayDatabase
from intraday.models import IntradaySettings
from intraday.order_lifecycle import IntradayOrderLifecycle


def settings(**overrides):
    payload = {
        "mode": "PAPER",
        "stocks": ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"],
        "confirm_real_mode": True,
        "active_trade_management_enabled": True,
    }
    payload.update(overrides)
    return IntradaySettings.from_payload(payload)


def long_trade(**overrides):
    trade = {
        "trade_id": "T1",
        "session_id": "S1",
        "symbol": "INFY",
        "exchange": "NSE",
        "side": "LONG",
        "quantity": 10,
        "original_quantity": 10,
        "entry_time": (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds"),
        "entry_price": 100.0,
        "initial_stoploss_trigger": 95.0,
        "initial_stoploss_limit": 94.95,
        "stoploss_trigger": 95.0,
        "stoploss_limit": 94.95,
        "initial_target": 110.0,
        "target": 110.0,
        "status": "OPEN",
        "entry_order_id": "ENT1",
        "stoploss_order_id": "SL1",
        "target_order_id": "TGT1",
        "margin_required": 200.0,
        "management": {},
    }
    trade.update(overrides)
    return trade


def snapshot(**overrides):
    row = {
        "symbol": "INFY",
        "ltp": 106.0,
        "ema20": 103.0,
        "ema50": 101.0,
        "vwap": 102.0,
        "rsi": 62.0,
        "relative_volume": 2.0,
        "poc": 102.0,
        "vah": 105.0,
        "val": 98.0,
        "depth_imbalance": 0.2,
        "spread_pct": 0.05,
        "news_score": 2.0,
        "trap_score": 10.0,
        "final_long_score": 82.0,
        "final_short_score": 30.0,
    }
    row.update(overrides)
    return row


class FakeRealBroker:
    def __init__(self):
        self.modified = []
        self.placed = []
        self.real_order_pause_reason = ""

    def get_orders(self):
        return []

    def get_trades(self):
        return []

    def modify_order(self, order_id, payload):
        self.modified.append((order_id, dict(payload)))
        return {"order_id": order_id, "status": "MODIFY_SENT", "payload": dict(payload)}

    def place_order(self, request):
        self.placed.append(request)
        return {"order_id": f"NEW{len(self.placed)}", "status": "PLACED"}


class ActiveTradeManagerTests(unittest.TestCase):
    def test_breakeven_moves_sl_after_one_r_without_widening(self):
        manager = ActiveTradeManager(settings(active_trailing_sl_enabled=False))
        decision = manager.evaluate(long_trade(), snapshot=snapshot(ltp=106.0), market_row={"candles": [{"open": 105, "close": 106}]})
        self.assertEqual(decision.action, ACTION_MOVE_SL_TO_BREAKEVEN)
        self.assertGreater(decision.new_stoploss, 100.0)

    def test_never_widens_existing_stoploss(self):
        manager = ActiveTradeManager(settings(active_trailing_sl_enabled=True, breakeven_sl_enabled=True))
        trade = long_trade(stoploss_trigger=103.0, stoploss_limit=102.95)
        decision = manager.evaluate(trade, snapshot=snapshot(ltp=104.0), market_row={"candles": [{"open": 103, "close": 104}]})
        self.assertNotEqual(decision.action, ACTION_MOVE_SL_TO_BREAKEVEN)
        self.assertTrue(decision.new_stoploss is None or decision.new_stoploss > trade["stoploss_trigger"])

    def test_trailing_sl_uses_existing_trade_context(self):
        manager = ActiveTradeManager(settings(breakeven_sl_enabled=False, active_trailing_sl_enabled=True, trailing_method="EMA20"))
        trade = long_trade(stoploss_trigger=100.5, stoploss_limit=100.45)
        decision = manager.evaluate(trade, snapshot=snapshot(ltp=108.0, ema20=104.0), market_row={"candles": [{"open": 107, "close": 108, "high": 109, "low": 106}]})
        self.assertEqual(decision.action, ACTION_TRAIL_SL)
        self.assertGreater(decision.new_stoploss, trade["stoploss_trigger"])

    def test_partial_exit_recommends_reduced_quantity_once(self):
        manager = ActiveTradeManager(settings(partial_exit_enabled=True, active_trailing_sl_enabled=False, breakeven_sl_enabled=False))
        decision = manager.evaluate(long_trade(), snapshot=snapshot(ltp=106.0), market_row={"candles": [{"open": 105, "close": 106}]})
        self.assertEqual(decision.action, ACTION_PARTIAL_EXIT)
        self.assertEqual(decision.partial_quantity, 5)

    def test_weak_trade_health_triggers_full_exit(self):
        manager = ActiveTradeManager(settings(active_trailing_sl_enabled=False, breakeven_sl_enabled=False, early_exit_health_threshold=45))
        weak = snapshot(
            ltp=98.0,
            ema20=101.0,
            ema50=103.0,
            vwap=102.0,
            rsi=38.0,
            relative_volume=0.4,
            depth_imbalance=-0.5,
            news_score=-8.0,
            trap_score=80.0,
            final_short_score=88.0,
        )
        decision = manager.evaluate(long_trade(), snapshot=weak, market_row={"candles": [{"open": 100, "close": 98, "high": 101, "low": 97}]})
        self.assertEqual(decision.action, ACTION_FULL_EXIT)

    def test_real_sl_management_modifies_existing_order_without_duplicate_sl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            broker = FakeRealBroker()
            db = IntradayDatabase(os.path.join(temp_dir, "intraday.sqlite"))
            lifecycle = IntradayOrderLifecycle(
                broker,
                db,
                settings(mode="REAL", breakeven_sl_enabled=False, active_trailing_sl_enabled=True, trailing_method="EMA20"),
                "S1",
            )
            trade = long_trade(stoploss_trigger=100.0, stoploss_limit=99.95, stoploss_broker_order_id="SLBROKER1")
            lifecycle.active_trade = trade
            lifecycle.active_trades[trade["trade_id"]] = trade
            lifecycle.order_history.append({
                "session_id": "S1",
                "broker_order_id": "SLBROKER1",
                "local_order_id": "SL1",
                "mode": "REAL",
                "symbol": "INFY",
                "exchange": "NSE",
                "side": "LONG",
                "transaction_type": "SELL",
                "order_type": "SL",
                "product": "MIS",
                "quantity": 10,
                "price": 99.95,
                "trigger_price": 100.0,
                "status": "PLACED",
                "status_message": "",
                "broker_response": {},
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "role": "STOPLOSS",
            })
            lifecycle.process_market_data(
                {"INFY": {"candles": [{"open": 107, "high": 109, "low": 106, "close": 108}]}},
                snapshots=[snapshot(ltp=108.0, ema20=104.0)],
            )
            self.assertEqual(len(broker.placed), 0)
            self.assertEqual(len(broker.modified), 1)
            self.assertEqual(broker.modified[0][0], "SLBROKER1")


if __name__ == "__main__":
    unittest.main()
