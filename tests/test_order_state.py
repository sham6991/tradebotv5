import unittest

from order_state import (
    ENTRY_CANCELLED_EMPTY,
    ENTRY_CANCELLED_PARTIAL,
    ENTRY_FILLED,
    ENTRY_OPEN,
    ENTRY_PARTIAL,
    ENTRY_PENDING,
    ENTRY_REJECTED,
    EXIT_FILLED,
    EXIT_PARTIAL,
    EXIT_PENDING,
    EXIT_REJECTED,
    UNKNOWN,
    classify_order_state,
    normalize_order_status,
)


class OrderStateTests(unittest.TestCase):
    def test_normalizes_zerodha_and_local_status_text(self):
        cases = {
            "BUY MARKET ORDER PLACED": "OPEN",
            "SELL SL-M ORDER PLACED": "TRIGGER PENDING",
            "SELL SL ORDER PLACED": "TRIGGER PENDING",
            "FILLED": "COMPLETE",
            "FAILED: broker rejected": "REJECTED",
            "PUT_ORDER_REQ_RECEIVED": "PUT ORDER REQ RECEIVED",
            "CANCEL_PENDING": "CANCEL PENDING",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_order_status(raw), expected)

    def test_entry_order_states_separate_registration_fill_and_cancel(self):
        self.assertEqual(classify_order_state({"status": "VALIDATION PENDING"}, "ENTRY")["state"], ENTRY_PENDING)
        self.assertEqual(classify_order_state({"status": "OPEN", "quantity": 75}, "ENTRY")["state"], ENTRY_OPEN)
        self.assertEqual(
            classify_order_state({"status": "OPEN", "quantity": 75, "filled_quantity": 25}, "ENTRY")["state"],
            ENTRY_PARTIAL,
        )
        self.assertEqual(
            classify_order_state({"status": "COMPLETE", "quantity": 75, "filled_quantity": 75}, "ENTRY")["state"],
            ENTRY_FILLED,
        )
        self.assertEqual(
            classify_order_state({"status": "CANCELLED", "quantity": 75, "filled_quantity": 0}, "ENTRY")["state"],
            ENTRY_CANCELLED_EMPTY,
        )
        self.assertEqual(
            classify_order_state({"status": "CANCELLED", "quantity": 75, "filled_quantity": 25}, "ENTRY")["state"],
            ENTRY_CANCELLED_PARTIAL,
        )
        self.assertEqual(classify_order_state({"status": "REJECTED"}, "ENTRY")["state"], ENTRY_REJECTED)

    def test_exit_order_states_and_safe_cancel_metadata(self):
        self.assertEqual(classify_order_state({"status": "TRIGGER PENDING"}, "EXIT")["state"], EXIT_PENDING)
        self.assertEqual(
            classify_order_state({"status": "OPEN", "quantity": 75, "filled_quantity": 25}, "EXIT")["state"],
            EXIT_PARTIAL,
        )
        self.assertEqual(
            classify_order_state({"status": "COMPLETE", "quantity": 75, "filled_quantity": 75}, "EXIT")["state"],
            EXIT_FILLED,
        )
        self.assertEqual(classify_order_state({"status": "REJECTED"}, "EXIT")["state"], EXIT_REJECTED)

        cancelled = classify_order_state({"status": "CANCELLED", "quantity": 75, "filled_quantity": 0}, "EXIT")
        self.assertEqual(cancelled["state"], UNKNOWN)
        self.assertTrue(cancelled["is_safely_inactive"])
        self.assertFalse(cancelled["requires_reconciliation"])

    def test_unknown_state_requires_reconciliation(self):
        classified = classify_order_state({"status": "SOMETHING NEW", "quantity": 75}, "ENTRY")
        self.assertEqual(classified["state"], UNKNOWN)
        self.assertTrue(classified["requires_reconciliation"])


if __name__ == "__main__":
    unittest.main()
