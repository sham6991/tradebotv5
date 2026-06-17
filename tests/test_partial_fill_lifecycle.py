import unittest

import pandas as pd

from execution_v2 import LivePaperSession


def option_frame():
    df = pd.DataFrame([
        {
            "datetime": "2026-05-10 09:15:00",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
        }
    ])
    df.attrs["instrument"] = "NIFTY25000CE"
    df.attrs["tradingsymbol"] = "NIFTY25000CE"
    df.attrs["option_type"] = "CE"
    return df


def signal(option):
    return {
        "option": option,
        "option_index": 0,
        "type": "CE",
        "instrument": "NIFTY25000CE",
        "tradingsymbol": "NIFTY25000CE",
        "entry": 100,
        "entry_offset": -2,
        "entry_index": 0,
        "score_row": {"Early Score": 85, "Buy Entry": "BUY"},
    }


class FakeOrderManager:
    def __init__(self, details_by_id):
        self.details_by_id = details_by_id
        self.cancelled = []
        self.placed = []
        self.next_id = 1

    def order_status(self, order_id, fallback="UNKNOWN"):
        return self.details_by_id.get(order_id, {}).get("status", fallback)

    def order_details(self, order_id, fallback_quantity=0, fallback_price=0):
        details = dict(self.details_by_id.get(order_id, {}))
        details.setdefault("order_id", order_id)
        details.setdefault("status", self.order_status(order_id))
        details.setdefault("quantity", int(fallback_quantity or 0))
        details.setdefault("filled_quantity", 0)
        details.setdefault("pending_quantity", int(fallback_quantity or 0))
        details.setdefault("cancelled_quantity", 0)
        details.setdefault("average_price", float(fallback_price or 0))
        details.setdefault("is_partial", details["filled_quantity"] > 0 and details["pending_quantity"] > 0)
        return details

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        details = self.details_by_id.setdefault(order_id, {})
        quantity = int(details.get("quantity", 0) or 0)
        filled = int(details.get("filled_quantity", 0) or 0)
        details["status"] = "CANCELLED"
        details["pending_quantity"] = 0
        details["cancelled_quantity"] = max(quantity - filled, 0)
        details["is_partial"] = False
        return {"cancelled": True, "error": ""}

    def place_order(self, side, tradingsymbol, quantity, product="NRML", order_type="MARKET", price=None, trigger_price=None):
        order_id = f"P{self.next_id}"
        self.next_id += 1
        self.placed.append({
            "side": side,
            "tradingsymbol": tradingsymbol,
            "quantity": quantity,
            "order_type": order_type,
            "price": price,
            "trigger_price": trigger_price,
        })
        self.details_by_id[order_id] = {
            "order_id": order_id,
            "status": "OPEN",
            "quantity": quantity,
            "filled_quantity": 0,
            "pending_quantity": quantity,
            "cancelled_quantity": 0,
            "average_price": price or trigger_price or 0,
            "is_partial": False,
        }
        return {
            "status": f"{side} {order_type} ORDER PLACED",
            "order_id": order_id,
            "log_status": f"{order_type} PLACED",
            "log_data": {"quantity": quantity, "price": price, "trigger_price": trigger_price},
            "error": "",
        }

    def available_margin(self):
        return 100000

    def lot_size(self, tradingsymbol):
        return 75


