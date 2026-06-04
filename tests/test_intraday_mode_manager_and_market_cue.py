import unittest
from datetime import datetime

from intraday.constants import MODE_BACKTEST, MODE_PAPER, MODE_REAL
from intraday.market_cue_engine import classify_market_cue
from intraday.mode_manager import SessionModeManager


class IntradayModeManagerAndMarketCueTests(unittest.TestCase):
    def test_paper_login_blocks_real_order_permission(self):
        manager = SessionModeManager()
        manager.start_session(MODE_PAPER)
        state = manager.to_dict()
        self.assertTrue(state["permissions"]["paper_trade_allowed"])
        self.assertFalse(state["permissions"]["real_trade_allowed"])
        self.assertIn("PAPER MODE ACTIVE", state["banner"])
        self.assertIn("Mode blocked", manager.blocker_for(MODE_REAL))

    def test_backtest_mode_allows_only_simulated_orders(self):
        manager = SessionModeManager()
        manager.start_session(MODE_BACKTEST)
        state = manager.to_dict()
        self.assertTrue(state["permissions"]["backtest_allowed"])
        self.assertTrue(state["permissions"]["simulated_order_allowed"])
        self.assertFalse(state["permissions"]["paper_trade_allowed"])
        self.assertFalse(state["permissions"]["real_trade_allowed"])
        manager.assert_order_allowed(MODE_BACKTEST)
        with self.assertRaises(ValueError):
            manager.assert_order_allowed(MODE_REAL)

    def test_market_cue_uses_fii_dii_only_in_morning(self):
        payload = {
            "market_trend": "Bullish",
            "fii_dii": {"fii_net": 1000, "dii_net": 500},
            "nifty_price": 22500,
            "nifty_vwap": 22400,
        }
        morning = classify_market_cue(payload, current_time=datetime(2026, 6, 3, 9, 30))
        midday = classify_market_cue(payload, current_time=datetime(2026, 6, 3, 12, 15))
        self.assertTrue(morning.fii_dii_used)
        self.assertFalse(midday.fii_dii_used)
        self.assertIn("FII/DII", midday.ignored_sources[0])


if __name__ == "__main__":
    unittest.main()
