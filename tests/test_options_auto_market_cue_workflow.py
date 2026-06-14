import tempfile
import unittest

from options_auto.data.fii_dii_loader import parse_fii_dii_csv_text
from options_auto.execution.execution_safety import DataQualityEngine
from options_auto.intelligence.market_cue_engine import MarketCueEngine
from options_auto.terminal_service import OptionsAutoTerminalService


class OptionsAutoMarketCueWorkflowTests(unittest.TestCase):
    def test_fii_dii_csv_upload_scores_against_turnover(self):
        parsed = parse_fii_dii_csv_text(
            "Category,Buy,Sell,Net\n"
            "FII,5000,1000,4000\n"
            "DII,1000,500,500\n"
            "Total turnover,,,90000\n",
            file_name="flows.csv",
        )

        self.assertEqual(parsed["status"], "OK")
        self.assertEqual(parsed["fii_net"], 4000)
        self.assertEqual(parsed["dii_net"], 500)
        self.assertEqual(parsed["total_turnover"], 90000)
        self.assertEqual(parsed["combined_net"], 4500)
        self.assertEqual(parsed["fii_dii_pct"], 5.0)
        self.assertEqual(parsed["fii_dii_score"], 50.0)

    def test_missing_premarket_upload_is_neutral_unless_required(self):
        neutral = MarketCueEngine().evaluate({"phase": "PREMARKET"}, phase="PREMARKET").to_dict()
        required = MarketCueEngine().evaluate({"phase": "PREMARKET", "require_fii_dii_upload": True}, phase="PREMARKET").to_dict()

        self.assertEqual(neutral["components"]["fii_dii"], 0)
        self.assertEqual(neutral["fii_dii_status"]["status"], "NEUTRAL_MISSING_UPLOAD")
        self.assertEqual(required["components"]["fii_dii"], 0)
        self.assertEqual(required["fii_dii_status"]["status"], "REQUIRED_MISSING_UPLOAD")

    def test_lunch_and_afternoon_ignore_uploaded_fii_dii(self):
        payload = {"fii_dii_status": {"status": "OK", "fii_net": 4000, "dii_net": 500, "total_turnover": 90000}}

        lunch = MarketCueEngine().evaluate(payload, phase="LUNCH").to_dict()
        afternoon = MarketCueEngine().evaluate(payload, phase="AFTERNOON").to_dict()

        self.assertEqual(lunch["components"]["fii_dii"], 0)
        self.assertEqual(lunch["fii_dii_status"]["status"], "IGNORED")
        self.assertEqual(afternoon["components"]["fii_dii"], 0)
        self.assertEqual(afternoon["fii_dii_status"]["status"], "IGNORED")

    def test_service_upload_status_feeds_premarket_cue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            upload = service.upload_fii_dii_csv({
                "file_name": "flows.csv",
                "csv_text": "Category,Buy,Sell,Net\nFII,5000,1000,4000\nDII,1000,500,500\nTotal turnover,,,90000\n",
            })
            cue = service.premarket_market_cue({"phase": "PREMARKET", "global_cue_score": 20, "previous_day_trend_score": 20})

            self.assertEqual(upload["status"], "OK")
            self.assertEqual(cue["fii_dii_status"]["score"], 50.0)
            self.assertEqual(cue["market_cue"]["components"]["fii_dii"], 50.0)

    def test_demo_data_is_blocked_for_paper_and_allowed_only_when_explicit(self):
        blocked = DataQualityEngine().validate_quote({"ltp": 142.4, "demo_data": True}, {"mode": "PAPER"}).to_dict()
        allowed = DataQualityEngine().validate_quote({"ltp": 142.4, "demo_data": True}, {"mode": "DEBUG"}).to_dict()

        self.assertFalse(blocked["allowed"])
        self.assertIn("demo/sample data", "; ".join(blocked["blockers"]))
        self.assertTrue(allowed["allowed"])


if __name__ == "__main__":
    unittest.main()
