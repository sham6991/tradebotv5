import tempfile
import unittest
from datetime import datetime, timedelta

from intraday.data_source_policy import IntradayDataSource, resolve_intraday_data_source
from intraday.models import IntradaySettings
from intraday.session_manager import IntradaySessionManager


SYMBOLS = ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"]


class FakeKite:
    def quote(self, keys):
        return {
            key: {
                "last_price": 150.0,
                "depth": {"buy": [{"price": 149.95, "quantity": 10000}], "sell": [{"price": 150.05, "quantity": 10000}]},
                "timestamp": "2026-06-05T10:00:00",
            }
            for key in keys
        }


class FakeZerodhaDataClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.kite = FakeKite()
        self.historical_calls = 0
        self.instrument_calls = 0

    def instruments(self, exchange="NSE"):
        self.instrument_calls += 1
        return [
            {"exchange": exchange or "NSE", "tradingsymbol": symbol, "instrument_token": index + 1, "tick_size": 0.05, "segment": exchange or "NSE"}
            for index, symbol in enumerate(SYMBOLS)
        ]

    def historical_candles(self, instrument_token, from_time, to_time, interval="minute"):
        self.historical_calls += 1
        if self.fail:
            raise RuntimeError("network down")
        rows = []
        start = datetime(2026, 6, 5, 9, 15)
        for index in range(35):
            base = 100 + int(instrument_token) + index
            rows.append({
                "date": start + timedelta(minutes=index),
                "open": base - 0.2,
                "high": base + 1,
                "low": base - 1,
                "close": base + 0.4,
                "volume": 10000 + index,
            })
        return rows


def upload_fii_dii(manager):
    manager.upload_fii_dii_csv({
        "csv_text": "Date,Category,Buy Value,Sell Value,Net Value\n2026-06-02,FII/FPI,1000,1300,-300\n2026-06-02,DII,1400,900,500\n"
    })


def payload(**overrides):
    row = {
        "mode": "PAPER",
        "stocks": SYMBOLS,
        "minimum_entry_score": 1,
        "minimum_risk_reward": 1.1,
        "ask_permission_before_entry": True,
    }
    row.update(overrides)
    return row


class IntradayDataSourcePolicyTests(unittest.TestCase):
    def test_policy_paper_with_paper_client_uses_zerodha_paper_data(self):
        settings = IntradaySettings.from_payload(payload())
        policy = resolve_intraday_data_source("PAPER", {}, paper_connected=True, live_connected=False, settings=settings)

        self.assertTrue(policy["allowed"])
        self.assertTrue(policy["requires_fetch"])
        self.assertEqual(policy["source"], IntradayDataSource.ZERODHA_PAPER)

    def test_policy_paper_without_client_blocks_by_default(self):
        settings = IntradaySettings.from_payload(payload())
        policy = resolve_intraday_data_source("PAPER", {}, paper_connected=False, live_connected=False, settings=settings)

        self.assertFalse(policy["allowed"])
        self.assertEqual(policy["source"], IntradayDataSource.UNAVAILABLE)
        self.assertIn("Connect Paper Data Zerodha", policy["blockers"][0])

    def test_policy_paper_without_client_allows_explicit_simulated_fallback(self):
        settings = IntradaySettings.from_payload(payload(allow_simulated_fallback=True, require_live_data_for_paper=False))
        policy = resolve_intraday_data_source("PAPER", {}, paper_connected=False, live_connected=False, settings=settings)

        self.assertTrue(policy["allowed"])
        self.assertEqual(policy["source"], IntradayDataSource.SIMULATED_FALLBACK)
        self.assertEqual(policy["status"], "WARNING")

    def test_policy_real_without_client_blocks_and_real_never_simulates(self):
        settings = IntradaySettings.from_payload(payload(mode="REAL", confirm_real_mode=True, allow_simulated_fallback=True))
        policy = resolve_intraday_data_source("REAL", {}, paper_connected=True, live_connected=False, settings=settings)

        self.assertFalse(policy["allowed"])
        self.assertEqual(policy["source"], IntradayDataSource.UNAVAILABLE)
        self.assertFalse(policy["allow_simulated"])

    def test_provided_market_data_is_blocked_for_active_real(self):
        settings = IntradaySettings.from_payload(payload(mode="REAL", confirm_real_mode=True))
        policy = resolve_intraday_data_source("REAL", {"market_data": {"INFY": {}}}, paper_connected=False, live_connected=True, settings=settings)

        self.assertFalse(policy["allowed"])
        self.assertIn("provided test data is blocked", policy["blockers"][0])

    def test_paper_session_with_client_fetches_zerodha_paper_data(self):
        client = FakeZerodhaDataClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir, zerodha_client_provider=lambda mode: client if str(mode).upper() in {"PAPER"} else None)
            upload_fii_dii(manager)
            manager.start_session(payload())
            status = manager.evaluate({"market_trend": "Bullish"})

            self.assertEqual(status["data_source_status"]["source"], IntradayDataSource.ZERODHA_PAPER)
            self.assertGreater(client.historical_calls, 0)
            self.assertTrue(status["snapshots"])
            self.assertEqual(status["snapshots"][0]["data_source"], IntradayDataSource.ZERODHA_PAPER)
            self.assertEqual(status["snapshots"][0]["data_mode"], "candle_polling")

    def test_paper_fetch_error_blocks_when_fallback_disabled(self):
        client = FakeZerodhaDataClient(fail=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir, zerodha_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            upload_fii_dii(manager)
            manager.start_session(payload())

            with self.assertRaisesRegex(ValueError, "Simulated fallback is disabled"):
                manager.evaluate({"market_trend": "Bullish"})

    def test_paper_fetch_error_uses_fallback_only_when_enabled(self):
        client = FakeZerodhaDataClient(fail=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir, zerodha_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            upload_fii_dii(manager)
            manager.start_session(payload(allow_simulated_fallback=True, require_live_data_for_paper=False))
            status = manager.evaluate({"market_trend": "Bullish"})

            self.assertEqual(status["data_source_status"]["source"], IntradayDataSource.SIMULATED_FALLBACK)
            self.assertEqual(status["data_source_status"]["status"], "WARNING")
            self.assertEqual(status["snapshots"][0]["data_source"], IntradayDataSource.SIMULATED_FALLBACK)

    def test_status_payload_uses_cached_funds_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            upload_fii_dii(manager)
            manager.start_session(payload(allow_simulated_fallback=True, require_live_data_for_paper=False))

            class CountingBroker:
                calls = 0

                def get_funds(self):
                    self.calls += 1
                    raise AssertionError("status should not fetch funds")

            broker = CountingBroker()
            manager.broker = broker
            status = manager.status_payload()

            self.assertIn("funds", status)
            self.assertEqual(broker.calls, 0)


if __name__ == "__main__":
    unittest.main()
