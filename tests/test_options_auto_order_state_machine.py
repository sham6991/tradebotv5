import unittest

from options_auto.execution.oco_manager import OCOManager
from options_auto.execution.order_state_machine import OrderStateMachine


class FakeCancelBroker:
    def __init__(self):
        self.cancelled = []

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return "CANCELLED"


class OptionsAutoOrderStateMachineTests(unittest.TestCase):
    def test_state_machine_rejects_skipped_fill(self):
        machine = OrderStateMachine()

        with self.assertRaisesRegex(ValueError, "Invalid Options Auto order transition"):
            machine.transition("ENTRY_FILLED")

    def test_state_machine_allows_protected_path(self):
        machine = OrderStateMachine()
        machine.transition("ENTRY_ORDER_PLACING")
        machine.transition("ENTRY_FILLED")
        machine.transition("PROTECTION_PENDING")
        machine.transition("OCO_ACTIVE")
        machine.transition("POSITION_ACTIVE")

        self.assertEqual(machine.state, "POSITION_ACTIVE")

    def test_oco_target_fill_cancels_stoploss(self):
        broker = FakeCancelBroker()
        manager = OCOManager(broker)
        manager.register("T1", "E1", "TARGET1", "SL1", 50)

        result = manager.on_order_update("T1", "TARGET1", "COMPLETE")

        self.assertEqual(result["action"], "CANCEL_PEER")
        self.assertEqual(broker.cancelled, ["SL1"])


if __name__ == "__main__":
    unittest.main()

