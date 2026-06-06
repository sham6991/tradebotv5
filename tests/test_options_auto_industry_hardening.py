import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

from options_auto.data.options_feed_health import DATA_STALE, OptionsFeedHealth
from options_auto.data.options_live_feed import OptionsLiveFeed
from options_auto.data.options_quote_provider import OptionsQuoteProvider
from options_auto.data.persistent_instrument_cache import PersistentInstrumentCache
from options_auto.execution.blackbox_recorder import BlackboxRecorder
from options_auto.execution.real_execution_controller import RealExecutionController
from options_auto.execution.real_order_lifecycle import (
    ENTRY_ORDER_OPEN,
    OCO_ACTIVE,
    UNPROTECTED_POSITION,
    RealOrderLifecycleEngine,
)
from options_auto.intelligence.option_premium_confirmation import confirm_option_premium
from web_core.path_safety import safe_user_path


class FakeQuoteClient:
    def __init__(self):
        self.calls = []

    def quote(self, keys):
        self.calls.append(list(keys))
        now = datetime.now()
        return {
            key: {
                "last_price": 100.0,
                "bid": 99.9,
                "ask": 100.1,
                "volume": 10000,
                "oi": 50000,
                "timestamp": now,
            }
            for key in keys
        }


class FailingQuoteClient(FakeQuoteClient):
    def quote(self, keys):
        self.calls.append(list(keys))
        raise RuntimeError("quote limit hit")


class FakeInstrumentClient:
    def __init__(self):
        self.calls = 0

    def instruments(self, exchange):
        self.calls += 1
        return [{
            "exchange": exchange,
            "tradingsymbol": "NIFTY26JUN23500CE",
            "instrument_token": 123,
            "name": "NIFTY",
            "expiry": date(2026, 6, 25),
            "strike": 23500,
            "instrument_type": "CE",
            "lot_size": 65,
            "tick_size": 0.05,
            "segment": "NFO-OPT",
        }]


class FakeProtectionAdapter:
    def __init__(self, fail_sl=False):
        self.fail_sl = fail_sl
        self.target_orders = []
        self.stoploss_orders = []
        self.cancelled = []

    def place_target_sell_limit(self, tradingsymbol, quantity, price, exchange, product, tag):
        self.target_orders.append({
            "tradingsymbol": tradingsymbol,
            "quantity": quantity,
            "price": price,
            "exchange": exchange,
            "product": product,
            "tag": tag,
        })
        return {"ok": True, "value": f"TARGET{len(self.target_orders)}"}

    def place_stoploss_sell_sl_limit(self, tradingsymbol, quantity, trigger_price, price, exchange, product, tag):
        self.stoploss_orders.append({
            "tradingsymbol": tradingsymbol,
            "quantity": quantity,
            "trigger_price": trigger_price,
            "price": price,
            "exchange": exchange,
            "product": product,
            "tag": tag,
        })
        if self.fail_sl:
            return {"ok": False, "error": "SL rejected"}
        return {"ok": True, "value": f"SL{len(self.stoploss_orders)}"}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"ok": True}


def trade_plan(**overrides):
    base = {
        "tradingsymbol": "NIFTY26JUN23500CE",
        "exchange": "NFO",
        "product": "NRML",
        "quantity": 65,
        "entry_price": 100.0,
        "option_atr14": 4.0,
        "tick_size": 0.05,
    }
    base.update(overrides)
    return base


def entry_order(**overrides):
    base = {
        "order_id": "ENTRY1",
        "tradingsymbol": "NIFTY26JUN23500CE",
        "quantity": 65,
        "price": 100.0,
        "status": "OPEN",
    }
    base.update(overrides)
    return base


