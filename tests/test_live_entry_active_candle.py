import unittest
from datetime import datetime, timedelta

import pandas as pd

from execution_v2 import LivePaperSession
from engine import attach_datetime_index_map
from strategy import OPTION_FORMULA_COLUMNS
from tests.test_strategy_regression import nifty_frame, settings


def fast_option_frame(option_type, entry_type="market"):
    start = datetime(2026, 5, 12, 9, 15)
    rows = [
        {
            "datetime": start + timedelta(minutes=3 * index),
            "open": 100,
            "high": 110,
            "low": 90,
            "close": 101,
            "volume": 1000,
        }
        for index in range(10)
    ]
    if entry_type == "limit":
        rows.append({
            "datetime": start + timedelta(minutes=30),
            "open": 96,
            "high": 112,
            "low": 94,
            "close": 104,
            "volume": 900,
        })
    else:
        rows.append({
            "datetime": start + timedelta(minutes=30),
            "open": 100,
            "high": 112,
            "low": 99,
            "close": 111,
            "volume": 1500,
        })
    df = pd.DataFrame(rows)
    for column in OPTION_FORMULA_COLUMNS:
        df[column] = 0
    df.attrs["instrument"] = f"NIFTY25000{option_type}"
    df.attrs["tradingsymbol"] = f"NIFTY25000{option_type}"
    df.attrs["option_type"] = option_type
    return attach_datetime_index_map(df)


def signal(option):
    return {
        "option": option,
        "option_index": 0,
        "type": "CE",
        "instrument": "NIFTY25000CE",
        "tradingsymbol": "NIFTY25000CE",
        "entry": 100,
        "entry_order_type": "MARKET",
        "entry_type": "MARKET ENTRY",
        "entry_offset": 0,
        "signal_index": 0,
        "nifty_signal_index": 0,
        "entry_index": 0,
        "target": 120,
        "stoploss": 90,
        "score_row": {"Early Score": 85, "Buy Entry": "BUY"},
    }


class LiveTrailingOrders:
    def __init__(self):
        self.modified = []

    def order_status(self, order_id, fallback="UNKNOWN"):
        return "TRIGGER PENDING"

    def modify_stoploss_trigger(self, order_id, trigger_price, quantity=None, price=None, order_type="SL"):
        self.modified.append((order_id, trigger_price, quantity, price, order_type))
        return {"modified": True, "status": "MODIFIED", "error": ""}

    def order_details(self, order_id, fallback_quantity=0, fallback_price=0):
        return {
            "order_id": order_id,
            "status": "TRIGGER PENDING",
            "quantity": fallback_quantity,
            "filled_quantity": 0,
            "pending_quantity": fallback_quantity,
            "cancelled_quantity": 0,
            "average_price": fallback_price,
            "is_partial": False,
            "raw": {},
        }


class LiveProtectiveOrders:
    def __init__(self):
        self.placed = []
        self.modified = []
        self.next_id = 1

    def place_order(self, side, tradingsymbol, quantity, product="NRML", order_type="MARKET", price=None, trigger_price=None):
        order_id = f"O{self.next_id}"
        self.next_id += 1
        self.placed.append({
            "side": side,
            "tradingsymbol": tradingsymbol,
            "quantity": quantity,
            "product": product,
            "order_type": order_type,
            "price": price,
            "trigger_price": trigger_price,
            "order_id": order_id,
        })
        return {
            "status": f"{side} {order_type} ORDER PLACED",
            "order_id": order_id,
            "log_status": f"{order_type} PLACED",
            "log_data": {"quantity": quantity, "price": price, "trigger_price": trigger_price},
            "error": "",
        }

    def order_status(self, order_id, fallback="UNKNOWN"):
        return "OPEN"

    def order_details(self, order_id, fallback_quantity=0, fallback_price=0):
        return {
            "order_id": order_id,
            "status": self.order_status(order_id),
            "quantity": fallback_quantity,
            "filled_quantity": 0,
            "pending_quantity": fallback_quantity,
            "cancelled_quantity": 0,
            "average_price": fallback_price,
            "is_partial": False,
            "raw": {},
        }

    def modify_limit_price(self, order_id, price, quantity=None):
        self.modified.append(("LIMIT", order_id, price, quantity))
        return {"modified": True, "status": "MODIFIED", "error": "", "price": price}

    def modify_stoploss_trigger(self, order_id, trigger_price, quantity=None, price=None, order_type="SL"):
        self.modified.append(("SL", order_id, trigger_price, quantity, price, order_type))
        return {"modified": True, "status": "MODIFIED", "error": "", "trigger_price": trigger_price, "price": price}

    def average_price(self, order_id, fallback):
        return fallback

    def filled_quantity(self, order_id, fallback):
        return fallback


