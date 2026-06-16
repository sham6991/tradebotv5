import unittest

from options_auto.data.live_quote_provider import LiveQuoteProvider
from options_auto.data.market_depth_controller import DEPTH_DEGRADED, DEPTH_FULL, DEPTH_INVALID, DEPTH_NONE, DEPTH_STALE, DEPTH_TOP, MarketDepthController
from options_auto.data.quote_identity_resolver import QuoteIdentityResolver


class OptionsAutoDepthAndIdentityTests(unittest.TestCase):
    def test_full_websocket_depth_normalizes_to_full_depth_ok(self):
        quote = LiveQuoteProvider().normalize_quote("NFO:NIFTY26JUN23500CE", {
            "quote_key": "NFO:NIFTY26JUN23500CE",
            "last_price": 100,
            "open_interest": 900000,
            "source": "websocket_full",
            "depth": {
                "buy": [{"price": 99.9, "quantity": 1200, "orders": 4}],
                "sell": [{"price": 100.1, "quantity": 1000, "orders": 5}],
            },
        })
        self.assertEqual(quote["oi"], 900000)
        self.assertEqual(quote["open_interest"], 900000)
        self.assertEqual(quote["bid"], 99.9)
        self.assertEqual(quote["ask"], 100.1)
        self.assertEqual(quote["depth_health"]["state"], DEPTH_FULL)
        self.assertEqual(quote["timestamp_source"], "local_received_at")

    def test_snapshot_missing_timestamp_is_not_fake_fresh(self):
        quote = LiveQuoteProvider().normalize_quote("NFO:NIFTY26JUN23500CE", {
            "quote_key": "NFO:NIFTY26JUN23500CE",
            "last_price": 100,
            "source": "zerodha_snapshot_quote",
            "depth": {"buy": [{"price": 99, "quantity": 1}], "sell": [{"price": 101, "quantity": 1}]},
        })
        self.assertFalse(quote["age_known"])
        self.assertIsNone(quote["age_seconds"])
        real_depth = MarketDepthController().evaluate(quote, {"final_validation_quote_stale_seconds": 5}, stage="real_final")
        self.assertFalse(real_depth["allowed_for_real_final"])
        self.assertIn("Unknown quote age", "; ".join(real_depth["blockers"]))

    def test_depth_controller_states(self):
        controller = MarketDepthController()
        full = {"ltp": 100, "age_known": True, "age_seconds": 1, "depth": {"buy": [{"price": 99, "quantity": 10}], "sell": [{"price": 101, "quantity": 10}]}}
        top = {"ltp": 100, "age_known": True, "age_seconds": 1, "bid": 99, "ask": 101, "bid_qty": 10}
        degraded = {"ltp": 100, "age_known": True, "age_seconds": 1, "bid": 99, "ask": 101}
        none = {"ltp": 100, "age_known": True, "age_seconds": 1}
        stale = {**full, "age_seconds": 9}
        invalid = {"ltp": 100, "age_known": True, "age_seconds": 1, "bid": 101, "ask": 99}
        self.assertEqual(controller.evaluate(full, {"max_quote_age_seconds": 3})["state"], DEPTH_FULL)
        self.assertEqual(controller.evaluate(top, {"max_quote_age_seconds": 3})["state"], DEPTH_TOP)
        self.assertEqual(controller.evaluate(degraded, {"max_quote_age_seconds": 3})["state"], DEPTH_DEGRADED)
        self.assertEqual(controller.evaluate(none, {"max_quote_age_seconds": 3})["state"], DEPTH_NONE)
        self.assertEqual(controller.evaluate(stale, {"max_quote_age_seconds": 3})["state"], DEPTH_STALE)
        self.assertEqual(controller.evaluate(invalid, {"max_quote_age_seconds": 3})["state"], DEPTH_INVALID)

    def test_quote_identity_resolver_prefers_exchange_symbol_and_prevents_role_mismatch(self):
        resolver = QuoteIdentityResolver()
        instrument = {"exchange": "NFO", "tradingsymbol": "NIFTY26JUN23500CE", "instrument_token": 123, "instrument_type": "CE"}
        quotes = {
            "123": {"tradingsymbol": "NIFTY26JUN23500PE", "quote_key": "NFO:NIFTY26JUN23500PE"},
            "NFO:NIFTY26JUN23500CE": {"tradingsymbol": "NIFTY26JUN23500CE", "quote_key": "NFO:NIFTY26JUN23500CE", "ltp": 100},
        }
        resolved = resolver.resolve("CE", instrument, quotes)
        self.assertTrue(resolved["resolved"])
        self.assertEqual(resolved["resolved_by"], "exchange_tradingsymbol")
        self.assertEqual(resolved["quote"]["ltp"], 100)

        mismatch = resolver.resolve("CE", {"exchange": "NFO", "tradingsymbol": "NIFTY26JUN23500CE", "instrument_token": 123}, {"123": quotes["123"]})
        self.assertFalse(mismatch["resolved"])
        self.assertTrue(mismatch["mismatch"])


if __name__ == "__main__":
    unittest.main()
