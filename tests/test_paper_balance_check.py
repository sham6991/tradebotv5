import unittest

import pandas as pd

from execution_v2 import LivePaperSession


def frame(rows=1):
    df = pd.DataFrame([
        {
            "datetime": f"2026-05-10 09:{15 + index:02d}:00",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
        }
        for index in range(rows)
    ])
    df.attrs["instrument"] = "NIFTY25000CE"
    df.attrs["tradingsymbol"] = "NIFTY25000CE"
    df.attrs["option_type"] = "CE"
    return df


def settings(balance):
    return {
        "cooldown": 0,
        "balance": balance,
        "lot_size": 1,
        "max_trades": 1,
        "profit_points": 20,
        "safety_points": 10,
        "square_off_time": "",
        "enforce_market_hours": "0",
    }


def signal(entry=100, index=0, option=None):
    option = option if option is not None else frame(index + 1)
    return {
        "option": option,
        "option_index": 0,
        "type": "CE",
        "instrument": "NIFTY25000CE",
        "tradingsymbol": "NIFTY25000CE",
        "entry": entry,
        "entry_offset": 0,
        "signal_index": index,
        "nifty_signal_index": index,
        "entry_index": index,
        "score_row": {"Early Score": 85, "Buy Entry": "BUY"},
    }


class BrokerMarginOrders:
    def __init__(self, available):
        self.available = available
        self.calls = 0

    def available_margin(self):
        self.calls += 1
        if isinstance(self.available, BaseException):
            raise self.available
        return self.available

    def lot_size(self, tradingsymbol):
        return 1


class FailingPlacementOrders(BrokerMarginOrders):
    def __init__(self, available, session=None):
        super().__init__(available)
        self.session = session
        self.place_calls = 0
        self.last_candle_index_during_place = []
        self.transition_state_during_place = []

    def place_order(self, side, tradingsymbol, quantity, product="NRML", order_type="MARKET", price=None, trigger_price=None):
        self.place_calls += 1
        self.last_candle_index_during_place.append(self.session.last_candle_index)
        self.transition_state_during_place.append(self.session.order_transition_in_progress)
        return {
            "status": "FAILED: temporary network unavailable",
            "order_id": "",
            "log_status": "",
            "log_data": {},
            "error": "temporary network unavailable",
            "retriable": True,
            "requires_reconciliation": False,
        }


class FailingBrokerMarginOrders:
    def available_margin(self):
        raise AssertionError("Paper mode must not query broker margin")


class FixedSignalEngine:
    def __init__(self, entry_signal):
        self.cooldown_until = -1
        self.last_skip_reason = ""
        self.entry_signal = entry_signal

    def find_trade(self, nifty, options, i, settings):
        return self.entry_signal

    def mark_trade_complete(self, exit_index):
        self.cooldown_until = exit_index