class LivePendingCancelOrders:
    def __init__(self, cancel_result=None, details=None):
        self.cancelled = []
        self.cancel_result = cancel_result
        self.details = details or {
            "E1": {
                "order_id": "E1",
                "status": "OPEN",
                "quantity": 75,
                "filled_quantity": 0,
                "pending_quantity": 75,
                "cancelled_quantity": 0,
                "average_price": 100,
                "is_partial": False,
                "raw": {},
            }
        }

    def order_status(self, order_id, fallback="UNKNOWN"):
        return self.details.get(order_id, {}).get("status", fallback)

    def order_details(self, order_id, fallback_quantity=0, fallback_price=0):
        details = dict(self.details.get(order_id, {}))
        details.setdefault("order_id", order_id)
        details.setdefault("status", self.order_status(order_id, fallback="OPEN"))
        details.setdefault("quantity", fallback_quantity)
        details.setdefault("filled_quantity", 0)
        details.setdefault("pending_quantity", fallback_quantity)
        details.setdefault("cancelled_quantity", 0)
        details.setdefault("average_price", fallback_price)
        details.setdefault("is_partial", False)
        details.setdefault("raw", {})
        return details

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        if self.cancel_result is not None:
            result = dict(self.cancel_result)
            if result.get("status"):
                self.details[order_id]["status"] = result["status"]
            return result
        self.details[order_id]["status"] = "CANCELLED"
        self.details[order_id]["pending_quantity"] = 0
        self.details[order_id]["cancelled_quantity"] = self.details[order_id]["quantity"]
        return {"cancelled": True, "error": "", "status": "CANCELLED"}


