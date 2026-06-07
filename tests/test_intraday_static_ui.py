import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IntradayStaticUiTests(unittest.TestCase):
    def test_intraday_ui_exposes_data_source_and_safety_controls(self):
        html = (ROOT / "web_static" / "intraday.html").read_text(encoding="utf-8")
        js = (ROOT / "web_static" / "intraday.js").read_text(encoding="utf-8")
        css = (ROOT / "web_static" / "intraday.css").read_text(encoding="utf-8")
        content = html + js + css

        for token in (
            "data-source-card",
            "data-source-badge",
            "data-source-warning",
            "allow-simulated-fallback",
            "paper-fill-model",
            "emergency-exit-order-type",
            "limit-timeout",
            "strategy-profile",
            "auto-real-orders-confirmed",
            "Stock Data Health",
            "Live Tick Status",
            "Backfill Status",
            "Profile Min Score",
            "renderDataSource",
            "renderUnavailableSource",
            "strategy_profile",
            "stock_data_health",
            "stock_live_feed",
            "profile_policy",
            "allow_simulated_fallback",
            "require_live_data_for_paper",
            "auto_real_orders_confirmed",
            "paper_fill_model",
            "emergency_exit_order_type",
            "beforeunload",
            "statusPollBusy",
            "/api/intraday/ui-summary",
            "renderUiSummary",
            "scheduleStatusPolling",
            "AbortController",
            "Engine Interval",
            "Last Cycle",
            "Next Interval Reason",
        ):
            self.assertIn(token, content)


if __name__ == "__main__":
    unittest.main()
