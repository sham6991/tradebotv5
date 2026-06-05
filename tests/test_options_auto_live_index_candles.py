import unittest
from datetime import date, datetime, time as dt_time, timedelta

from options_auto.data.live_index_candles import LiveIndexCandleStore


class FakeHistoryClient:
    def __init__(self):
        self.calls = []

    def historical_candles(self, instrument_token, from_dt, to_dt, interval="3minute"):
        self.calls.append((instrument_token, from_dt, to_dt, interval))
        today_start = datetime.combine(date.today(), dt_time(9, 15))
        old_start = today_start - timedelta(days=1)
        return [
            {"datetime": old_start, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10},
            {"datetime": today_start, "open": 22400, "high": 22420, "low": 22390, "close": 22410, "volume": 1000},
            {"datetime": today_start + timedelta(minutes=3), "open": 22410, "high": 22440, "low": 22400, "close": 22430, "volume": 1300},
        ]


class OptionsAutoLiveIndexCandlesTests(unittest.TestCase):
    def test_builds_live_tick_candle_and_backfills_only_current_day(self):
        client = FakeHistoryClient()
        store = LiveIndexCandleStore(max_candles=20)
        tick_time = datetime.combine(date.today(), dt_time(9, 21, 12))

        result = store.update(
            client=client,
            instrument_token=256265,
            underlying="NIFTY",
            mode="PAPER",
            interval="3minute",
            spot=22450,
            timestamp=tick_time,
            volume=1600,
        )

        self.assertEqual(result["source"], "zerodha_live_tick_candles")
        self.assertEqual(result["backfill"]["rows"], 2)
        self.assertEqual(result["candle_count"], 3)
        self.assertEqual(result["latest_candle"]["close"], 22450)
        self.assertTrue(all(str(row["datetime"]).startswith(date.today().isoformat()) for row in result["candles"]))
        self.assertEqual(client.calls[0][3], "3minute")

    def test_stop_resets_active_builder_without_erasing_backfilled_history(self):
        client = FakeHistoryClient()
        store = LiveIndexCandleStore(max_candles=20)
        tick_time = datetime.combine(date.today(), dt_time(9, 21, 12))

        first = store.update(
            client=client,
            instrument_token=256265,
            underlying="NIFTY",
            mode="REAL",
            interval="3minute",
            spot=22450,
            timestamp=tick_time,
        )
        store.stop()
        restarted = store.update(
            client=client,
            instrument_token=256265,
            underlying="NIFTY",
            mode="REAL",
            interval="3minute",
            spot=22460,
            timestamp=tick_time + timedelta(minutes=1),
        )

        self.assertGreaterEqual(first["candle_count"], 3)
        self.assertGreaterEqual(restarted["candle_count"], 3)
        self.assertEqual(restarted["latest_candle"]["close"], 22460)


if __name__ == "__main__":
    unittest.main()
