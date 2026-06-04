import os
import unittest

from intraday.margin_engine import calculate_intraday_equity_quantity


class IntradayMarginEngineTests(unittest.TestCase):
    def calculate(self, **overrides):
        payload = {
            "symbol": "INFY",
            "exchange": "NSE",
            "side": "LONG",
            "entry_price": 1000,
            "stoploss_price": 990,
            "available_funds": 100000,
            "max_capital_allocation_percent": 20,
            "risk_per_trade_percent": 1,
            "estimated_leverage": 5,
            "mode": "PAPER",
        }
        payload.update(overrides)
        return calculate_intraday_equity_quantity(**payload)

    def test_paper_five_x_leverage_and_ten_point_stop_allows_hundred_quantity(self):
        result = self.calculate()
        self.assertEqual(result["margin_validation_status"], "PASSED")
        self.assertEqual(result["final_quantity"], 100)
        self.assertEqual(result["risk_based_quantity"], 100)
        self.assertEqual(result["margin_based_quantity"], 100)
        self.assertEqual(result["actual_required_margin"], 20000)

    def test_paper_wider_stop_reduces_risk_based_quantity(self):
        result = self.calculate(stoploss_price=980)
        self.assertEqual(result["margin_validation_status"], "PASSED")
        self.assertEqual(result["final_quantity"], 50)
        self.assertEqual(result["risk_based_quantity"], 50)
        self.assertEqual(result["margin_based_quantity"], 100)

    def test_real_margin_check_reduces_quantity_until_actual_margin_fits(self):
        seen_quantities = []

        def margin(request):
            seen_quantities.append(request.quantity)
            return {"required": request.quantity * 250, "available": 100000}

        result = self.calculate(mode="REAL", margin_calculator=margin)
        self.assertEqual(result["margin_validation_status"], "PASSED")
        self.assertEqual(result["final_quantity"], 80)
        self.assertEqual(result["actual_required_margin"], 20000)
        self.assertEqual(seen_quantities, [100, 80])

    def test_real_margin_api_failure_blocks_order(self):
        def margin(_request):
            raise RuntimeError("temporary Zerodha margin outage")

        result = self.calculate(mode="REAL", margin_calculator=margin)
        self.assertEqual(result["margin_validation_status"], "FAILED")
        self.assertEqual(result["final_quantity"], 0)
        self.assertIn("Real order blocked", result["rejection_reason"])

    def test_real_actual_required_margin_inside_allowed_capital_can_proceed(self):
        result = self.calculate(
            mode="REAL",
            margin_calculator=lambda request: {"required": request.quantity * 150, "available": 100000},
        )
        self.assertEqual(result["margin_validation_status"], "PASSED")
        self.assertEqual(result["final_quantity"], 100)
        self.assertLessEqual(result["actual_required_margin"], result["allowed_margin_capital"])

    def test_real_quantity_zero_after_margin_check_is_rejected(self):
        result = self.calculate(
            entry_price=100,
            stoploss_price=99,
            available_funds=1000,
            max_capital_allocation_percent=10,
            risk_per_trade_percent=1,
            mode="REAL",
            margin_calculator=lambda request: {"required": request.quantity * 1000, "available": 1000},
        )
        self.assertEqual(result["margin_validation_status"], "FAILED")
        self.assertEqual(result["final_quantity"], 0)
        self.assertIn("Insufficient margin", result["rejection_reason"])

    def test_intraday_ui_contains_margin_quantity_labels_and_warning(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "web_static", "intraday.html"), encoding="utf-8") as handle:
            html = handle.read()
        with open(os.path.join(root, "web_static", "intraday.js"), encoding="utf-8") as handle:
            script = handle.read()
        content = html + script
        for label in (
            "Estimated MIS Leverage",
            "Actual Required Margin",
            "Margin Validation",
            "Final Quantity",
            "Risk-Based Quantity",
            "Margin-Based Quantity",
            "Allowed Capital for This Trade",
            "Latest News &amp; Sentiment",
            "Live News",
            "Sentiment",
            "Live Paper Session",
            "Backtest / Replay Report",
            "Run Backtest / Replay",
            "Start Live Paper Session",
            "Chart Interval",
            "Last Candle",
        ):
            self.assertIn(label, content)
        self.assertIn(
            "Zerodha MIS equity leverage can be up to 5x for eligible stocks",
            html,
        )
        self.assertIn('switchStep("backtest")', script)
        self.assertNotIn("renderStatus(data.stopped);", script)


if __name__ == "__main__":
    unittest.main()
