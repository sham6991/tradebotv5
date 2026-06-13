import tempfile
import unittest
from pathlib import Path

from options_auto.testing.fake_zerodha_replay import CsvReplayZerodhaClient


class OptionsAutoFakeReplayTests(unittest.TestCase):
    def test_csv_replay_client_emits_full_depth_ticks_and_quotes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            index = root / "nifty.csv"
            ce = root / "NIFTY26JUN22500CE.csv"
            pe = root / "NIFTY26JUN22500PE.csv"
            index.write_text("timestamp,last_price,volume\n2026-06-04 09:15:00,22520,1000\n", encoding="utf-8")
            ce.write_text(
                "timestamp,tradingsymbol,instrument_token,exchange,instrument_type,strike,expiry,lot_size,last_price,bid,ask,bid_qty,ask_qty,volume,oi\n"
                "2026-06-04 09:15:00,NIFTY26JUN22500CE,2001,NFO,CE,22500,2026-06-25,50,110,109.95,110.05,1200,1300,90000,800000\n",
                encoding="utf-8",
            )
            pe.write_text(
                "timestamp,tradingsymbol,instrument_token,exchange,instrument_type,strike,expiry,lot_size,last_price,bid,ask,bid_qty,ask_qty,volume,oi\n"
                "2026-06-04 09:15:00,NIFTY26JUN22500PE,2002,NFO,PE,22500,2026-06-25,50,95,94.95,95.05,1500,1400,85000,760000\n",
                encoding="utf-8",
            )
            client = CsvReplayZerodhaClient(index_csv=index, option_csvs=[ce, pe])
            instruments = client.instruments("NFO")
            received = []

            client.start_named_ticker(
                "options_auto",
                [256265, 2001, 2002],
                lambda ticks: received.extend(ticks),
            )
            emitted = client.emit_next()
            quotes = client.quote(["NFO:NIFTY26JUN22500CE"])

            self.assertEqual(len(emitted), 3)
            self.assertEqual(len(received), 3)
            self.assertEqual(instruments[0]["tradingsymbol"], "NIFTY26JUN22500CE")
            self.assertEqual(received[1]["depth"]["buy"][0]["price"], 109.95)
            self.assertEqual(quotes["NFO:NIFTY26JUN22500CE"]["bid_qty"], 1200.0)


if __name__ == "__main__":
    unittest.main()