class PaperBalanceCheckTests(unittest.TestCase):
    def session(self, balance, mode="PAPER"):
        return LivePaperSession(
            frame(),
            [frame()],
            {1: "NIFTY", 2: "OPTION_0"},
            settings(balance),
            save_path=None,
            mode=mode,
            zerodha=object() if mode == "LIVE" else None,
        )

    def test_paper_rejects_entry_when_simulated_balance_is_too_low(self):
        session = self.session(balance=500, mode="PAPER")

        error = session._validate_margin(signal(entry=100), qty=10)

        self.assertIn("insufficient paper balance", error)
        self.assertIn("available=500.00", error)
        self.assertIn("required=1000.00", error)

    def test_paper_allows_entry_when_simulated_balance_covers_required_value(self):
        session = self.session(balance=1500, mode="PAPER")

        self.assertEqual(session._validate_margin(signal(entry=100), qty=10), "")

    def test_paper_balance_check_is_isolated_from_broker_margin(self):
        session = self.session(balance=1500, mode="PAPER")
        session.orders = FailingBrokerMarginOrders()
        session.zerodha = object()

        self.assertEqual(session._validate_margin(signal(entry=100), qty=10), "")

    def test_live_still_uses_broker_margin_instead_of_local_balance(self):
        session = self.session(balance=500, mode="LIVE")
        orders = BrokerMarginOrders(available=2000)
        session.orders = orders

        self.assertEqual(session._validate_margin(signal(entry=100), qty=10), "")
        self.assertEqual(orders.calls, 1)

    def test_live_rejects_entry_when_broker_margin_lookup_fails(self):
        session = self.session(balance=500, mode="LIVE")
        session.orders = BrokerMarginOrders(available=RuntimeError("temporary margin outage"))

        error = session._validate_margin(signal(entry=100), qty=10)

        self.assertIn("live margin check failed", error)
        self.assertIn("temporary margin outage", error)

    def test_live_rejects_entry_when_broker_margin_is_unavailable(self):
        session = self.session(balance=500, mode="LIVE")
        session.orders = BrokerMarginOrders(available=None)

        self.assertEqual(
            session._validate_margin(signal(entry=100), qty=10),
            "ENTRY REJECTED: live margin unavailable",
        )

    def test_live_margin_rejection_does_not_stop_future_entry_scan(self):
        candles = frame(rows=2)
        session = LivePaperSession(
            candles,
            [candles],
            {1: "NIFTY", 2: "OPTION_0"},
            settings(500),
            save_path=None,
            mode="LIVE",
            zerodha=object(),
        )
        orders = BrokerMarginOrders(available=RuntimeError("temporary margin outage"))
        session.orders = orders
        session.engine = FixedSignalEngine(signal(entry=100, index=0, option=candles))
        placed_orders = []

        def fake_place_order(side, entry_signal, qty, order_type="MARKET", price=None, trigger_price=None):
            placed_orders.append((side, entry_signal["entry_index"], qty))
            return "FAILED: TEST ORDER BLOCK", ""

        session._place_order = fake_place_order

        session._try_entry(0)

        self.assertEqual(session.trade_count, 0)
        self.assertIsNone(session.open_position)
        self.assertEqual(len(session.trades), 1)
        self.assertIn("live margin check failed", session.trades[0]["Remarks"])
        self.assertEqual(placed_orders, [])

        orders.available = 2000
        session.engine.entry_signal = signal(entry=100, index=1, option=candles)

        session._try_entry(1)

        self.assertEqual(placed_orders, [("BUY", 1, 1)])

    def test_failed_entry_order_does_not_consume_candle_attempt(self):
        session = self.session(balance=500, mode="LIVE")
        session.orders = BrokerMarginOrders(available=2000)
        session.engine = FixedSignalEngine(signal(entry=100))
        order_calls = []

        def failing_place_order(side, entry_signal, qty, order_type="MARKET", price=None, trigger_price=None):
            order_calls.append((side, entry_signal["entry_index"], qty))
            return "FAILED: temporary broker timeout", ""

        session._place_order = failing_place_order

        session._try_entry(0)
        session._try_entry(0)

        self.assertEqual(order_calls, [("BUY", 0, 1), ("BUY", 0, 1)])
        self.assertEqual(session.trade_count, 0)
        self.assertEqual(session.entry_attempt_candle_keys, set())

    def test_completed_candle_is_marked_scanned_only_after_order_attempt_finishes(self):
        candles = frame(rows=8)
        session = LivePaperSession(
            candles,
            [candles],
            {1: "NIFTY", 2: "OPTION_0"},
            {**settings(500), "order_placement_retry_delay_seconds": 0},
            save_path=None,
            mode="LIVE",
            zerodha=object(),
        )
        orders = FailingPlacementOrders(available=2000, session=session)
        session.orders = orders
        session.engine = FixedSignalEngine(signal(entry=100, index=7, option=candles))

        session._process_completed_candles()

        self.assertEqual(orders.place_calls, 3)
        self.assertEqual(orders.last_candle_index_during_place, [-1, -1, -1])
        self.assertEqual(orders.transition_state_during_place, [True, True, True])
        self.assertEqual(session.last_candle_index, 7)
        self.assertIsNone(session.open_position)
        self.assertIsNone(session.pending_entry)

    def test_missed_limit_cooldown_defaults_to_no_future_block(self):
        session = self.session(balance=500, mode="PAPER")
        pending = {"signal": signal(entry=100), "option_index": 0, "placed_index": 0}

        session._mark_missed_limit_cooldown(pending)

        self.assertEqual(session.missed_limit_cooldown_until_by_option, {})

    def test_missed_limit_cooldown_remains_explicitly_configurable(self):
        session = self.session(balance=500, mode="PAPER")
        session.settings["missed_limit_cooldown_candles"] = 1
        pending = {"signal": signal(entry=100), "option_index": 0, "placed_index": 0}

        session._mark_missed_limit_cooldown(pending)

        self.assertEqual(session.missed_limit_cooldown_until_by_option, {0: 1})

    def test_rejected_entry_does_not_consume_max_trade_slot(self):
        session = self.session(balance=500, mode="PAPER")
        session.engine = FixedSignalEngine(signal(entry=100))

        session._try_entry(0)

        self.assertEqual(session.trade_count, 0)
        self.assertIsNone(session.open_position)
        self.assertEqual(len(session.trades), 1)
        self.assertIn("insufficient paper balance", session.trades[0]["Remarks"])

        session.balance = 10000
        session._try_entry(0)

        self.assertIsNone(session.open_position)
        self.assertIsNotNone(session.pending_entry)
        self.assertEqual(session.trade_count, 0)


if __name__ == "__main__":
    unittest.main()
