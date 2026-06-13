import unittest

from options_auto.intelligence.trade_candidate_validator import TradeCandidateValidator


def contract(**overrides):
    row = {
        "tradingsymbol": "NIFTY26JUN22500CE",
        "instrument_token": 1001,
        "option_type": "CE",
        "lot_size": 50,
        "ltp": 100.0,
        "bid": 99.95,
        "ask": 100.05,
        "spread_pct": 0.1,
        "bid_qty": 1500,
        "ask_qty": 1500,
        "total_depth": 3000,
        "liquidity_score": 80,
    }
    row.update(overrides)
    return row


class OptionsAutoTradeCandidateValidatorTests(unittest.TestCase):
    def validate(self, **overrides):
        payload = {
            "selected_side": "CE",
            "selected_contract": contract(),
            "settings": {"buy_score_threshold": 50, "max_spread_pct": 0.6, "min_depth_qty": 1},
            "data_quality": {"allowed": True, "blockers": [], "warnings": []},
            "theta_premium_risk": {"allowed": True, "blockers": [], "warnings": []},
            "trade_score": {"score": 72},
            "entry_timing": {"allowed": True, "blockers": [], "warnings": []},
            "effective_score_threshold": 50,
        }
        payload.update(overrides)
        return TradeCandidateValidator().validate(**payload).to_dict()

    def test_missing_selected_contract_blocks_safely(self):
        result = self.validate(selected_contract={}, selection_blockers=["No matching CE/PE contracts found."])

        self.assertFalse(result["allowed"])
        self.assertEqual(result["stage"], "NO_CONTRACT")
        self.assertEqual(result["blockers"], ["No matching CE/PE contracts found."])

    def test_stale_quote_blocker_once(self):
        result = self.validate(data_quality={"allowed": False, "blockers": ["Quote is stale.", "Quote is stale."], "warnings": []})

        self.assertFalse(result["allowed"])
        self.assertEqual(result["stage"], "QUOTE_INVALID")
        self.assertEqual(result["blockers"].count("Quote is stale."), 1)

    def test_spread_too_wide_blocker_once(self):
        result = self.validate(
            selected_contract=contract(spread_pct=1.4),
            data_quality={"allowed": False, "blockers": ["Quote spread is too wide."], "warnings": []},
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["stage"], "LIQUIDITY_BLOCKED")
        self.assertEqual(result["blockers"], ["Spread too wide."])

    def test_depth_too_low_blocks(self):
        result = self.validate(selected_contract=contract(total_depth=0, bid_qty=0, ask_qty=0))

        self.assertFalse(result["allowed"])
        self.assertEqual(result["stage"], "LIQUIDITY_BLOCKED")
        self.assertIn("Depth too low.", result["blockers"])

    def test_theta_score_and_timing_stages_are_separate(self):
        theta = self.validate(theta_premium_risk={"allowed": False, "blockers": ["Theta risk too high."], "warnings": []})
        score = self.validate(trade_score={"score": 40}, effective_score_threshold=50)
        timing = self.validate(entry_timing={"allowed": False, "blockers": ["Entry is chasing premium."], "warnings": []})

        self.assertEqual(theta["stage"], "THETA_BLOCKED")
        self.assertEqual(score["stage"], "SCORE_BLOCKED")
        self.assertEqual(timing["stage"], "TIMING_BLOCKED")

    def test_valid_candidate_passes(self):
        result = self.validate()

        self.assertTrue(result["allowed"])
        self.assertEqual(result["stage"], "VALID")
        self.assertEqual(result["blockers"], [])


if __name__ == "__main__":
    unittest.main()