class LiveEntryActiveCandleTests(unittest.TestCase):
    def test_live_paper_creates_pending_limit_from_signal_candle_close(self):
        test_settings = settings(entry_offset=0, max_trades=1, lot_size=1)
        nifty = nifty_frame("bearish", count=11)
        ce = fast_option_frame("CE")
        pe = fast_option_frame("PE", entry_type="limit")
        session = LivePaperSession(
            nifty,
            [ce, pe],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            test_settings,
            save_path=None,
            mode="PAPER",
        )

        active_time = datetime(2026, 5, 12, 9, 21)
        session.candle_builder.add_tick("NIFTY", 101, timestamp=active_time, volume=100)
        session.candle_builder.add_tick("OPTION_0", 100, timestamp=active_time, volume=100)
        session.candle_builder.add_tick("OPTION_1", 104, timestamp=active_time, volume=100)

        session._try_entry(10)

        self.assertIsNone(session.open_position)
        self.assertIsNotNone(session.pending_entry)
        self.assertEqual(session.pending_entry["signal"]["type"], "PE")
        self.assertEqual(session.pending_entry["limit_price"], 102.0)

    def test_live_paper_limit_entry_cancels_after_timeout_without_market_conversion(self):
        test_settings = settings(
            entry_offset=-2,
            max_trades=1,
            lot_size=1,
            pending_entry_timeout_seconds=30,
        )
        updates = []
        nifty = nifty_frame("bearish", count=11)
        ce = fast_option_frame("CE")
        pe = fast_option_frame("PE", entry_type="limit")
        session = LivePaperSession(
            nifty,
            [ce, pe],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            test_settings,
            save_path=None,
            mode="PAPER",
            on_order_update=updates.append,
        )

        session._try_entry(10)

        self.assertIsNotNone(session.pending_entry)
        self.assertIsNone(session.open_position)
        self.assertEqual(session.pending_entry["limit_price"], 102)
        self.assertEqual(len(session.active_orders), 1)

        session._check_pending_entry(10, force_timeout=True)

        self.assertIsNone(session.pending_entry)
        self.assertIsNone(session.open_position)
        self.assertEqual(session.active_orders, {})
        self.assertEqual(session.order_history[-1]["Action"], "CANCEL ORDER")
        self.assertEqual(session.order_history[-1]["Order Status"], "CANCELLED")
        self.assertEqual(session.order_history[-1]["Error / Rejection Reason"], "TIME EXHAUSTION CANCELLATION")
        self.assertEqual(updates[-1]["active_orders"], [])
        self.assertFalse(updates[-1]["health"]["pending_entry"])

    def test_live_limit_timeout_removes_confirmed_cancel_from_active_orders(self):
        updates = []
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=11),
            [option, fast_option_frame("PE")],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            settings(max_trades=1, lot_size=1, pending_entry_timeout_seconds=30),
            save_path=None,
            mode="PAPER",
            on_order_update=updates.append,
        )
        fake_orders = LivePendingCancelOrders()
        entry_signal = signal(option)
        entry_signal["entry_order_type"] = "LIMIT"
        session.mode = "LIVE"
        session.zerodha = object()
        session.orders = fake_orders
        session.pending_entry = {
            "signal": entry_signal,
            "option_index": 0,
            "order_id": "E1",
            "quantity": 75,
            "contract_lot_size": 75,
            "limit_price": 100,
            "placed_at": datetime.now() - timedelta(seconds=31),
            "placed_index": 0,
        }
        session._record_pending_entry_order(entry_signal, 0, "OPEN", "E1", 75, 75)

        session._check_pending_entry(0, force_timeout=True)

        self.assertEqual(fake_orders.cancelled, ["E1"])
        self.assertIsNone(session.pending_entry)
        self.assertEqual(session.active_orders, {})
        self.assertEqual(session.order_history[-1]["Order Status"], "CANCELLED")
        self.assertEqual(updates[-1]["active_orders"], [])
        self.assertFalse(updates[-1]["health"]["pending_entry"])

    def test_live_limit_timeout_keeps_pending_entry_when_cancel_unresolved(self):
        updates = []
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=11),
            [option, fast_option_frame("PE")],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            settings(max_trades=1, lot_size=1, pending_entry_timeout_seconds=30),
            save_path=None,
            mode="PAPER",
            on_order_update=updates.append,
        )
        fake_orders = LivePendingCancelOrders(
            cancel_result={"cancelled": False, "accepted": True, "resolved": False, "status": "OPEN", "error": ""}
        )
        entry_signal = signal(option)
        entry_signal["entry_order_type"] = "LIMIT"
        session.mode = "LIVE"
        session.zerodha = object()
        session.orders = fake_orders
        session.pending_entry = {
            "signal": entry_signal,
            "option_index": 0,
            "order_id": "E1",
            "quantity": 75,
            "contract_lot_size": 75,
            "limit_price": 100,
            "placed_at": datetime.now() - timedelta(seconds=31),
            "placed_index": 0,
        }
        session._record_pending_entry_order(entry_signal, 0, "OPEN", "E1", 75, 75)

        session._check_pending_entry(0, force_timeout=True)

        self.assertEqual(fake_orders.cancelled, ["E1"])
        self.assertIsNotNone(session.pending_entry)
        self.assertFalse(session.risk_guard.kill_switch_active)
        self.assertEqual(len(session.active_orders), 1)
        self.assertTrue(updates[-1]["health"]["pending_entry"])
        self.assertEqual(updates[-1]["active_orders"][0]["Order Status"], "OPEN")

    def test_live_limit_timeout_keeps_pending_entry_and_blocks_when_cancel_fails(self):
        updates = []
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=11),
            [option, fast_option_frame("PE")],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            settings(max_trades=1, lot_size=1, pending_entry_timeout_seconds=30),
            save_path=None,
            mode="PAPER",
            on_order_update=updates.append,
        )
        fake_orders = LivePendingCancelOrders(
            cancel_result={
                "cancelled": False,
                "accepted": False,
                "resolved": False,
                "status": "UNKNOWN",
                "error": "network timeout",
            }
        )
        entry_signal = signal(option)
        entry_signal["entry_order_type"] = "LIMIT"
        session.mode = "LIVE"
        session.zerodha = object()
        session.orders = fake_orders
        session.pending_entry = {
            "signal": entry_signal,
            "option_index": 0,
            "order_id": "E1",
            "quantity": 75,
            "contract_lot_size": 75,
            "limit_price": 100,
            "placed_at": datetime.now() - timedelta(seconds=31),
            "placed_index": 0,
        }
        session._record_pending_entry_order(entry_signal, 0, "OPEN", "E1", 75, 75)

        session._check_pending_entry(0, force_timeout=True)

        self.assertEqual(fake_orders.cancelled, ["E1"])
        self.assertIsNotNone(session.pending_entry)
        self.assertTrue(session.risk_guard.kill_switch_active)
        self.assertEqual(len(session.active_orders), 1)
        self.assertTrue(updates[-1]["health"]["pending_entry"])
        self.assertIn("UNKNOWN_CANCEL_STATE", session.risk_guard.kill_switch_reason)

    def test_live_paper_limit_entry_fills_from_ltp_without_next_candle(self):
        test_settings = settings(
            entry_offset=-2,
            max_trades=1,
            lot_size=1,
            pending_entry_timeout_seconds=30,
        )
        nifty = nifty_frame("bearish", count=11)
        ce = fast_option_frame("CE")
        pe = fast_option_frame("PE", entry_type="limit")
        session = LivePaperSession(
            nifty,
            [ce, pe],
            {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
            test_settings,
            save_path=None,
            mode="PAPER",
        )

        session._try_entry(10)
        session.latest_ltp_by_option[1] = 101
        session._check_pending_entry(10)

        self.assertIsNone(session.pending_entry)
        self.assertIsNotNone(session.open_position)
        self.assertEqual(session.open_position["entry_price"], 102)

    def test_paper_trailing_stop_moves_virtual_sl_and_exits_on_trail(self):
        test_settings = settings(
            profit_points=20,
            safety_points=10,
            trailing_sl_enabled=True,
            trailing_start_points=10,
            trailing_step_points=5,
            trailing_lock_points=5,
        )
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=len(option)),
            [option],
            {1: "NIFTY", 2: "OPTION_0"},
            test_settings,
            save_path=None,
            mode="PAPER",
        )
        session._open_position_from_fill(signal(option), 1, "", 100, 1)

        session._check_live_exit_price(115, datetime(2026, 5, 12, 9, 18))

        self.assertIsNotNone(session.open_position)
        self.assertEqual(session.open_position["stoploss"], 110)
        self.assertEqual(session.open_position["last_trailing_level"], 15)

        session._check_live_exit_price(110, datetime(2026, 5, 12, 9, 19))

        self.assertIsNone(session.open_position)
        self.assertEqual(session.trades[-1]["Reason"], "TRAILING_STOPLOSS")
        self.assertEqual(session.trades[-1]["Exit"], 110)
        self.assertEqual(session.trades[-1]["stoploss_order_type"], "SL")
        self.assertEqual(session.order_history[-1]["Order Type"], "SL")
        self.assertEqual(session.order_history[-1]["Limit Price"], 108)
        self.assertEqual(session.order_history[-1]["Trigger Price"], 110)
        self.assertEqual(session.order_history[-1]["Exit Reason"], "TRAILING_STOPLOSS")
        self.assertFalse(session.trades[-1]["trailing_time_safeguard_applied"])

    def test_paper_trailing_time_safeguard_tightens_target_and_sl_when_start_not_reached(self):
        test_settings = settings(
            profit_points=20,
            safety_points=10,
            trailing_sl_enabled=True,
            trailing_start_points=10,
            trailing_step_points=5,
            trailing_lock_points=5,
        )
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=len(option)),
            [option],
            {1: "NIFTY", 2: "OPTION_0"},
            test_settings,
            save_path=None,
            mode="PAPER",
        )
        session._open_position_from_fill(signal(option), 1, "", 100, 1)

        session._check_live_exit_price(104, datetime(2026, 5, 12, 9, 30))

        self.assertIsNotNone(session.open_position)
        self.assertEqual(session.open_position["target"], 105)
        self.assertEqual(session.open_position["stoploss"], 95)
        self.assertTrue(session.open_position["trailing_time_safeguard_applied"])
        self.assertEqual(session.order_history[-2]["Order Type"], "LIMIT")
        self.assertEqual(session.order_history[-2]["Limit Price"], 105)
        self.assertEqual(session.order_history[-1]["Order Type"], "SL")
        self.assertEqual(session.order_history[-1]["Trigger Price"], 95)
        self.assertEqual(session.order_history[-1]["Limit Price"], 93)

    def test_paper_time_exit_records_sell_sl_with_ltp_trigger_and_buffer_limit(self):
        test_settings = settings(time_exit=1, stoploss_limit_buffer_points=2)
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=len(option)),
            [option],
            {1: "NIFTY", 2: "OPTION_0"},
            test_settings,
            save_path=None,
            mode="PAPER",
        )
        session._open_position_from_fill(signal(option), 1, "", 100, 1)

        session._check_live_exit(1)

        self.assertIsNone(session.open_position)
        self.assertEqual(session.trades[-1]["Reason"], "TIME_EXIT")
        self.assertEqual(session.order_history[-1]["Order Type"], "SL")
        self.assertEqual(session.order_history[-1]["Trigger Price"], 101)
        self.assertEqual(session.order_history[-1]["Limit Price"], 99)

    def test_live_time_exit_places_sell_sl_with_ltp_trigger_and_buffer_limit(self):
        test_settings = settings(time_exit=1, stoploss_limit_buffer_points=2)
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=len(option)),
            [option],
            {1: "NIFTY", 2: "OPTION_0"},
            test_settings,
            save_path=None,
            mode="LIVE",
            zerodha=None,
        )
        fake_orders = LiveProtectiveOrders()
        session.orders = fake_orders
        session._open_position_from_fill(signal(option), 75, "B1", 100, 75)

        session._check_live_exit(1)

        self.assertIsNone(session.open_position)
        self.assertEqual(session.trades[-1]["Reason"], "TIME_EXIT")
        self.assertEqual(fake_orders.placed[-1]["side"], "SELL")
        self.assertEqual(fake_orders.placed[-1]["order_type"], "SL")
        self.assertEqual(fake_orders.placed[-1]["trigger_price"], 101)
        self.assertEqual(fake_orders.placed[-1]["price"], 99)

    def test_live_trailing_stop_modifies_existing_sl_order_only(self):
        test_settings = settings(
            profit_points=20,
            safety_points=10,
            trailing_sl_enabled=True,
            trailing_start_points=10,
            trailing_step_points=5,
            trailing_lock_points=5,
        )
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=len(option)),
            [option],
            {1: "NIFTY", 2: "OPTION_0"},
            test_settings,
            save_path=None,
            mode="LIVE",
            zerodha=object(),
        )
        fake_orders = LiveTrailingOrders()
        session.orders = fake_orders
        session.open_position = {
            "trade_no": 1,
            "signal": signal(option),
            "option_index": 0,
            "entry_time": "2026-05-12 09:15:00",
            "entry_index": 0,
            "entry_price": 100,
            "target": 120,
            "stoploss": 90,
            "initial_target_price": 120,
            "initial_stoploss_price": 90,
            "current_sl_price": 90,
            "trailing_sl_enabled": True,
            "trailing_start_points": 10,
            "trailing_step_points": 5,
            "trailing_lock_points": 5,
            "last_trailing_level": 0,
            "trailing_modification_count": 0,
            "trailing_modifications": [],
            "quantity": 75,
            "contract_lot_size": 75,
            "entry_order_id": "B1",
            "target_order_id": "T1",
            "stoploss_order_id": "S1",
            "peak_price": 100,
        }

        changed = session._apply_trailing_stop(115, datetime(2026, 5, 12, 9, 18))

        self.assertTrue(changed)
        self.assertEqual(fake_orders.modified, [("S1", 110, 75, 108, "SL")])
        self.assertEqual(session.open_position["stoploss"], 110)

    def test_live_trailing_time_safeguard_modifies_existing_target_and_sl_orders(self):
        test_settings = settings(
            profit_points=20,
            safety_points=10,
            trailing_sl_enabled=True,
            trailing_start_points=10,
            trailing_step_points=5,
            trailing_lock_points=5,
        )
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=len(option)),
            [option],
            {1: "NIFTY", 2: "OPTION_0"},
            test_settings,
            save_path=None,
            mode="LIVE",
            zerodha=object(),
        )
        fake_orders = LiveProtectiveOrders()
        session.orders = fake_orders

        session._open_position_from_fill(signal(option), 75, "B1", 100, 75)
        changed = session._apply_trailing_time_safeguard(104, 5, datetime(2026, 5, 12, 9, 30))

        self.assertTrue(changed)
        self.assertEqual(session.open_position["target"], 105)
        self.assertEqual(session.open_position["stoploss"], 95)
        self.assertEqual(fake_orders.modified, [
            ("LIMIT", "O1", 105, 75),
            ("SL", "O2", 95, 75, 93, "SL"),
        ])

    def test_live_protective_stoploss_uses_sl_trigger_and_limit_buffer(self):
        test_settings = settings(profit_points=20, safety_points=10, stoploss_limit_buffer_points=2)
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=len(option)),
            [option],
            {1: "NIFTY", 2: "OPTION_0"},
            test_settings,
            save_path=None,
            mode="LIVE",
            zerodha=object(),
        )
        fake_orders = LiveProtectiveOrders()
        session.orders = fake_orders
        session.open_position = {
            "trade_no": 1,
            "signal": signal(option),
            "option_index": 0,
            "entry_price": 100,
            "target": 120,
            "stoploss": 90,
            "quantity": 75,
            "contract_lot_size": 75,
            "entry_order_id": "B1",
            "target_order_id": "",
            "stoploss_order_id": "",
        }

        session._place_protective_exit_orders()

        self.assertEqual(fake_orders.placed[0]["order_type"], "LIMIT")
        self.assertEqual(fake_orders.placed[0]["price"], 120)
        self.assertEqual(fake_orders.placed[1]["order_type"], "SL")
        self.assertEqual(fake_orders.placed[1]["trigger_price"], 90)
        self.assertEqual(fake_orders.placed[1]["price"], 88)
        self.assertLess(fake_orders.placed[1]["price"], fake_orders.placed[1]["trigger_price"])

    def test_live_option_market_entry_can_be_converted_to_aggressive_limit(self):
        test_settings = settings(
            profit_points=20,
            safety_points=10,
            live_option_market_entry_as_limit_enabled=True,
            live_option_market_entry_limit_buffer_points=2,
        )
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=len(option)),
            [option],
            {1: "NIFTY", 2: "OPTION_0"},
            test_settings,
            save_path=None,
            mode="LIVE",
            zerodha=object(),
        )
        fake_orders = LiveProtectiveOrders()
        session.orders = fake_orders
        entry_signal = signal(option)
        session.latest_ltp_by_option[0] = 101

        status, order_id = session._place_order("BUY", entry_signal, 75, order_type="MARKET")

        self.assertEqual(status, "BUY LIMIT ORDER PLACED")
        self.assertEqual(order_id, "O1")
        self.assertEqual(fake_orders.placed[0]["order_type"], "LIMIT")
        self.assertEqual(fake_orders.placed[0]["price"], 103)
        self.assertEqual(entry_signal["_live_entry_order_type_actual"], "LIMIT")
        self.assertEqual(entry_signal["_live_entry_limit_price"], 103)

    def test_live_low_price_stoploss_keeps_sl_limit_below_trigger(self):
        test_settings = settings(profit_points=20, safety_points=10, stoploss_limit_buffer_points=2)
        option = fast_option_frame("CE")
        session = LivePaperSession(
            nifty_frame("bullish", count=len(option)),
            [option],
            {1: "NIFTY", 2: "OPTION_0"},
            test_settings,
            save_path=None,
            mode="LIVE",
            zerodha=None,
        )

        session._open_position_from_fill(signal(option), 75, "B1", 1, 75)

        self.assertEqual(session.open_position["stoploss"], 0.1)
        self.assertEqual(session._stoploss_limit_price(session.open_position["stoploss"]), 0.05)
        self.assertLess(session._stoploss_limit_price(session.open_position["stoploss"]), session.open_position["stoploss"])


if __name__ == "__main__":
    unittest.main()
