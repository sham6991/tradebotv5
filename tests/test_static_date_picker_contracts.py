from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StaticDatePickerContractTests(unittest.TestCase):
    def test_main_and_intraday_keep_native_date_inputs(self):
        pages = {
            "main": ROOT / "web_static" / "index.html",
            "intraday": ROOT / "web_static" / "intraday.html",
        }

        for name, path in pages.items():
            with self.subTest(page=name):
                html = path.read_text(encoding="utf-8")
                self.assertIn('type="date"', html)

    def test_all_terminal_scripts_initialize_native_date_pickers(self):
        scripts = {
            "main": ROOT / "web_static" / "app.js",
            "intraday": ROOT / "web_static" / "intraday.js",
        }

        for name, path in scripts.items():
            with self.subTest(script=name):
                js = path.read_text(encoding="utf-8")
                self.assertIn("function initNativeDatePickers", js)
                self.assertIn("input[type='date']", js)
                self.assertIn("showPicker", js)

    def test_shared_terminal_css_keeps_calendar_indicator_visible(self):
        css = (ROOT / "web_static" / "terminal_design.css").read_text(encoding="utf-8")

        self.assertIn('input[type="date"]', css)
        self.assertIn("::-webkit-calendar-picker-indicator", css)
        self.assertIn("color-scheme: dark", css)
        self.assertIn("cursor: pointer", css)


if __name__ == "__main__":
    unittest.main()
