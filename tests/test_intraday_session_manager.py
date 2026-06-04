import os
import tempfile
import unittest
from datetime import datetime, timedelta

from intraday.session_manager import IntradaySessionManager


class IntradaySessionManagerTests(unittest.TestCase):
    def upload_fii_dii(self, manager):
        return manager.upload_fii_dii_csv({
            "csv_text": "Date,Category,Buy Value,Sell Value,Net Value\n2026-06-02,FII/FPI,1000,1300,-300\n2026-06-02,DII,1400,900,500\n"
        })

    def payload(self):
        return {
            "mode": "PAPER",
            "stocks": ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"],
            "minimum_entry_score": 1,
            "minimum_risk_reward": 1.2,
            "ask_permission_before_entry": True,
            "max_trades_per_day": 3,
        }

    def market_data(self):
        data = {}
        for offset, symbol in enumerate(["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"]):
            candles = []
            for index in range(32):
                base = 100 + offset * 20 + index
                candles.append({
                    "open": base - 0.2,
                    "high": base + 1.0,
                    "low": base - 1.0,
                    "close": base + 0.4,
                    "volume": 85000 if index == 31 else 10000 + index * 1000,
                })
            data[symbol] = {
                "ltp": candles[-1]["close"],
                "candles": candles,
                "depth": {
                    "buy": [{"price": candles[-1]["close"] - 0.05, "quantity": 25000}],
                    "sell": [{"price": candles[-1]["close"] + 0.05, "quantity": 22000}],
                },
            }
        return data

    def test_paper_session_evaluates_approves_and_exports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            self.upload_fii_dii(manager)
            started = manager.start_session(self.payload())
            self.assertEqual(started["status"], "RUNNING")
            self.assertEqual(started["mode_state"]["banner"], "PAPER MODE ACTIVE")
            evaluated = manager.evaluate({"market_data": self.market_data(), "market_trend": "Bullish"})
            self.assertEqual(len(evaluated["snapshots"]), 5)
            self.assertIsNotNone(evaluated["pending_signal"])

            approved = manager.approve_entry({})
            self.assertIsNone(approved["pending_signal"])
            self.assertEqual(approved["last_signal"]["final_decision"], "ORDER_SENT")

            stopped = manager.stop_session()
            self.assertTrue(os.path.isfile(stopped["export_path"]))
            self.assertTrue(os.path.isfile(stopped["db_path"]))
            self.assertGreaterEqual(len(manager.database.table_rows("intraday_orders", manager.session_id)), 1)
            self.assertGreaterEqual(len(manager.database.table_rows("intraday_order_events", manager.session_id)), 1)
            self.assertGreaterEqual(len(manager.database.table_rows("intraday_symbols", manager.session_id)), 5)
            self.assertGreaterEqual(len(manager.database.table_rows("intraday_market_cues", manager.session_id)), 1)

    def test_evaluate_returns_latest_news_and_snapshot_sentiment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            self.upload_fii_dii(manager)
            manager.start_session(self.payload())
            evaluated = manager.evaluate({
                "market_data": self.market_data(),
                "market_trend": "Bullish",
                "news": [
                    {
                        "symbol": "INFY",
                        "headline": "INFY wins growth order and beats expectations",
                        "source": "Manual",
                    }
                ],
            })
            self.assertEqual(evaluated["latest_news"][0]["sentiment"], "Positive")
            infy = [row for row in evaluated["snapshots"] if row["symbol"] == "INFY"][0]
            self.assertEqual(infy["news_sentiment"], "Positive")
            self.assertGreater(infy["news_score"], 0)
            self.assertGreaterEqual(len(manager.database.table_rows("intraday_news", manager.session_id)), 1)

    def test_live_evaluate_loads_candles_for_all_selected_stocks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            self.upload_fii_dii(manager)
            manager.start_session({**self.payload(), "candle_interval": "3minute"})
            evaluated = manager.evaluate({"market_trend": "Bullish"})
            self.assertEqual(len(evaluated["snapshots"]), 5)
            self.assertEqual(set(evaluated["last_market_data_symbols"]), {"INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"})
            for row in evaluated["snapshots"]:
                self.assertEqual(row["candle_interval"], "3minute")
                self.assertGreater(row["candles_available"], 0)
                self.assertTrue(row["last_candle_time"])

    def test_pending_entry_expires_after_one_minute_without_user_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            self.upload_fii_dii(manager)
            manager.start_session(self.payload())
            evaluated = manager.evaluate({"market_data": self.market_data(), "market_trend": "Bullish"})
            self.assertIsNotNone(evaluated["pending_signal"])
            created_at = datetime.fromisoformat(evaluated["pending_signal"]["created_at"])

            expired = manager.evaluate({
                "market_data": self.market_data(),
                "market_trend": "Bullish",
                "current_time": (created_at + timedelta(seconds=61)).isoformat(timespec="seconds"),
            })
            self.assertIsNone(expired["pending_signal"])
            self.assertEqual(expired["last_signal"]["final_decision"], "EXPIRED_NO_USER_RESPONSE")
            self.assertIn("User approval timed out", "; ".join(expired["last_signal"]["blockers"]))

    def test_paper_or_real_start_requires_uploaded_fii_dii_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            with self.assertRaisesRegex(ValueError, "Upload valid NSE FII/DII CSV"):
                manager.start_session(self.payload())

            uploaded = self.upload_fii_dii(manager)
            self.assertTrue(uploaded["fii_dii_upload"]["valid"])
            started = manager.start_session(self.payload())
            self.assertEqual(started["fii_dii_upload"]["status"], "OK")


if __name__ == "__main__":
    unittest.main()
