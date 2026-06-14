import tempfile
import unittest

from options_auto.terminal_service import OptionsAutoTerminalService


def index_rows(count=8):
    return [
        {
            "datetime": f"2026-06-04 10:{i:02d}:00",
            "open": 22500 + i * 3,
            "high": 22512 + i * 3,
            "low": 22495 + i * 3,
            "close": 22508 + i * 3,
            "volume": 10000 + i * 500,
        }
        for i in range(count)
    ]


def option_rows(symbol, option_type, count=8):
    return [
        {
            "datetime": f"2026-06-04 10:{i:02d}:00",
            "tradingsymbol": symbol,
            "instrument_token": f"BT-{symbol}",
            "instrument_type": option_type,
            "option_type": option_type,
            "open": 100 + i,
            "high": 108 + i,
            "low": 98 + i,
            "close": 104 + i,
            "volume": 50000 + i * 1000,
            "lot_size": 50,
            "tick_size": 0.05,
        }
        for i in range(count)
    ]


class NoOrderBroker:
    def __init__(self):
        self.order_calls = []

    def orders(self):
        raise AssertionError("Backtest must not fetch live broker orderbook.")

    def positions(self):
        raise AssertionError("Backtest must not fetch live broker positions.")

    def place_limit_order(self, **kwargs):
        self.order_calls.append(kwargs)
        raise AssertionError("Backtest must not place broker orders.")

    def place_stoploss_limit_order(self, **kwargs):
        self.order_calls.append(kwargs)
        raise AssertionError("Backtest must not place stoploss broker orders.")


class OptionsAutoBacktestParityTests(unittest.TestCase):
    def _service(self, client=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return OptionsAutoTerminalService(temp_dir.name, kite_client_provider=lambda _mode: client)

    def test_backtest_baseline_report_enforced(self):
        service = self._service()

        result = service.backtest({
            "candles": index_rows(),
            "option_candles": [
                option_rows("NIFTY26JUN22500CE", "CE"),
                option_rows("NIFTY26JUN22500PE", "PE"),
            ],
            "settings": {
                "paper_starting_balance": 20000,
                "buy_score_threshold": 95,
                "premium_expansion_required": False,
                "market_context_enabled": True,
                "backtest_compare_market_context_scenarios": True,
            },
        })

        scenarios = result["market_context_scenarios"]
        self.assertEqual(list(scenarios), ["BASELINE", "REPORT_ONLY", "ENFORCED"])
        self.assertFalse(scenarios["BASELINE"]["settings"]["market_context_enabled"])
        self.assertTrue(scenarios["REPORT_ONLY"]["settings"]["market_context_enabled"])
        self.assertFalse(scenarios["REPORT_ONLY"]["settings"]["market_context_enforcement_enabled"])
        self.assertTrue(scenarios["ENFORCED"]["settings"]["market_context_enforcement_enabled"])
        for scenario in scenarios.values():
            self.assertIn("metrics", scenario)
            self.assertIn("zero_trade_reason", scenario)

    def test_backtest_missing_data_assumptions(self):
        service = self._service()

        result = service.backtest({
            "candles": index_rows(),
            "option_candles": [option_rows("NIFTY26JUN22500CE", "CE")],
            "settings": {
                "paper_starting_balance": 20000,
                "buy_score_threshold": 95,
                "premium_expansion_required": False,
            },
        })

        assumptions = result["historical_data_assumptions"]
        self.assertTrue(assumptions["synthetic_quote_proxy_used"])
        self.assertIn("bid", assumptions["unavailable_fields"])
        self.assertIn("ask", assumptions["unavailable_fields"])
        self.assertIn("bid_qty", assumptions["unavailable_fields"])
        self.assertIn("ask_qty", assumptions["unavailable_fields"])
        self.assertIn("oi", assumptions["unavailable_fields"])
        self.assertIn("news", assumptions["unavailable_fields"])
        self.assertIn("current Zerodha Pulse", assumptions["missing_data_policy"]["news"])
        self.assertEqual(result["metrics"]["charges"], sum(float(trade.get("charges") or 0) for trade in result["trades"]))

    def test_backtest_real_safety_simulation_no_broker_calls(self):
        broker = NoOrderBroker()
        service = self._service(broker)

        result = service.backtest({
            "candles": index_rows(),
            "option_candles": [option_rows("NIFTY26JUN22500CE", "CE")],
            "settings": {
                "paper_starting_balance": 20000,
                "buy_score_threshold": 95,
                "premium_expansion_required": False,
            },
        })

        self.assertEqual(result["real_orders_placed"], 0)
        self.assertEqual(broker.order_calls, [])


if __name__ == "__main__":
    unittest.main()
