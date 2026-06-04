import os
import tempfile
import unittest

import web_app


class FakeMarketCueService:
    def save(self, payload):
        return {"report_id": 7, "saved": True, "summary": {}}

    def report(self, report_id):
        return {"report_id": report_id, "report_text": "Saved market cue report"}


class ResultFolderTests(unittest.TestCase):
    def test_web_result_folder_routes_to_named_subfolder(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_results = web_app.RESULT_FOLDER
            web_app.RESULT_FOLDER = temp_dir
            try:
                self.assertEqual(
                    web_app.result_folder("backtest"),
                    os.path.join(temp_dir, "backtest"),
                )
                self.assertEqual(
                    web_app.result_folder("backtest_risk_setting_optimizer"),
                    os.path.join(temp_dir, "backtest risk setting optimizer"),
                )
                self.assertEqual(
                    web_app.result_folder("backtest_trading_tab_optimizer"),
                    os.path.join(temp_dir, "backtest trading tab optimizer"),
                )
            finally:
                web_app.RESULT_FOLDER = original_results

    def test_market_cue_save_writes_report_file_under_market_cue_folder(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            original_results = web_app.RESULT_FOLDER
            web_app.RESULT_FOLDER = temp_dir
            try:
                app = web_app.WebTradeBotApp()
                app.market_cue = FakeMarketCueService()

                result = app.market_cue_save({})
                self.assertTrue(result["output_path"].startswith(os.path.join(temp_dir, "market_cue")))
                self.assertTrue(os.path.exists(result["output_path"]))
                with open(result["output_path"], "r", encoding="utf-8") as handle:
                    self.assertIn("Saved market cue report", handle.read())
            finally:
                web_app.RESULT_FOLDER = original_results


if __name__ == "__main__":
    unittest.main()
