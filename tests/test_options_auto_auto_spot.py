import tempfile
import unittest
from datetime import date, datetime, time as dt_time, timedelta

import pandas as pd

from options_auto.constants import MODE_BACKTEST, MODE_PAPER, MODE_REAL
from options_auto.data.index_data_provider import OptionsAutoIndexDataProvider, nearest_strike
from options_auto.data.option_chain_builder import OptionChainBuilder
from options_auto.terminal_service import OptionsAutoTerminalService


def option_instruments(strikes=(22450, 22500, 22550, 22600), underlying="NIFTY"):
    rows = []
    for strike in strikes:
        for option_type in ("CE", "PE"):
            rows.append({
                "tradingsymbol": f"{underlying}26JUN{strike}{option_type}",
                "name": underlying,
                "underlying": underlying,
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_token": int(f"{strike}{1 if option_type == 'CE' else 2}"),
                "instrument_type": option_type,
                "strike": strike,
                "expiry": date(2026, 6, 25),
                "lot_size": 50,
                "tick_size": 0.05,
            })
    return rows


def index_rows(count=80, trade_day=None):
    start = datetime.combine(trade_day or date.today(), dt_time(9, 15))
    return [
        {
            "datetime": (start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": 22400 + i * 2.0,
            "high": 22425 + i * 2.0,
            "low": 22395 + i * 2.0,
            "close": 22410 + i * 2.0,
            "volume": 10000 + i * 120,
        }
        for i in range(count)
    ]


def option_rows(strike: int, option_type: str, count=80, trade_day=None):
    start = datetime.combine(trade_day or date.today(), dt_time(9, 15))
    return [
        {
            "datetime": (start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": 100 + i,
            "high": 108 + i,
            "low": 98 + i,
            "close": 104 + i,
            "volume": 100000 + i * 100,
        }
        for i in range(count)
    ]


class FakeOptionsZerodha:
    def __init__(self, spot=22540.0, returned_option_keys=None, label="PAPER", option_price=104.0, trade_day=None):
        self.spot = spot
        self.returned_option_keys = set(returned_option_keys or [])
        self.label = label
        self.option_price = option_price
        self.trade_day = trade_day or date.today()
        self.quote_calls = []
        self.historical_calls = []
        self.options = option_instruments()

    def instruments(self, exchange=None):
        if exchange == "NSE":
            return [{"tradingsymbol": "NIFTY 50", "name": "NIFTY 50", "instrument_token": 256265}]
        if exchange == "NFO":
            return list(self.options)
        return []

    def quote(self, keys):
        keys = list(keys or [])
        self.quote_calls.append(keys)
        rows = {}
        for key in keys:
            if key == "NSE:NIFTY 50":
                rows[key] = {
                    "last_price": self.spot,
                    "source": self.label,
                    "timestamp": datetime.combine(self.trade_day, dt_time(10, 35)).isoformat(),
                    "volume": 25000,
                }
            elif not self.returned_option_keys or key in self.returned_option_keys:
                rows[key] = {
                    "last_price": self.option_price,
                    "bid": self.option_price - 0.05,
                    "ask": self.option_price + 0.05,
                    "bid_qty": 2500,
                    "ask_qty": 2400,
                    "volume": 120000,
                    "oi": 900000,
                    "premium_return_1": 1.2,
                    "premium_return_3": 4.5,
                    "relative_volume": 1.7,
                    "option_vwap": self.option_price - 2,
                    "option_atr14": 5,
                    "momentum_score": 80,
                }
        return rows

    def get_nifty50_token(self):
        return 256265

    def historical_candles(self, instrument_token, from_dt, to_dt, interval="3minute"):
        self.historical_calls.append((instrument_token, interval))
        if int(instrument_token) == 256265:
            return pd.DataFrame(index_rows(trade_day=self.trade_day))
        for row in self.options:
            if int(row["instrument_token"]) == int(instrument_token):
                return pd.DataFrame(option_rows(int(row["strike"]), row["instrument_type"], trade_day=self.trade_day))
        return pd.DataFrame()


class OptionsAutoAutoSpotTests(unittest.TestCase):
    def test_provider_fetches_nifty_spot_from_paper_and_ignores_manual_spot(self):
        client = FakeOptionsZerodha(spot=22540)
        provider = OptionsAutoIndexDataProvider(lambda _mode: client)

        result = provider.get_spot("NIFTY", MODE_PAPER, payload={"spot": 99999})

        self.assertEqual(result["spot"], 22540)
        self.assertEqual(result["spot_source"], "zerodha_paper_data")
        self.assertEqual(client.quote_calls[0], ["NSE:NIFTY 50"])

    def test_provider_fetches_nifty_spot_from_real(self):
        client = FakeOptionsZerodha(spot=22472, label="REAL")
        provider = OptionsAutoIndexDataProvider(lambda _mode: client)

        result = provider.get_spot("NIFTY", MODE_REAL, payload={"spot": 11111})

        self.assertEqual(result["spot"], 22472)
        self.assertEqual(result["spot_source"], "zerodha_real_data")

    def test_backtest_spot_uses_manual_value_and_does_not_quote_live_spot(self):
        client = FakeOptionsZerodha(spot=99999)
        provider = OptionsAutoIndexDataProvider(lambda _mode: client)

        result = provider.get_spot("NIFTY", MODE_BACKTEST, payload={"backtest_spot": 22540}, index_candles=index_rows())

        self.assertEqual(result["spot"], 22540)
        self.assertEqual(result["spot_source"], "backtest_manual_spot")
        self.assertEqual(client.quote_calls, [])

    def test_nearest_strike_examples(self):
        self.assertEqual(nearest_strike(22540, 50), 22550)
        self.assertEqual(nearest_strike(22472, 50), 22450)

    def test_candidate_builder_creates_ce_pe_around_atm_span(self):
        result = OptionChainBuilder().build(option_instruments(strikes=(22450, 22500, 22550, 22600, 22650)), "NIFTY", 22540, span=2, strike_step=50)

        self.assertEqual(result["atm"], 22550)
        self.assertEqual(result["strikes"], [22450.0, 22500.0, 22550.0, 22600.0, 22650.0])
        self.assertEqual(result["contracts_requested"], 10)
        self.assertEqual(result["contracts_found"], 10)
        self.assertEqual({row["option_type"] for row in result["contracts"]}, {"CE", "PE"})

    def test_service_paper_fetches_spot_chain_and_quotes_from_paper_client(self):
        client = FakeOptionsZerodha(spot=22540)
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)

            result = service.evaluate({
                "mode": MODE_PAPER,
                "spot": 99999,
                "settings": {"underlying": "NIFTY", "atm_scan_strike_span": 1, "market_cue_alignment_required": False},
            })

        self.assertEqual(result["spot_value"], 22540)
        self.assertEqual(result["spot_source"], "zerodha_paper_data")
        self.assertEqual(result["atm_strike"], 22550)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["contract_lock"]["ce"]["strike"], 22600)
        self.assertEqual(result["contract_lock"]["pe"]["strike"], 22500)
        self.assertEqual(result["contract_lock"]["ce"]["quantity"], 50)
        self.assertGreater(result["valid_quote_count"], 0)
        self.assertTrue(any(len(call) == 2 for call in client.quote_calls))
        self.assertEqual(service.status()["index_ticks"][-1]["spot"], 22540)
        self.assertEqual(service.status()["index_ticks"][-1]["spot_source"], "zerodha_paper_data")

    def test_service_real_fetches_from_real_client_not_paper_client(self):
        paper = FakeOptionsZerodha(spot=11111, label="PAPER")
        real = FakeOptionsZerodha(spot=22540, label="REAL")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: real if str(mode).upper() == "LIVE" else paper)

            result = service.evaluate({
                "mode": MODE_REAL,
                "spot": 99999,
                "settings": {"underlying": "NIFTY", "atm_scan_strike_span": 1, "confirm_real_mode": True},
            })

        self.assertEqual(result["spot_value"], 22540)
        self.assertEqual(result["spot_source"], "zerodha_real_data")
        self.assertEqual(paper.quote_calls, [])
        self.assertTrue(real.quote_calls)
        self.assertEqual(service.status()["index_ticks"][-1]["mode"], MODE_REAL)
        self.assertEqual(service.status()["index_ticks"][-1]["spot"], 22540)

    def test_missing_spot_quote_blocks_with_exact_data_governor_state(self):
        client = FakeOptionsZerodha(spot=0)
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda _mode: client)

            result = service.evaluate({"mode": MODE_PAPER, "settings": {"underlying": "NIFTY"}})

        self.assertFalse(result["allowed"])
        self.assertEqual(result["governor"]["state"], "BLOCKED_BY_DATA")
        self.assertIn("NIFTY spot quote unavailable from Paper Data Zerodha.", result["blockers"])
        self.assertIn("NSE:NIFTY 50", result["next_action"])

    def test_missing_locked_option_quote_blocks_contract_lock(self):
        returned = {"NFO:NIFTY26JUN22600CE"}
        client = FakeOptionsZerodha(spot=22540, returned_option_keys=returned)
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda _mode: client)

            result = service.evaluate({
                "mode": MODE_PAPER,
                "settings": {"underlying": "NIFTY", "atm_scan_strike_span": 0, "market_cue_alignment_required": False},
            })

        self.assertFalse(result["allowed"])
        self.assertIn("Quote missing for selected contract.", result["blockers"])
        self.assertTrue(any(call == ["NFO:NIFTY26JUN22600CE"] for call in client.quote_calls))
        self.assertTrue(any(call == ["NFO:NIFTY26JUN22500PE"] for call in client.quote_calls))


if __name__ == "__main__":
    unittest.main()