def session_with_orders(fake_orders):
    option = option_frame()
    session = LivePaperSession(
        option_frame(),
        [option, option_frame()],
        {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
        {
            "cooldown": 0,
            "balance": 100000,
            "lot_size": 1,
            "max_trades": 1,
            "profit_points": 20,
            "safety_points": 10,
            "square_off_time": "",
        },
        save_path=None,
        mode="PAPER",
    )
    session.mode = "LIVE"
    session.zerodha = object()
    session.orders = fake_orders
    return session, option


class MarketPartialEngine:
    def __init__(self, option):
        self.cooldown_until = -1
        self.last_skip_reason = ""
        self.option = option

    def find_trade(self, nifty, options, i, settings):
        data = signal(self.option)
        data["entry_offset"] = 0
        data["entry_order_type"] = "MARKET"
        data["entry_type"] = "MARKET"
        return data

    def mark_trade_complete(self, exit_index):
        self.cooldown_until = exit_index


class PartialFillLifecycleTests(unittest.TestCase):
    def test_partial_pending_entry_opens_position_for_filled_quantity(self):
        fake_orders = FakeOrderManager({
            "E1": {
                "status": "OPEN",
                "quantity": 75,
                "filled_quantity": 25,
                "pending_quantity": 50,
                "average_price": 99.5,
                "is_partial": True,
            }
        })
        session, option = session_with_orders(fake_orders)
        session.pending_entry = {
            "signal": signal(option),
            "option_index": 0,
            "order_id": "E1",
            "quantity": 75,
            "contract_lot_size": 75,
            "limit_price": 100,
            "placed_at": pd.to_datetime("2026-05-10 09:15:00").to_pydatetime(),
            "placed_index": 0,
        }

        session._check_pending_entry(0)

        self.assertIsNone(session.pending_entry)
        self.assertEqual(fake_orders.cancelled, ["E1"])
        self.assertIsNotNone(session.open_position)
        self.assertEqual(session.open_position["quantity"], 25)
        self.assertEqual(session.open_position["entry_price"], 99.5)
        self.assertEqual([order["quantity"] for order in fake_orders.placed], [25, 25])

    def test_legacy_market_partial_entry_no_longer_runs_in_limit_only_mode(self):
        fake_orders = FakeOrderManager({})
        session, option = session_with_orders(fake_orders)
        session.settings["enforce_market_hours"] = 0
        session.settings["check_margin"] = 0
        session.settings["entry_order_fill_timeout_seconds"] = 0.1
        session.engine = MarketPartialEngine(option)

        original_place_order = fake_orders.place_order

        def place_order(side, tradingsymbol, quantity, product="NRML", order_type="MARKET", price=None, trigger_price=None):
            result = original_place_order(side, tradingsymbol, quantity, product, order_type, price, trigger_price)
            if side == "BUY" and order_type == "MARKET":
                fake_orders.details_by_id[result["order_id"]].update({
                    "status": "OPEN",
                    "quantity": quantity,
                    "filled_quantity": 25,
                    "pending_quantity": quantity - 25,
                    "average_price": 100,
                    "is_partial": True,
                })
            return result

        fake_orders.place_order = place_order

        session._try_entry(0)

        self.assertEqual(fake_orders.cancelled, [])
        self.assertIsNone(session.open_position)
        self.assertTrue(session.pending_entry or session.trades)

    def test_partial_exit_fill_activates_kill_switch_without_finalizing_trade(self):
        fake_orders = FakeOrderManager({
            "T1": {
                "status": "OPEN",
                "quantity": 75,
                "filled_quantity": 25,
                "pending_quantity": 50,
                "average_price": 120,
                "is_partial": True,
            },
            "S1": {
                "status": "OPEN",
                "quantity": 75,
                "filled_quantity": 0,
                "pending_quantity": 75,
                "average_price": 0,
                "is_partial": False,
            },
        })
        session, option = session_with_orders(fake_orders)
        session.open_position = {
            "trade_no": 1,
            "signal": signal(option),
            "option_index": 0,
            "entry_time": "2026-05-10 09:15:00",
            "entry_index": 0,
            "entry_price": 100,
            "target": 120,
            "stoploss": 90,
            "quantity": 75,
            "contract_lot_size": 75,
            "entry_order_id": "E1",
            "target_order_id": "T1",
            "stoploss_order_id": "S1",
            "peak_price": 100,
        }

        handled = session._check_protective_exit_orders(0, force=True)

        self.assertTrue(handled)
        self.assertIsNotNone(session.open_position)
        self.assertEqual(session.trade_count, 0)
        self.assertTrue(session.risk_guard.kill_switch_active)
        self.assertIn("PARTIAL TARGET EXIT FILL DETECTED", session.trading_blocked_reason)


if __name__ == "__main__":
    unittest.main()
