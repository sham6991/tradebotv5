from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OptionsAutoStaticUITests(unittest.TestCase):
    def test_hidden_tab_panels_have_css_display_guard(self):
        css = (ROOT / "web_static" / "options_auto.css").read_text(encoding="utf-8")

        self.assertRegex(css, r"\.oa-tab-panel\[hidden\]\s*\{[^}]*display:\s*none\s*!important")

    def test_dashboard_contains_fii_upload_and_data_source_panel(self):
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")

        self.assertIn('id="oa-fii-dii-form"', html)
        self.assertIn('id="oa-fii-dii-file"', html)
        self.assertIn('id="oa-data-source-panel"', html)
        self.assertIn('id="oa-demo-banner"', html)
        self.assertEqual(html.count('data-index-tick-panel'), 3)
        self.assertIn("Index Tick Stream", html)
        self.assertIn('id="oa-stop-engine-top"', html)
        self.assertIn('id="oa-kill-switch-top"', html)
        self.assertIn('id="oa-paper-kill"', html)
        self.assertIn('id="oa-real-stop-engine"', html)
        self.assertIn('id="oa-real-kill"', html)
        self.assertIn("REAL MONEY MODE - LIVE ZERODHA ORDERS ONLY AFTER PREFLIGHT", html)
        self.assertNotIn("disabled in this build", html.lower())

    def test_raw_json_controls_stay_in_debug_tab(self):
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")
        debug_index = html.index('id="oa-tab-debug"')

        for control_id in ("oa-market-cue-json", "oa-instruments-json", "oa-quotes-json"):
            control_index = html.index(f'id="{control_id}"')
            self.assertGreater(control_index, debug_index)

    def test_javascript_keeps_demo_samples_outside_live_modes(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn("allowDemo", js)
        self.assertIn("demo_data", js)
        self.assertIn("/api/options-auto/market-cue/fii-dii-upload", js)
        self.assertRegex(js, re.compile(r"panel\.hidden\s*=", re.MULTILINE))

    def test_backtest_payload_uses_historical_data_request(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")
        match = re.search(r"function backtestPayload\(\) \{(?P<body>.*?)\n\}", js, re.DOTALL)

        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertIn('id="oa-backtest-spot"', html)
        self.assertIn('id="oa-backtest-span"', html)
        self.assertIn('data_source: "zerodha_historical"', body)
        self.assertIn("trade_date: tradeDate", body)
        self.assertIn("backtest_spot: backtestSpot", body)
        self.assertIn("atm_scan_strike_span: span", body)
        self.assertIn("underlying", body)
        self.assertIn("interval", body)
        self.assertNotIn("sampleReplayCandles()", body)

    def test_live_options_data_health_fields_are_visible(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        for token in (
            "Spot Source",
            "Live Spot",
            "ATM Strike",
            "Candidate Span",
            "Candidate Count",
            "Valid Quote Count",
            "Missing Quote Keys",
            "Live Scanner",
            "Next Action",
            "Index Candles",
            "Candle Interval",
            "ZERODHA_REQUIRED",
        ):
            self.assertIn(token, js)
        self.assertRegex(js, re.compile(r"allowDemo\s*\?\s*parseJson", re.MULTILINE))

    def test_index_tick_stream_renders_from_status_buffer(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn("function renderIndexTickStreams", js)
        self.assertIn("state.status.index_ticks", js)
        self.assertIn("data-index-tick-badge", js)
        self.assertIn("live_index_candles", js)

    def test_stop_and_kill_controls_call_live_engine_routes(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn("function stopEngine", js)
        self.assertIn("function killSwitch", js)
        self.assertIn("/api/options-auto/stop", js)
        self.assertIn("/api/options-auto/kill-switch", js)


if __name__ == "__main__":
    unittest.main()
