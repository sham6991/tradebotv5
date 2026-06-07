import unittest
from unittest.mock import patch

import pandas as pd

from options_auto.backtest.backtest_engine import OptionsAutoBacktestEngine


class OptionsAutoBacktestMarginTests(unittest.TestCase):
    def test_losing_trade_reduces_next_contract_available_margin_but_profit_does_not_add(self):
        index = pd.DataFrame([
            {"datetime": "2026-06-04 10:00:00", "open": 22400, "high": 22420, "low": 22390, "close": 22410, "volume": 10000},
            {"datetime": "2026-06-04 10:03:00", "open": 22410, "high": 22415, "low": 22380, "close": 22390, "volume": 12000},
            {"datetime": "2026-06-04 10:06:00", "open": 22390, "high": 22430, "low": 22385, "close": 22420, "volume": 13000},
            {"datetime": "2026-06-04 10:09:00", "open": 22420, "high": 22480, "low": 22410, "close": 22470, "volume": 14000},
        ])
        option = pd.DataFrame([
            _option_row("2026-06-04 10:00:00", 100, 104, 98, 100),
            _option_row("2026-06-04 10:03:00", 100, 101, 80, 88),
            _option_row("2026-06-04 10:06:00", 70, 72, 68, 70),
            _option_row("2026-06-04 10:09:00", 70, 95, 69, 94),
        ])
        account_states = []

        def decision_stub(*, account_state, option_candidates, quotes, **_kwargs):
            account_states.append(dict(account_state))
            selected = dict(option_candidates[0])
            return {
                "allowed": True,
                "selected_contract": selected,
                "trade_plan": {
                    "tradingsymbol": selected["tradingsymbol"],
                    "quantity": 50,
                    "stop_distance": 10,
                    "target_distance": 20,
                },
                "decision_snapshot": {"stub": True},
            }

        with patch("options_auto.backtest.backtest_engine.evaluate_options_auto_decision", side_effect=decision_stub):
            result = OptionsAutoBacktestEngine().run(
                index,
                [option],
                {
                    "paper_starting_balance": 20000,
                    "backtest_entry_mode": "CURRENT_CANDLE_CLOSE",
                    "estimated_total_charges": 40,
                },
            )

        self.assertEqual(len(result["trades"]), 2)
        self.assertLess(result["trades"][0]["net_pnl"], 0)
        self.assertGreater(result["trades"][1]["net_pnl"], 0)
        reduced_margin = 20000 + result["trades"][0]["net_pnl"]

        self.assertEqual(account_states[0]["available_capital"], 20000)
        self.assertEqual(account_states[1]["available_capital"], reduced_margin)
        self.assertEqual(result["trades"][0]["available_margin_at_entry"], 20000)
        self.assertEqual(result["trades"][1]["available_margin_at_entry"], round(reduced_margin, 2))
        self.assertEqual(result["ending_available_margin"], round(reduced_margin, 2))
        self.assertEqual(result["margin_ledger"][1]["available_margin_after"], round(reduced_margin, 2))
        self.assertEqual(result["margin_ledger"][2]["available_margin_after"], round(reduced_margin, 2))


def _option_row(timestamp, open_, high, low, close):
    return {
        "datetime": timestamp,
        "tradingsymbol": "NIFTY26JUN22500CE",
        "instrument_token": "1",
        "instrument_type": "CE",
        "strike": 22500,
        "expiry": "2026-06-25",
        "lot_size": 50,
        "tick_size": 0.05,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "bid": close - 0.05,
        "ask": close + 0.05,
        "bid_qty": 3000,
        "ask_qty": 3000,
        "volume": 100000,
        "oi": 1000000,
    }


if __name__ == "__main__":
    unittest.main()
