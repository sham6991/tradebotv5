import unittest

from order_manager import ZerodhaOrderManager, classify_order_error


class FakeZerodha:
    def __init__(self):
        self.calls = []
        self.next_id = 1
        self.status_by_id = {}
        self.average_price_by_id = {}
        self.filled_quantity_by_id = {}
        self.orders_by_id = {}
        self.raise_on_place = None

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

    def cancel_order(self, order_id):
        self.calls.append(("CANCEL", {"order_id": order_id}))
        self.status_by_id[order_id] = "CANCELLED"

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


class ZerodhaOrderManagerTests(unittest.TestCase):
    def test_classify_order_error_marks_timeout_as_unknown_state(self):
        classification = classify_order_error("Read timed out while placing order")

        self.assertEqual(classification["class"], "UNKNOWN_BROKER_STATE")
        self.assertTrue(classification["retriable"])
        self.assertTrue(classification["requires_reconciliation"])

    def test_classify_order_error_marks_margin_as_rejected(self):
        classification = classify_order_error("RMS: insufficient margin")

        self.assertEqual(classification["class"], "BROKER_REJECTED")
        self.assertFalse(classification["retriable"])
        self.assertFalse(classification["requires_reconciliation"])

    def test_paper_order_does_not_touch_zerodha(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="PAPER", default_lot_size=75)

        result = manager.place_order("BUY", "NIFTY25000CE", 75)

        self.assertEqual(result["status"], "PAPER BUY")
        self.assertEqual(result["order_id"], "")
        self.assertEqual(fake.calls, [])

    def test_live_places_market_limit_and_slm_orders(self):
        fake = FakeZerodha()
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)

        market = manager.place_order("BUY", "NIFTY25000CE", 50, product="NRML")
        limit = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="LIMIT", price=120)
        slm = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="SL-M", trigger_price=95)

        self.assertEqual(market["status"], "BUY MARKET ORDER PLACED")
        self.assertEqual(limit["status"], "SELL LIMIT ORDER PLACED")
        self.assertEqual(slm["status"], "SELL SL-M ORDER PLACED")
        self.assertEqual([call[0] for call in fake.calls], ["MARKET", "LIMIT", "SL-M"])
        self.assertEqual(limit["log_data"]["price"], 120)
        self.assertEqual(slm["log_data"]["trigger_price"], 95)

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
        stoploss = manager.place_order("SELL", "NIFTY25000CE", 50, product="NRML", order_type="SL-M", trigger_price=90)
        cancelled = manager.cancel_order(stoploss["order_id"])

        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(manager.order_status(stoploss["order_id"]), "CANCELLED")
        self.assertEqual(manager.order_status(target["order_id"]), "OPEN")
        self.assertEqual(manager.lot_size("NIFTY25000CE"), 50)
        self.assertEqual(manager.available_margin(), 123456.78)

    def test_place_failure_is_returned_not_raised(self):
        fake = FakeZerodha()
        fake.raise_on_place = "broker rejected"
        manager = ZerodhaOrderManager(fake, mode="LIVE", default_lot_size=75)

        result = manager.place_order("BUY", "NIFTY25000CE", 50)

        self.assertEqual(result["status"], "FAILED: broker rejected")
        self.assertEqual(result["order_id"], "")
        self.assertEqual(result["error"], "broker rejected")
        self.assertEqual(result["error_class"], "BROKER_REJECTED")

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
