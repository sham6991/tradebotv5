import tempfile
import unittest
from datetime import date

import pandas as pd

from options_auto.terminal_service import OptionsAutoTerminalService


def _index_rows(count=60):
    return [
        {
            "datetime": f"2026-06-04 10:{i % 60:02d}:00",
            "open": 22480 + i * 3,
            "high": 22505 + i * 3,
            "low": 22470 + i * 3,
            "close": 22490 + i * 4,
            "volume": 10000 + i * 500,
        }
        for i in range(count)
    ]


def _option_rows(prefix: str, token: int, option_type: str, count=60):
    direction = 1 if option_type == "CE" else -0.4
    return [
        {
            "datetime": f"2026-06-04 10:{i % 60:02d}:00",
            "open": 120 + i * direction,
            "high": 127 + i * direction,
            "low": 118 + i * direction,
            "close": 124 + i * direction,
            "volume": 100000 + i * 1000,
        }
        for i in range(count)
    ]


class FakePaperZerodha:
    def __init__(self):
        self.calls = []
        self.rows = {
            256265: _index_rows(),
            1001: _option_rows("NIFTY26JUN22500CE", 1001, "CE"),
            1002: _option_rows("NIFTY26JUN22500PE", 1002, "PE"),
            1003: _option_rows("NIFTY26JUN22400PE", 1003, "PE"),
        }

    def instruments(self, exchange=None):
        if exchange == "NSE":
            return [{"tradingsymbol": "NIFTY 50", "name": "NIFTY 50", "instrument_token": 256265}]
        if exchange == "NFO":
            return [
                {"tradingsymbol": "NIFTY26JUN22500CE", "name": "NIFTY", "instrument_token": 1001, "instrument_type": "CE", "segment": "NFO-OPT", "strike": 22500, "expiry": date(2026, 6, 25), "lot_size": 50, "tick_size": 0.05, "exchange": "NFO"},
                {"tradingsymbol": "NIFTY26JUN22500PE", "name": "NIFTY", "instrument_token": 1002, "instrument_type": "PE", "segment": "NFO-OPT", "strike": 22500, "expiry": date(2026, 6, 25), "lot_size": 50, "tick_size": 0.05, "exchange": "NFO"},
                {"tradingsymbol": "NIFTY26JUN22400PE", "name": "NIFTY", "instrument_token": 1003, "instrument_type": "PE", "segment": "NFO-OPT", "strike": 22400, "expiry": date(2026, 6, 25), "lot_size": 50, "tick_size": 0.05, "exchange": "NFO"},
            ]
        return []

    def get_nifty50_token(self):
        return 256265

    def historical_candles(self, instrument_token, from_dt, to_dt, interval="3minute"):
        self.calls.append((int(instrument_token), interval, from_dt, to_dt))
        return pd.DataFrame(self.rows[int(instrument_token)])


class WideFallbackZerodha:
    def __init__(self):
        self.rows = {}
        self._instruments = []
        for offset, strike in enumerate(range(22500, 23300, 100), start=1):
            token = 9000 + offset
            premium = 500 if strike <= 23000 else 50
            self.rows[token] = _option_rows(f"NIFTY26JUN{strike}CE", token, "CE")
            for row in self.rows[token]:
                row["open"] = premium
                row["high"] = premium + 5
                row["low"] = premium - 2
                row["close"] = premium
            self._instruments.append({
                "tradingsymbol": f"NIFTY26JUN{strike}CE",
                "name": "NIFTY",
                "instrument_token": token,
                "instrument_type": "CE",
                "segment": "NFO-OPT",
                "strike": strike,
                "expiry": date(2026, 6, 25),
                "lot_size": 50,
                "tick_size": 0.05,
                "exchange": "NFO",
            })

    def instruments(self, exchange=None):
        return list(self._instruments) if exchange == "NFO" else []

    def historical_candles(self, instrument_token, from_dt, to_dt, interval="3minute"):
        return pd.DataFrame(self.rows[int(instrument_token)])


class OptionsAutoBacktestHistoryTests(unittest.TestCase):
    def test_backtest_fetches_zerodha_history_and_generates_trades(self):
        client = FakePaperZerodha()
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda _mode: client)

            result = service.backtest({
                "data_source": "zerodha_historical",
                "trade_date": "2026-06-04",
                "underlying": "NIFTY",
                "interval": "3minute",
                "settings": {
                    "buy_score_threshold": 20,
                    "market_cue_alignment_required": False,
                    "premium_expansion_required": False,
                    "avoid_first_minutes": 0,
                    "cooldown_after_trade_seconds": 0,
                    "max_capital_per_trade_pct": 100,
                    "max_risk_per_trade_pct": 10,
                    "paper_starting_balance": 20000,
                },
            })

        self.assertEqual(result["data_source"], "zerodha_historical")
        self.assertEqual(result["source_metadata"]["atm_strike"], 22500)
        self.assertEqual(result["source_metadata"]["major_strike_step"], 100)
        self.assertEqual(result["contract_lock"]["ce"]["strike"], 22500)
        self.assertEqual(result["contract_lock"]["pe"]["strike"], 22400)
        self.assertEqual(result["contract_lock"]["ce"]["quantity"], 50)
        self.assertEqual(result["option_frames"], 2)
        self.assertGreaterEqual(len(client.calls), 3)
        self.assertTrue([row for row in result["decisions"] if row["decision"] == "ENTRY"])
        self.assertTrue(result["trades"])
        first_entry = next(row for row in result["decisions"] if row["decision"] == "ENTRY")
        self.assertEqual(first_entry["tradingsymbol"], "NIFTY26JUN22500CE")

    def test_backtest_history_requires_paper_connection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            with self.assertRaisesRegex(ValueError, "Connect Paper Data Zerodha"):
                service.backtest({"data_source": "zerodha_historical", "trade_date": "2026-06-04"})

    def test_backtest_fallback_scans_farther_major_strikes_when_first_five_are_unaffordable(self):
        client = WideFallbackZerodha()
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda _mode: client)
            result = service._select_backtest_major_contract(
                client=client,
                underlying="NIFTY",
                exchange="NFO",
                expiry="2026-06-25",
                option_type="CE",
                initial_strike=22500,
                hop_direction=1,
                major_step=100,
                lots=1,
                available_margin=5000,
                max_hops=5,
                settings={"estimated_charges_per_lot": 0, "capital_buffer_pct": 0},
                from_dt=pd.Timestamp("2026-06-04 09:15").to_pydatetime(),
                to_dt=pd.Timestamp("2026-06-04 15:30").to_pydatetime(),
                interval="3minute",
            )

        self.assertEqual(result["selected"]["strike"], 23100)
        self.assertTrue(result["fallback_used"])
        self.assertTrue(result["selected"]["fallback_selected"])
        self.assertNotIn(22550, [row.get("strike") for row in result["hop_history"]])


if __name__ == "__main__":
    unittest.main()
