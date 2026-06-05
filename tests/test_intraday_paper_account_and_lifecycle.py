import os
import tempfile
import unittest

from intraday.paper_account import PaperAccountStore
from intraday.session_manager import IntradaySessionManager


class IntradayPaperAccountAndLifecycleTests(unittest.TestCase):
    def upload_fii_dii(self, manager):
        return manager.upload_fii_dii_csv({
            "csv_text": "Date,Category,Buy Value,Sell Value,Net Value\n2026-06-02,FII/FPI,1000,1300,-300\n2026-06-02,DII,1400,900,500\n"
        })

    def payload(self, **overrides):
        payload = {
            "mode": "PAPER",
            "stocks": ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"],
            "minimum_entry_score": 1,
            "minimum_risk_reward": 1.1,
            "ask_permission_before_entry": True,
            "paper_starting_balance": 100000,
            "allow_simulated_fallback": True,
            "require_live_data_for_paper": False,
        }
        payload.update(overrides)
        return payload

    def market_data(self, close=132.0, high=133.0, low=130.0):
        data = {}
        for offset, symbol in enumerate(["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"]):
            candles = []
            for index in range(35):
                base = close - 35 + index + offset
                candles.append({
                    "open": base - 0.2,
                    "high": max(base + 1.0, high if index == 34 else base + 1.0),
                    "low": min(base - 1.0, low if index == 34 else base - 1.0),
                    "close": base + 0.4,
                    "volume": 90000 if index == 34 else 10000 + index * 1000,
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

    def test_paper_account_persists_until_user_resets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "paper.json")
            store = PaperAccountStore(path)
            store.reset(75000)
            self.assertEqual(PaperAccountStore(path).snapshot()["available"], 75000)

    def test_paper_backtest_does_not_change_persistent_paper_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            manager.update_paper_account({"balance": 75000, "reset": True})
            before = manager.paper_account_status()
            result = manager.run_paper_backtest(self.payload(paper_starting_balance=100000, max_quantity_per_trade=1))
            after = manager.paper_account_status()
            for key in ("available", "used_margin", "position_value", "realized_pnl", "unrealized_pnl", "charges", "net_pnl"):
                self.assertEqual(after[key], before[key])
            self.assertTrue(result["summary"]["paper_balance_unchanged"])
            self.assertEqual(result["stopped"]["paper_account"]["available"], before["available"])
            self.assertIn("backtest_account", result["summary"])
            self.assertGreater(result["summary"]["replay_steps"], 1)
            self.assertGreater(result["summary"]["candle_count"], result["summary"]["replay_steps"] - 1)
            self.assertIn("best_signals", result["summary"])
            self.assertIn("best_possible_trades", result["summary"])
            self.assertEqual(result["started"]["settings"]["mode"], "BACKTEST")
            self.assertIn(os.path.join("intraday", "backtest"), result["summary"]["export_path"])

    def test_unfilled_limit_order_cancels_after_one_minute(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            self.upload_fii_dii(manager)
            manager.start_session(self.payload())
            manager.evaluate({"market_data": self.market_data(low=130), "market_trend": "Bullish"})
            manager.approve_entry({})
            status = manager.process_orders({"market_data": self.market_data(low=200), "force_entry_timeout": True})
            entry = [row for row in status["order_history"] if row.get("role") == "ENTRY"][0]
            self.assertEqual(entry["status"], "CANCELLED")
            self.assertIn("cancellation confirmed", entry["status_message"])

    def test_paper_entry_fill_places_oco_orders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            self.upload_fii_dii(manager)
            manager.start_session(self.payload())
            manager.evaluate({"market_data": self.market_data(low=130), "market_trend": "Bullish"})
            manager.approve_entry({})
            status = manager.process_orders({"market_data": self.market_data(low=1), "force_entry_timeout": False})
            roles = {row.get("role") for row in status["order_history"]}
            self.assertIn("STOPLOSS", roles)
            self.assertIn("TARGET", roles)
            self.assertIsNotNone(status["active_trade"])
            events = manager.database.table_rows("intraday_order_events", manager.session_id)
            self.assertGreaterEqual(len(events), 3)

    def test_stoploss_hit_closes_trade_without_pending_protective_orders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            self.upload_fii_dii(manager)
            manager.start_session(self.payload())
            manager.evaluate({"market_data": self.market_data(low=130), "market_trend": "Bullish"})
            manager.approve_entry({})
            status = manager.process_orders({"market_data": self.market_data(low=1), "force_entry_timeout": False})
            by_role = {row.get("role"): row for row in status["order_history"]}
            self.assertEqual(by_role["ENTRY"]["status"], "COMPLETE")
            self.assertEqual(by_role["STOPLOSS"]["status"], "COMPLETE")
            self.assertEqual(by_role["TARGET"]["status"], "CANCELLED")
            self.assertEqual(status["active_trade"]["status"], "CLOSED")
            trades = manager.database.table_rows("intraday_trades", manager.session_id)
            self.assertEqual(trades[-1]["exit_reason"], "STOPLOSS")

    def test_live_intraday_blocks_second_trade_while_one_is_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            self.upload_fii_dii(manager)
            manager.start_session(self.payload())
            manager.evaluate({"market_data": self.market_data(low=130), "market_trend": "Bullish"})
            manager.approve_entry({})
            filled = manager.process_orders({"market_data": self.market_data(low=130), "force_entry_timeout": False})
            self.assertEqual(filled["active_trade"]["status"], "OPEN")

            status = manager.evaluate({"market_data": self.market_data(close=140, low=138), "market_trend": "Bullish"})
            self.assertIsNone(status["pending_signal"])
            self.assertEqual(status["last_signal"]["final_decision"], "BLOCKED")
            self.assertIn("Another trade is already active.", status["last_signal"]["blockers"])


if __name__ == "__main__":
    unittest.main()
