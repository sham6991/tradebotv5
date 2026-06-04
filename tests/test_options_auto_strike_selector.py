import unittest

from options_auto.intelligence.strike_selector import StrikeSelector


class OptionsAutoStrikeSelectorTests(unittest.TestCase):
    def setUp(self):
        self.instruments = [
            {"name": "NIFTY", "tradingsymbol": "NIFTY26JUN22500CE", "instrument_token": "1", "instrument_type": "CE", "strike": 22500, "lot_size": 50},
            {"name": "NIFTY", "tradingsymbol": "NIFTY26JUN22600CE", "instrument_token": "2", "instrument_type": "CE", "strike": 22600, "lot_size": 50},
            {"name": "NIFTY", "tradingsymbol": "NIFTY26JUN22500PE", "instrument_token": "3", "instrument_type": "PE", "strike": 22500, "lot_size": 50},
        ]
        self.quotes = {
            "1": {"ltp": 140, "bid": 139.95, "ask": 140.05, "bid_qty": 1500, "ask_qty": 1400, "volume": 90000, "oi": 950000, "momentum_score": 75},
            "2": {"ltp": 98, "bid": 96, "ask": 100, "bid_qty": 1, "ask_qty": 1, "volume": 100, "oi": 100, "momentum_score": 40},
            "3": {"ltp": 125, "bid": 124.9, "ask": 125.1, "bid_qty": 1200, "ask_qty": 1200, "volume": 85000, "oi": 900000, "momentum_score": 35},
        }

    def test_selects_liquid_ce_above_threshold(self):
        selector = StrikeSelector()
        result = selector.select(
            self.instruments,
            self.quotes,
            22520,
            "CE",
            {"underlying": "NIFTY", "paper_starting_balance": 20000, "buy_score_threshold": 50, "max_spread_pct": 0.6, "min_depth_qty": 1},
            {"regime_alignment": 80, "market_cue_score": 75, "time_of_day_score": 70},
        )

        self.assertEqual(result.selected["tradingsymbol"], "NIFTY26JUN22500CE")
        self.assertGreaterEqual(result.score, 50)

    def test_blocks_wide_spread_and_low_depth_contract(self):
        selector = StrikeSelector()
        result = selector.select(
            [self.instruments[1]],
            self.quotes,
            22520,
            "CE",
            {"underlying": "NIFTY", "paper_starting_balance": 20000, "buy_score_threshold": 50, "max_spread_pct": 0.6, "min_depth_qty": 10},
            {"regime_alignment": 80, "market_cue_score": 75, "time_of_day_score": 70},
        )

        self.assertIsNone(result.selected)
        self.assertTrue(result.blockers)


if __name__ == "__main__":
    unittest.main()

