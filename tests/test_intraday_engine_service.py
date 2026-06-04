import tempfile
import time
import unittest

from intraday.terminal_service import IntradayTerminalService


class IntradayEngineServiceTests(unittest.TestCase):
    def payload(self):
        return {
            "mode": "PAPER",
            "stocks": ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"],
            "minimum_entry_score": 1,
            "minimum_risk_reward": 1.1,
            "ask_permission_before_entry": True,
            "engine_interval_seconds": 1,
        }

    def test_start_runs_continuous_engine_until_stop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = IntradayTerminalService(temp_dir)
            service.upload_fii_dii({
                "csv_text": "Date,Category,Buy Value,Sell Value,Net Value\n2026-06-02,FII/FPI,1000,1300,-300\n2026-06-02,DII,1400,900,500\n"
            })
            started = service.start(self.payload())
            self.assertTrue(started["engine"]["running"])
            self.assertGreater(len(started["snapshots"]), 0)
            self.assertIn(started["latest_news_status"]["status"], {"OK", "UNAVAILABLE", "DISABLED"})

            for _ in range(20):
                status = service.status()
                if status["snapshots"]:
                    break
                time.sleep(0.1)

            self.assertTrue(status["engine"]["running"])
            self.assertGreater(len(status["snapshots"]), 0)

            stopped = service.stop()
            self.assertFalse(stopped["engine"]["running"])


if __name__ == "__main__":
    unittest.main()
