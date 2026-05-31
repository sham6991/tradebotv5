import unittest

from order_manager import ZerodhaOrderManager, classify_order_error
from zerodha_client import ZerodhaClient


class FakeZerodha:
    def __init__(self):
        self.calls = []
        self.next_id = 1
        self.status_by_id = {}
        self.average_price_by_id = {}
        self.filled_quantity_by_id = {}
        self.orders_by_id = {}
        self.raise_on_place = None
        self.cancel_failures_before_success = 0
        self.cancel_leaves_status_open = False
        self.cancel_sets_pending = False

    def _new_order(self, prefix):
        order_id = f"{prefix}{self.next_id}"
        self.next_id += 1
        self.status_by_id[order_id] = "OPEN"
        self.orders_by_id[order_id] = {
            "order_id": order_id,
            "status": "OPEN",
            "quantity": 0,
            "filled_quantity": 0,
            "pending_quantity": 0,
            "cancelled_quantity": 0,
            "average_price": 0,
        }
        return order_id

    def place_market_order(self, **kwargs):
        if self.raise_on_place:
            raise RuntimeError(self.raise_on_place)
        self.calls.append(("MARKET", kwargs))
        return self._new_order("M")

    def place_limit_order(self, **kwargs):
        if self.raise_on_place:
            raise RuntimeError(self.raise_on_place)
        self.calls.append(("LIMIT", kwargs))
        return self._new_order("L")

    def place_stoploss_market_order(self, **kwargs):
        if self.raise_on_place:
            raise RuntimeError(self.raise_on_place)
        self.calls.append(("SL-M", kwargs))
        return self._new_order("S")

    def place_stoploss_limit_order(self, **kwargs):
        if self.raise_on_place:
            raise RuntimeError(self.raise_on_place)
        self.calls.append(("SL", kwargs))
        return self._new_order("S")

    def cancel_order(self, order_id):
        self.calls.append(("CANCEL", {"order_id": order_id}))
        if self.cancel_failures_before_success > 0:
            self.cancel_failures_before_success -= 1
            raise RuntimeError("temporary cancel failure")
        if self.cancel_sets_pending:
            self.status_by_id[order_id] = "CANCEL PENDING"
            return
        if self.cancel_leaves_status_open:
            return
        self.status_by_id[order_id] = "CANCELLED"

    def modify_stoploss_market_order(self, **kwargs):
        self.calls.append(("MODIFY_SLM", kwargs))
        order_id = kwargs["order_id"]
        self.orders_by_id[order_id]["trigger_price"] = kwargs["trigger_price"]
        return order_id

    def modify_stoploss_limit_order(self, **kwargs):
        self.calls.append(("MODIFY_SL", kwargs))
        order_id = kwargs["order_id"]
        self.orders_by_id[order_id]["trigger_price"] = kwargs["trigger_price"]
        self.orders_by_id[order_id]["price"] = kwargs["price"]
        return order_id

    def modify_limit_order(self, **kwargs):
        self.calls.append(("MODIFY_LIMIT", kwargs))
        order_id = kwargs["order_id"]
        self.orders_by_id[order_id]["price"] = kwargs["price"]
        return order_id

    def order_status(self, order_id):
        return self.status_by_id.get(order_id, "UNKNOWN")

    def get_order(self, order_id):
        order = self.orders_by_id.get(order_id)
        if not order:
            return None
        order = dict(order)
        order["status"] = self.status_by_id.get(order_id, order.get("status", "UNKNOWN"))
        return order

    def order_average_price(self, order_id):
        return self.average_price_by_id.get(order_id)

    def order_filled_quantity(self, order_id):
        return self.filled_quantity_by_id.get(order_id, 0)

    def get_lot_size(self, tradingsymbol):
        self.calls.append(("LOT_SIZE", {"tradingsymbol": tradingsymbol}))
        return 50

    def available_margin(self):
        return 123456.78


class FakeKite:
    VARIETY_REGULAR = "regular"
    EXCHANGE_NFO = "NFO"
    PRODUCT_NRML = "NRML"
    ORDER_TYPE_SLM = "SL-M"
    TRANSACTION_TYPE_SELL = "SELL"
    VALIDITY_DAY = "DAY"

    def __init__(self):
        self.calls = []

    def place_order(self, **kwargs):
        self.calls.append(kwargs)
        return "OID1"


