import unittest

from position_reconciler import PositionReconciler


class FakeOrderManager:
    def __init__(self, statuses):
        self.statuses = statuses

    def order_status(self, order_id, fallback="UNKNOWN"):
        return self.statuses.get(order_id, fallback)


def signal():
    return {"instrument": "NIFTY25000CE", "type": "CE"}


class PositionReconcilerTests(unittest.TestCase):
    def test_clean_pending_entry_has_no_findings(self):
        reconciler = PositionReconciler(FakeOrderManager({"E1": "OPEN"}))

        findings = reconciler.reconcile(
            pending_entry={"order_id": "E1", "signal": signal()}
        )

        self.assertEqual(findings, [])

    def test_filled_pending_entry_is_reported(self):
        reconciler = PositionReconciler(FakeOrderManager({"E1": "COMPLETE"}))

        findings = reconciler.reconcile(
            pending_entry={"order_id": "E1", "signal": signal()}
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "PENDING_ENTRY_ALREADY_FILLED")
        self.assertEqual(findings[0]["status"], "COMPLETE")

    def test_open_position_with_active_protection_has_no_findings(self):
        reconciler = PositionReconciler(
            FakeOrderManager({"E1": "COMPLETE", "T1": "OPEN", "S1": "TRIGGER PENDING"})
        )

        findings = reconciler.reconcile(
            open_position={
                "trade_no": 1,
                "signal": signal(),
                "entry_order_id": "E1",
                "target_order_id": "T1",
                "stoploss_order_id": "S1",
            }
        )

        self.assertEqual(findings, [])

    def test_missing_protective_orders_are_reported(self):
        reconciler = PositionReconciler(FakeOrderManager({"E1": "COMPLETE"}))

        findings = reconciler.reconcile(
            open_position={
                "trade_no": 1,
                "signal": signal(),
                "entry_order_id": "E1",
                "target_order_id": "",
                "stoploss_order_id": "",
            }
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "PROTECTIVE_ORDERS_MISSING")

    def test_filled_exit_order_is_reported_as_error(self):
        reconciler = PositionReconciler(
            FakeOrderManager({"E1": "COMPLETE", "T1": "COMPLETE", "S1": "OPEN"})
        )

        findings = reconciler.reconcile(
            open_position={
                "trade_no": 1,
                "signal": signal(),
                "entry_order_id": "E1",
                "target_order_id": "T1",
                "stoploss_order_id": "S1",
            }
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["level"], "ERROR")
        self.assertEqual(findings[0]["code"], "TARGET_ORDER_ALREADY_FILLED")

    def test_rejected_stoploss_order_is_reported(self):
        reconciler = PositionReconciler(
            FakeOrderManager({"E1": "COMPLETE", "T1": "OPEN", "S1": "REJECTED"})
        )

        findings = reconciler.reconcile(
            open_position={
                "trade_no": 1,
                "signal": signal(),
                "entry_order_id": "E1",
                "target_order_id": "T1",
                "stoploss_order_id": "S1",
            }
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "STOPLOSS_ORDER_TERMINAL")
        self.assertEqual(findings[0]["status"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
