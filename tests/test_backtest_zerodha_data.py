import unittest
from datetime import datetime, time, timedelta

import pandas as pd

from backtest_zerodha_data import fetch_zerodha_backtest_data
from web_app import WebTradeBotApp


class FakeZerodhaHistoricalClient:
    def __init__(self):
        self.historical_calls = []
        self.contract_calls = []

    def get_nifty50_token(self):
        return 256265

    def find_option_contract(self, option_type=None, strike=None, expiry=None, name="NIFTY"):
        self.contract_calls.append({
            "option_type": option_type,
            "strike": strike,
            "expiry": expiry,
            "name": name,
        })
        return {
            "tradingsymbol": f"NIFTY{expiry.replace('-', '')}{strike}{option_type}",
            "instrument_token": 100000 + len(self.contract_calls),
            "instrument_type": option_type,
            "strike": strike,
            "expiry": expiry,
        }

    def historical_candles(self, instrument_token, from_date, to_date, interval="5minute"):
        self.historical_calls.append({
            "instrument_token": instrument_token,
            "from_date": from_date,
            "to_date": to_date,
            "interval": interval,
        })
        rows = []
        start = datetime.combine(from_date.date(), time(9, 15))
        for offset in range(10):
            price = 100 + offset
            rows.append({
                "date": start + timedelta(minutes=offset * 3),
                "open": price,
                "high": price + 2,
                "low": price - 1,
                "close": price + 1,
                "volume": 1000 + offset,
            })
        return pd.DataFrame(rows)


class ZerodhaBacktestDataTests(unittest.TestCase):
    def test_fetch_uses_full_market_day_and_sets_option_metadata(self):
        client = FakeZerodhaHistoricalClient()

        nifty, options, metadata = fetch_zerodha_backtest_data(
            client,
            "2026-05-28",
            "3minute",
            {"buy_limit_score_low": 40},
            "25000",
            "2026-05-28",
            "25000",
            "2026-05-28",
        )

        self.assertFalse(nifty.empty)
        self.assertEqual(len(options), 2)
        self.assertEqual(metadata["from"], "2026-05-28 09:15:00")
        self.assertEqual(metadata["to"], "2026-05-28 15:30:00")
        self.assertEqual([call["interval"] for call in client.historical_calls], ["3minute"] * 3)
        self.assertTrue(all(call["from_date"].time() == time(9, 15) for call in client.historical_calls))
        self.assertTrue(all(call["to_date"].time() == time(15, 30) for call in client.historical_calls))
        self.assertEqual(options[0].attrs["option_type"], "CE")
        self.assertEqual(options[1].attrs["option_type"], "PE")
        self.assertIn("Early Score", options[0].columns)

    def test_web_app_adapter_uses_paper_client_without_switching_mode(self):
        app = WebTradeBotApp()
        app.current_mode = "LIVE"
        app.zerodha_clients_by_mode["PAPER"] = FakeZerodhaHistoricalClient()
        payload = app.normalise_backtest_payload({
            "data_source": "zerodha",
            "trade_date": "2026-05-28",
            "history_interval": "5 min",
            "call_strike": "25000",
            "call_expiry": "2026-05-28",
            "put_strike": "25000",
            "put_expiry": "2026-05-28",
        })

        _nifty, _options, metadata = app.load_zerodha_backtest_data(
            payload,
            {"chart_interval": "3 min", "buy_limit_score_low": 40},
        )

        self.assertEqual(app.current_mode, "LIVE")
        self.assertEqual(metadata["connected_mode"], "PAPER")
        self.assertEqual(metadata["interval"], "5minute")


if __name__ == "__main__":
    unittest.main()
