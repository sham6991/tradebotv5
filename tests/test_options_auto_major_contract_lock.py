import tempfile
import unittest
from datetime import date

from options_auto.config.options_auto_defaults import default_settings
from options_auto.constants import MODE_PAPER
from options_auto.data.major_strike_selector import select_major_strikes_for_spot
from options_auto.data.options_instrument_cache import get_contract_lot_size
from options_auto.intelligence.strike_selector import StrikeSelector
from options_auto.terminal_service import OptionsAutoTerminalService


class FakeMajorStrikeClient:
    def instruments(self, exchange=None):
        if exchange != "NFO":
            return []
        rows = []
        for token, strike, option_type, lot_size in (
            (1, 23400, "CE", 65),
            (2, 23500, "CE", 65),
            (3, 23300, "PE", 65),
            (4, 23200, "PE", 65),
            (5, 23350, "CE", 65),
        ):
            rows.append({
                "tradingsymbol": f"NIFTY26JUN{strike}{option_type}",
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_token": token,
                "instrument_type": option_type,
                "strike": strike,
                "expiry": date(2026, 6, 25),
                "lot_size": lot_size,
                "tick_size": 0.05,
            })
        return rows

    def quote(self, keys):
        prices = {
            "NFO:NIFTY26JUN23400CE": 450,
            "NFO:NIFTY26JUN23500CE": 270,
            "NFO:NIFTY26JUN23300PE": 430,
            "NFO:NIFTY26JUN23200PE": 240,
            "NFO:NIFTY26JUN23350CE": 10,
        }
        return {
            key: {
                "last_price": price,
                "bid": price - 0.05,
                "ask": price,
                "bid_qty": 1,
                "ask_qty": 1,
                "volume": 0,
                "oi": 0,
            }
            for key, price in prices.items()
            if key in set(keys or [])
        }


class OptionsAutoMajorContractLockTests(unittest.TestCase):
    def test_major_strike_examples_avoid_in_between_strikes(self):
        examples = {
            23350: (23400, 23300),
            23359: (23400, 23300),
            23301: (23400, 23300),
            23401: (23500, 23400),
            23300: (23400, 23200),
        }
        for spot, expected in examples.items():
            with self.subTest(spot=spot):
                result = select_major_strikes_for_spot(spot, 100)
                self.assertEqual((result["ce_strike"], result["pe_strike"]), expected)
                self.assertNotIn(result["ce_strike"], {23350, 23450, 23250})
                self.assertNotIn(result["pe_strike"], {23350, 23450, 23250})

    def test_lots_use_fetched_zerodha_lot_size(self):
        self.assertEqual(get_contract_lot_size({"lot_size": 65}), 65)
        self.assertEqual(2 * get_contract_lot_size({"lot_size": 65}), 130)
        self.assertEqual(get_contract_lot_size({"lot_size": 0}), 0)
        self.assertEqual(get_contract_lot_size({}), 0)

    def test_margin_hopping_uses_major_strikes_only_and_locks_two_contracts(self):
        client = FakeMajorStrikeClient()
        settings = default_settings()
        settings.update({
            "underlying": "NIFTY",
            "number_of_lots": 1,
            "major_strike_step": 100,
            "max_hop_strikes": 5,
            "estimated_charges_per_lot": 0,
            "capital_buffer_pct": 0,
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda _mode: client)
            result = service._select_and_lock_major_contracts(
                client=client,
                mode=MODE_PAPER,
                underlying="NIFTY",
                exchange="NFO",
                expiry="2026-06-25",
                spot_value=23350,
                settings=settings,
                source="zerodha_paper_data",
            )

        self.assertTrue(result["allowed"])
        lock = result["lock"]
        self.assertEqual(lock["ce"]["strike"], 23500)
        self.assertEqual(lock["pe"]["strike"], 23200)
        self.assertEqual(lock["ce"]["quantity"], 65)
        self.assertEqual(lock["pe"]["quantity"], 65)
        self.assertEqual(lock["ce"]["hop_count"], 1)
        self.assertEqual(lock["pe"]["hop_count"], 1)
        self.assertNotIn(23350, [row.get("strike") for row in lock["margin_hop_history"]])

    def test_user_lots_must_be_positive(self):
        client = FakeMajorStrikeClient()
        settings = default_settings()
        settings["number_of_lots"] = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda _mode: client)
            result = service._select_and_lock_major_contracts(
                client=client,
                mode=MODE_PAPER,
                underlying="NIFTY",
                exchange="NFO",
                expiry="2026-06-25",
                spot_value=23350,
                settings=settings,
                source="zerodha_paper_data",
            )
        self.assertFalse(result["allowed"])
        self.assertIn("Lots must be greater than zero.", result["blockers"])

    def test_strict_liquidity_filter_is_optional(self):
        instrument = {
            "name": "NIFTY",
            "tradingsymbol": "NIFTY26JUN23400CE",
            "instrument_token": "1",
            "instrument_type": "CE",
            "strike": 23400,
            "expiry": "2026-06-25",
            "lot_size": 65,
        }
        quote = {"ltp": 100, "bid": 99.95, "ask": 100.05, "bid_qty": 1, "ask_qty": 1, "volume": 0, "oi": 0, "momentum_score": 90}
        relaxed = StrikeSelector().select([instrument], {"1": quote}, 23350, "CE", {"buy_score_threshold": 0, "strict_liquidity_filter": False, "premium_expansion_required": False}, {})
        strict = StrikeSelector().select([instrument], {"1": quote}, 23350, "CE", {"buy_score_threshold": 0, "strict_liquidity_filter": True, "premium_expansion_required": False}, {})

        self.assertNotIn("Liquidity score too low.", relaxed.candidates[0]["blockers"])
        self.assertIn("Liquidity score too low.", strict.candidates[0]["blockers"])


if __name__ == "__main__":
    unittest.main()
