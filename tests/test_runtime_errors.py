import unittest

from runtime_errors import classify_runtime_error


class RuntimeErrorClassificationTests(unittest.TestCase):
    def test_order_timeout_is_unknown_broker_state(self):
        result = classify_runtime_error("Read timed out after order submit", context="order_placement")

        self.assertEqual(result["category"], "unknown_broker_state")
        self.assertEqual(result["class"], "UNKNOWN_BROKER_STATE")
        self.assertTrue(result["retriable"])
        self.assertTrue(result["requires_reconciliation"])

    def test_non_order_timeout_is_timeout_category(self):
        result = classify_runtime_error("Read timed out during health check", context="network_health")

        self.assertEqual(result["category"], "timeout")
        self.assertEqual(result["class"], "BROKER_TIMEOUT")
        self.assertTrue(result["retriable"])
        self.assertFalse(result["requires_reconciliation"])

    def test_margin_error_is_margin_category(self):
        result = classify_runtime_error("RMS: insufficient margin", context="margin")

        self.assertEqual(result["category"], "margin")
        self.assertEqual(result["class"], "BROKER_MARGIN_ERROR")
        self.assertFalse(result["retriable"])

    def test_auth_error_is_auth_category(self):
        result = classify_runtime_error("Access token is invalid", context="profile")

        self.assertEqual(result["category"], "auth")
        self.assertEqual(result["class"], "BROKER_AUTH_ERROR")


if __name__ == "__main__":
    unittest.main()
