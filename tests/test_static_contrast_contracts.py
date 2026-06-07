from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _linear(channel: int) -> float:
    normalized = channel / 255
    return normalized / 12.92 if normalized <= 0.03928 else ((normalized + 0.055) / 1.055) ** 2.4


def contrast_ratio(foreground: str, background: str) -> float:
    fg = _hex_to_rgb(foreground)
    bg = _hex_to_rgb(background)
    fg_luminance = 0.2126 * _linear(fg[0]) + 0.7152 * _linear(fg[1]) + 0.0722 * _linear(fg[2])
    bg_luminance = 0.2126 * _linear(bg[0]) + 0.7152 * _linear(bg[1]) + 0.0722 * _linear(bg[2])
    lighter = max(fg_luminance, bg_luminance)
    darker = min(fg_luminance, bg_luminance)
    return (lighter + 0.05) / (darker + 0.05)


class StaticContrastContractTests(unittest.TestCase):
    def test_options_auto_backtest_table_overrides_shared_dark_shell_table_colors(self):
        css = (ROOT / "web_static" / "options_auto.css").read_text(encoding="utf-8")

        for token in (
            "body.app-shell .oa-shell .oa-table",
            "body.app-shell .oa-shell .oa-table th",
            "body.app-shell .oa-shell .oa-table td",
            "body.app-shell .oa-shell .oa-table tbody tr:nth-child(even)",
            "body.app-shell .oa-shell .oa-status-badge",
            "body.app-shell .oa-shell .oa-badge-green",
            "body.app-shell .oa-shell .oa-badge-red",
            "body.app-shell .oa-shell .oa-badge-yellow",
            "body.app-shell .oa-shell .oa-badge-grey",
            "body.app-shell .oa-shell .oa-badge-blue",
            'body.app-shell .oa-shell input[type="date"]',
            "color-scheme: light",
        ):
            self.assertIn(token, css)

    def test_main_and_intraday_tables_override_light_even_row_defaults_in_dark_shell(self):
        app_css = (ROOT / "web_static" / "app.css").read_text(encoding="utf-8")
        intraday_css = (ROOT / "web_static" / "intraday.css").read_text(encoding="utf-8")

        for css in (app_css, intraday_css):
            with self.subTest(css=css[:20]):
                self.assertIn("body.app-shell table", css)
                self.assertIn("background: var(--bg-panel)", css)
                self.assertIn("body.app-shell td", css)
                self.assertIn("color: var(--text-primary)", css)
                self.assertIn("body.app-shell tbody tr:nth-child(even)", css)
                self.assertIn("background: var(--bg-panel-soft)", css)

    def test_required_contrast_pairs_stay_above_wcag_normal_text_threshold(self):
        pairs = {
            "options_auto_table_text": ("#17212b", "#ffffff"),
            "options_auto_table_even": ("#17212b", "#f6f9fc"),
            "options_auto_table_header": ("#25364a", "#edf4fa"),
            "options_auto_green_badge": ("#0f6b45", "#e8f6ef"),
            "options_auto_red_badge": ("#9f1f17", "#fdeceb"),
            "options_auto_yellow_badge": ("#744600", "#fff4dc"),
            "options_auto_blue_badge": ("#164f82", "#e7f1fb"),
            "main_dark_table": ("#eef4fb", "#121a24"),
            "main_dark_even_table": ("#eef4fb", "#182332"),
            "intraday_dark_table": ("#eef4fb", "#121a24"),
            "intraday_dark_even_table": ("#eef4fb", "#182332"),
        }

        for name, (foreground, background) in pairs.items():
            with self.subTest(pair=name):
                self.assertGreaterEqual(contrast_ratio(foreground, background), 4.5)


if __name__ == "__main__":
    unittest.main()