class ZerodhaOrderManagerTests(unittest.TestCase):
    def test_classify_order_error_marks_timeout_as_unknown_state(self):
        classification = classify_order_error("Read timed out while placing order")

        self.assertEqual(classification["class"], "UNKNOWN_BROKER_STATE")
        self.assertTrue(classification["retriable"])
        self.assertTrue(classification["requires_reconciliation"])

    def test_classify_order_error_marks_margin_as_rejected(self):
        classification = classify_order_error("RMS: insufficient margin")

        self.assertEqual(classification["class"], "BROKER_REJECTED")
        self.assertEqual(classification["category"], "margin")
        self.assertFalse(classification["retriable"])
        self.assertFalse(classification["requires_reconciliation"])

    def test_paper_order_does_not_touch_zerodha(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="PAPER", default_lot_size=75)

        result = manager.place_order("BUY", "NIFTY25000CE", 75)

        self.assertEqual(result["status"], "PAPER BUY")
        self.assertEqual(result["order_id"], "")
        self.assertEqual(fake.calls, [])

    def test_live_places_market_limit_and_sl_orders(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)

        market = manager.place_order("BUY", "NIFTY25000CE", 50, product="NRML")
        limit = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="LIMIT", price=120)
        stoploss = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="SL", trigger_price=95, price=93)

        self.assertEqual(market["status"], "BUY MARKET ORDER PLACED")
        self.assertEqual(limit["status"], "SELL LIMIT ORDER PLACED")
        self.assertEqual(stoploss["status"], "SELL SL ORDER PLACED")
        self.assertEqual([call[0] for call in fake.calls], ["MARKET", "LIMIT", "SL"])
        self.assertEqual(limit["log_data"]["price"], 120)
        self.assertEqual(stoploss["log_data"]["trigger_price"], 95)
        self.assertEqual(stoploss["log_data"]["price"], 93)

    def test_live_rejects_slm_for_option_symbols(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)

        result = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="SL-M", trigger_price=95)

        self.assertTrue(result["status"].startswith("FAILED"))
        self.assertIn("SL-M is blocked", result["error"])
        self.assertEqual(fake.calls, [])

    def test_low_level_client_rejects_slm_for_option_symbols(self):
        client = object.__new__(ZerodhaClient)
        client.kite = FakeKite()

        with self.assertRaisesRegex(ValueError, "SL-M is blocked"):
            client.place_stoploss_market_order("NIFTY25000CE", "SELL", 50, trigger_price=95)

        self.assertEqual(client.kite.calls, [])

    def test_order_lifecycle_status_fill_cancel_margin_and_lot_size(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)

        entry = manager.place_order("BUY", "NIFTY25000CE", 50, product="NRML", order_type="LIMIT", price=100)
        entry_id = entry["order_id"]
        self.assertEqual(manager.order_status(entry_id), "OPEN")

        fake.status_by_id[entry_id] = "COMPLETE"
        fake.average_price_by_id[entry_id] = 99.5
        fake.filled_quantity_by_id[entry_id] = 50

        self.assertEqual(manager.order_status(entry_id), "COMPLETE")
        self.assertEqual(manager.average_price(entry_id, fallback=100), 99.5)
        self.assertEqual(manager.filled_quantity(entry_id, fallback=75), 50)

        target = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="LIMIT", price=120)
        stoploss = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="SL", trigger_price=90, price=88)
        cancelled = manager.cancel_order(stoploss["order_id"])

        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(manager.order_status(stoploss["order_id"]), "CANCELLED")
        self.assertEqual(manager.order_status(target["order_id"]), "OPEN")
        self.assertEqual(manager.lot_size("NIFTY25000CE"), 50)
        self.assertEqual(manager.available_margin(), 123456.78)

    def test_cancel_order_retries_and_refreshes_final_status(self):
        fake = FakeZerodha()
        fake.cancel_failures_before_success = 1
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)
        stoploss = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="SL", trigger_price=90, price=88)

        cancelled = manager.cancel_order(stoploss["order_id"], retries=2, retry_delay=0)

        self.assertTrue(cancelled["cancelled"])
        self.assertTrue(cancelled["resolved"])
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual([call[0] for call in fake.calls if call[0] == "CANCEL"], ["CANCEL", "CANCEL"])

    def test_cancel_order_does_not_mark_cancelled_when_refreshed_status_stays_open(self):
        fake = FakeZerodha()
        fake.cancel_leaves_status_open = True
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)
        stoploss = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="SL", trigger_price=90, price=88)

        cancelled = manager.cancel_order(stoploss["order_id"], retries=1, retry_delay=0)

        self.assertFalse(cancelled["cancelled"])
        self.assertTrue(cancelled["accepted"])
        self.assertFalse(cancelled["resolved"])
        self.assertEqual(cancelled["status"], "OPEN")

    def test_cancel_order_reports_cancel_pending_as_accepted_not_resolved(self):
        fake = FakeZerodha()
        fake.cancel_sets_pending = True
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)
        stoploss = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="SL", trigger_price=90, price=88)

        cancelled = manager.cancel_order(stoploss["order_id"], retries=2, retry_delay=0)

        self.assertFalse(cancelled["cancelled"])
        self.assertTrue(cancelled["accepted"])
        self.assertFalse(cancelled["resolved"])
        self.assertEqual(cancelled["status"], "CANCEL PENDING")

    def test_live_modifies_existing_stoploss_trigger(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)
        stoploss = manager.place_order("SELL", "NIFTY25000CE", 50, order_type="SL", trigger_price=90, price=88)

        result = manager.modify_stoploss_trigger(stoploss["order_id"], 105, quantity=50, price=103)

        self.assertTrue(result["modified"])
        self.assertEqual(fake.calls[-1][0], "MODIFY_SL")
        self.assertEqual(fake.calls[-1][1]["order_id"], stoploss["order_id"])
        self.assertEqual(fake.calls[-1][1]["trigger_price"], 105)
        self.assertEqual(fake.calls[-1][1]["price"], 103)

    def test_live_modifies_existing_limit_price(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)
        target = manager.place_order("SELL", "NIFTY25000CE", 50, order_type="LIMIT", price=120)

        result = manager.modify_limit_price(target["order_id"], 105, quantity=50)

        self.assertTrue(result["modified"])
        self.assertEqual(fake.calls[-1][0], "MODIFY_LIMIT")
        self.assertEqual(fake.calls[-1][1]["order_id"], target["order_id"])
        self.assertEqual(fake.calls[-1][1]["price"], 105)
        self.assertEqual(fake.calls[-1][1]["quantity"], 50)

    def test_place_failure_is_returned_not_raised(self):
        fake = FakeZerodha()
        fake.raise_on_place = "broker rejected"
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)

        result = manager.place_order("BUY", "NIFTY25000CE", 50)

        self.assertEqual(result["status"], "FAILED: broker rejected")
        self.assertEqual(result["order_id"], "")
        self.assertEqual(result["error"], "broker rejected")
        self.assertEqual(result["error_class"], "BROKER_REJECTED")
        self.assertEqual(result["error_category"], "rejected")

    def test_order_details_reports_partial_fill_quantities(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)
        order = manager.place_order("BUY", "NIFTY25000CE", 75, order_type="LIMIT", price=100)
        order_id = order["order_id"]
        fake.orders_by_id[order_id].update({
            "quantity": 75,
            "filled_quantity": 25,
            "pending_quantity": 50,
            "average_price": 99.75,
        })

        details = manager.order_details(order_id)

        self.assertEqual(details["status"], "OPEN")
        self.assertEqual(details["quantity"], 75)
        self.assertEqual(details["filled_quantity"], 25)
        self.assertEqual(details["pending_quantity"], 50)
        self.assertEqual(details["average_price"], 99.75)
        self.assertTrue(details["is_partial"])

    def test_order_details_reports_cancelled_with_partial_fill(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)
        order = manager.place_order("BUY", "NIFTY25000CE", 75, order_type="LIMIT", price=100)
        order_id = order["order_id"]
        fake.status_by_id[order_id] = "CANCELLED"
        fake.orders_by_id[order_id].update({
            "quantity": 75,
            "filled_quantity": 25,
            "pending_quantity": 0,
            "cancelled_quantity": 50,
            "average_price": 99.75,
        })

        details = manager.order_details(order_id)

        self.assertEqual(details["status"], "CANCELLED")
        self.assertEqual(details["filled_quantity"], 25)
        self.assertEqual(details["cancelled_quantity"], 50)
        self.assertFalse(details["is_partial"])

    def test_order_details_falls_back_when_raw_order_missing(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)
        fake.status_by_id["MISSING"] = "OPEN"
        fake.average_price_by_id["MISSING"] = 101.5
        fake.filled_quantity_by_id["MISSING"] = 20

        details = manager.order_details("MISSING", fallback_quantity=75, fallback_price=100)

        self.assertEqual(details["status"], "OPEN")
        self.assertEqual(details["quantity"], 75)
        self.assertEqual(details["filled_quantity"], 20)
        self.assertEqual(details["pending_quantity"], 55)
        self.assertEqual(details["average_price"], 101.5)
        self.assertTrue(details["is_partial"])


if __name__ == "__main__":
    unittest.main()
