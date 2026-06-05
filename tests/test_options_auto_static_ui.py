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


if __name__ == "__main__":
    unittest.main()
