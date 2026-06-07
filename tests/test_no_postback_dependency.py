from pathlib import Path
import unittest

from options_auto.config.options_auto_defaults import DEFAULT_OPTIONS_AUTO_SETTINGS
from web_app import TradeBotRequestHandler


class NoPostbackDependencyTests(unittest.TestCase):
    def test_options_auto_defaults_use_polling_reconciliation_not_postback(self):
        self.assertEqual(
            DEFAULT_OPTIONS_AUTO_SETTINGS["real_order_update_source"],
            "POLLING_AND_RECONCILIATION",
        )

    def test_no_zerodha_postback_route_is_registered(self):
        source = Path("web_app.py").read_text(encoding="utf-8")
        self.assertIn("/zerodha/callback", source)
        self.assertNotIn("/zerodha/postback", source)
        self.assertTrue(hasattr(TradeBotRequestHandler, "route_get"))
        self.assertTrue(hasattr(TradeBotRequestHandler, "route_post"))

    def test_no_postback_url_config_is_required(self):
        for path in Path(".").rglob("*"):
            if not path.is_file():
                continue
            if path.name == "test_no_postback_dependency.py":
                continue
            if any(part in {".git", "__pycache__", ".codex_screenshots"} for part in path.parts):
                continue
            if path.suffix.lower() not in {".py", ".js", ".html", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("postback_url", text.lower(), str(path))
            self.assertNotIn("POSTBACK_URL", text, str(path))


if __name__ == "__main__":
    unittest.main()
