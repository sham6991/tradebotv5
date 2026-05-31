import unittest

from market_cue.scoring import calculate_confidence, generate_zones, score_market_cues
from market_cue.validator import validate_market_data


class MarketCueScoringTests(unittest.TestCase):
    def sample_raw(self):
        return {
            "indian_market": {
                "NIFTY 50": {"value": 22600, "previous_close": 22500, "percent_change": 0.44, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "BANK NIFTY": {"value": 48500, "previous_close": 48250, "percent_change": 0.52, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "India VIX": {"value": 13, "previous_close": 14, "percent_change": -7.1, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
            },
            "global_market": {
                "Nasdaq Futures": {"percent_change": 0.35, "value": 19000, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "S&P Futures": {"percent_change": 0.2, "value": 5400, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "Dow Jones": {"percent_change": 0.1, "value": 39000, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "Nikkei 225": {"percent_change": 0.1, "value": 39000, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "Hang Seng": {"percent_change": 0.1, "value": 19000, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "Shanghai": {"percent_change": -0.1, "value": 3100, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "FTSE 100": {"percent_change": 0.1, "value": 8300, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "DAX": {"percent_change": 0.1, "value": 18000, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "CAC 40": {"percent_change": 0.1, "value": 7900, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "WTI Crude": {"percent_change": -1.2, "value": 78, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "DXY": {"percent_change": -0.1, "value": 103, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "USD/INR": {"percent_change": -0.1, "value": 83, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "US 10Y Yield": {"percent_change": -0.2, "value": 4.2, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
            },
            "institutional_flow": {"fii_net": 3500, "dii_net": 2100, "status": "OK", "data_date": "2099-01-01", "fetch_mode": "auto_download"},
        }

    def test_scoring_engine_classifies_strong_bullish(self):
        raw = self.sample_raw()
        validation = validate_market_data(raw)
        scoring = score_market_cues(raw, validation)

        self.assertEqual(scoring["bias"], "Strong Bullish")
        self.assertGreaterEqual(scoring["final_score"], 7)

    def test_confidence_penalizes_missing_fii_dii(self):
        raw = self.sample_raw()
        raw["institutional_flow"]["fii_net"] = None
        raw["institutional_flow"]["dii_net"] = None
        validation = validate_market_data(raw)
        scoring = score_market_cues(raw, validation)

        self.assertLessEqual(scoring["confidence"], 80)

    def test_zone_generation_rounds_nifty_and_banknifty(self):
        nifty = generate_zones(22500, "NIFTY")
        bank = generate_zones(48250, "BANKNIFTY")

        self.assertEqual(nifty["resistance_1"] % 5, 0)
        self.assertEqual(bank["resistance_1"] % 10, 0)

    def test_missing_indian_data_reduces_reliability(self):
        raw = self.sample_raw()
        raw["indian_market"]["NIFTY 50"]["status"] = "FAILED"
        raw["indian_market"]["NIFTY 50"]["value"] = None
        validation = validate_market_data(raw)

        self.assertEqual(validation["data_reliability"], "Poor")

    def test_historical_kite_fallback_reduces_indian_score(self):
        raw = self.sample_raw()
        raw["indian_market"]["NIFTY 50"]["ltp_source"] = "historical_fallback"
        validation = validate_market_data(raw)
        scoring = score_market_cues(raw, validation)
        nifty = next(item for item in scoring["contributions"] if item["name"] == "NIFTY 50")

        self.assertEqual(nifty["score"], 1.5)
        self.assertIn("historical fallback", nifty["note"])
        self.assertEqual(validation["data_reliability"], "Partial")


if __name__ == "__main__":
    unittest.main()