class OptionsAutoIndustryHardeningTests(unittest.TestCase):
    def test_real_lifecycle_waits_for_fill_before_target_and_sl(self):
        engine = RealOrderLifecycleEngine(RealExecutionController())
        engine.submit_entry(entry_order(), trade_plan(), {})

        open_snapshot = engine.poll_entry_status([{
            "order_id": "ENTRY1",
            "status": "OPEN",
            "quantity": 65,
            "filled_quantity": 0,
        }])

        self.assertEqual(open_snapshot["state"], ENTRY_ORDER_OPEN)
        self.assertEqual(open_snapshot["target_order"], {})
        self.assertEqual(open_snapshot["stoploss_order"], {})

    def test_real_lifecycle_places_protection_from_actual_fill(self):
        engine = RealOrderLifecycleEngine(RealExecutionController())
        adapter = FakeProtectionAdapter()
        engine.submit_entry(entry_order(), trade_plan(), {})

        filled = engine.poll_entry_status([{
            "order_id": "ENTRY1",
            "status": "COMPLETE",
            "quantity": 65,
            "filled_quantity": 65,
            "average_price": 101.25,
        }], settings={"min_stoploss_pct": 2.0, "risk_reward_multiplier": 1.5}, adapter=adapter)

        self.assertEqual(filled["state"], OCO_ACTIVE)
        self.assertEqual(filled["fill"]["average_price"], 101.25)
        self.assertEqual(adapter.target_orders[0]["quantity"], 65)
        self.assertEqual(adapter.stoploss_orders[0]["quantity"], 65)
        self.assertGreater(adapter.target_orders[0]["price"], filled["fill"]["average_price"])
        self.assertLess(adapter.stoploss_orders[0]["trigger_price"], filled["fill"]["average_price"])

    def test_real_lifecycle_protects_partial_fill_quantity(self):
        engine = RealOrderLifecycleEngine(RealExecutionController())
        adapter = FakeProtectionAdapter()
        engine.submit_entry(entry_order(quantity=65), trade_plan(quantity=65), {})

        snapshot = engine.poll_entry_status([{
            "order_id": "ENTRY1",
            "status": "OPEN",
            "quantity": 65,
            "filled_quantity": 20,
            "average_price": 99.5,
        }], settings={"partial_fill_protect_immediately": True}, adapter=adapter)

        self.assertEqual(snapshot["state"], OCO_ACTIVE)
        self.assertEqual(adapter.target_orders[0]["quantity"], 20)
        self.assertEqual(adapter.stoploss_orders[0]["quantity"], 20)

    def test_sl_failure_enters_unprotected_safe_mode(self):
        controller = RealExecutionController()
        engine = RealOrderLifecycleEngine(controller)
        engine.submit_entry(entry_order(), trade_plan(), {})

        snapshot = engine.poll_entry_status([{
            "order_id": "ENTRY1",
            "status": "COMPLETE",
            "quantity": 65,
            "filled_quantity": 65,
            "average_price": 100.0,
        }], adapter=FakeProtectionAdapter(fail_sl=True))

        self.assertEqual(snapshot["state"], UNPROTECTED_POSITION)
        self.assertTrue(snapshot["safe_mode"])
        self.assertTrue(controller.state.safe_mode)
        self.assertTrue(controller.state.stop_new_entries)

    def test_quote_provider_batches_and_returns_token_lookup(self):
        client = FakeQuoteClient()
        provider = OptionsQuoteProvider(client)
        candidates = [
            {"exchange": "NFO", "tradingsymbol": f"NIFTY26JUN23{i}00CE", "instrument_token": i}
            for i in range(3)
        ]

        result = provider.quote_candidates(candidates, {"max_full_quote_batch_size": 2, "max_quote_age_seconds": 3})

        self.assertEqual([len(call) for call in client.calls], [2, 1])
        self.assertEqual(result["data_mode"], "QUOTE_SNAPSHOT_POLLING")
        self.assertEqual(result["valid_quote_count"], 3)
        self.assertIn("1", result["quotes"])
        self.assertFalse(result["blocked"])

    def test_quote_provider_reports_api_failures_without_raising(self):
        result = OptionsQuoteProvider(FailingQuoteClient()).quote_candidates(
            [{"exchange": "NFO", "tradingsymbol": "NIFTY26JUN23500CE", "instrument_token": 1}],
            {"max_full_quote_batch_size": 1},
        )

        self.assertTrue(result["blocked"])
        self.assertTrue(result["errors"])
        self.assertEqual(result["valid_quote_count"], 0)

    def test_live_feed_tracks_locked_option_ticks_and_staleness(self):
        feed = OptionsLiveFeed()
        feed.subscribe_locked_contracts(1001, {"instrument_token": 2001}, {"instrument_token": 2002})
        now = datetime.now()
        feed.on_tick({"instrument_token": 1001, "last_price": 23350, "timestamp": now}, role="INDEX", interval="1minute")
        feed.on_tick({"instrument_token": 2001, "last_price": 110, "timestamp": now}, role="CE", interval="1minute")
        feed.on_tick({"instrument_token": 2002, "last_price": 95, "timestamp": now}, role="PE", interval="1minute")

        snapshot = feed.snapshot()

        self.assertEqual(snapshot["data_mode"], "WEBSOCKET_TICKS")
        self.assertEqual(snapshot["subscribed_tokens"], [1001, 2001, 2002])
        self.assertEqual(len(snapshot["option_candles"]["streams"]), 2)

    def test_feed_health_blocks_new_entries_on_stale_ticks(self):
        health = OptionsFeedHealth()
        health.mark_tick("INDEX", datetime.now() - timedelta(seconds=10))

        result = health.evaluate({"max_tick_age_seconds": 3, "pause_entries_on_feed_stale": True})

        self.assertEqual(result["data_mode"], DATA_STALE)
        self.assertFalse(result["new_entries_allowed"])
        self.assertEqual(result["stale_labels"], ["INDEX"])

    def test_persistent_instrument_cache_reuses_daily_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = PersistentInstrumentCache(tmp)
            client = FakeInstrumentClient()

            first = cache.get_or_fetch(client, "NFO", lambda c, exchange: c.instruments(exchange))
            second = cache.get_or_fetch(client, "NFO", lambda c, exchange: c.instruments(exchange))

            self.assertEqual(client.calls, 1)
            self.assertEqual(first["source"], "zerodha_fetch")
            self.assertEqual(second["source"], "daily_file")
            self.assertEqual(second["rows"][0]["lot_size"], 65)
            self.assertTrue(os.path.isfile(second["path"]))

    def test_safe_user_path_allows_csv_under_root_and_blocks_scripts(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            csv_path = os.path.join(root, "fii.csv")
            script_path = os.path.join(root, "script.py")
            outside_path = os.path.join(other, "fii.csv")
            for path in (csv_path, script_path, outside_path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x\n")

            self.assertEqual(safe_user_path(csv_path, [root], allowed_extensions={".csv"}), os.path.abspath(csv_path))
            with self.assertRaises(ValueError):
                safe_user_path(script_path, [root], allowed_extensions={".csv"})
            with self.assertRaises(ValueError):
                safe_user_path(outside_path, [root], allowed_extensions={".csv"})

    def test_option_premium_confirmation_requires_bullish_premium_candle(self):
        bullish = confirm_option_premium("CE", [{"open": 100, "high": 105, "low": 99, "close": 104, "complete": True}])
        weak = confirm_option_premium("CE", [{"open": 104, "high": 105, "low": 99, "close": 100, "complete": True}])

        self.assertTrue(bullish["allowed"])
        self.assertFalse(weak["allowed"])

    def test_blackbox_latency_report_records_p95(self):
        recorder = BlackboxRecorder()
        start = datetime(2026, 6, 6, 10, 0, 0)

        recorder.record(
            signal_generated_at=start,
            final_validation_started_at=start + timedelta(milliseconds=12),
            final_validation_completed_at=start + timedelta(milliseconds=28),
            order_submitted_at=start + timedelta(milliseconds=30),
            broker_ack_at=start + timedelta(milliseconds=72),
            data_age_ms=24,
        )

        report = recorder.snapshot()["latency_report"]
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["decision_latency_ms"]["p95"], 12)
        self.assertEqual(report["submit_to_ack_ms"]["p95"], 42)


if __name__ == "__main__":
    unittest.main()
