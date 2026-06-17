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
