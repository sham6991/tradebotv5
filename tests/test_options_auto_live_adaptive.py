import time
import tempfile
import unittest

from options_auto.core.task_priority import P0_CRITICAL_PROTECTION, P4_SLOW
from options_auto.execution.paper_broker import PaperBroker
from options_auto.execution.paper_lifecycle import PaperLifecycleEngine
from options_auto.intelligence.live_adaptive_engine import LiveAdaptiveEngine
from options_auto.intelligence.low_latency_decision_engine import LowLatencyDecisionEngine
from options_auto.intelligence.ready_trade_plan_cache import ReadyTradePlanCache
from options_auto.terminal_service import OptionsAutoTerminalService
from tests.test_options_auto_auto_spot import FakeOptionsZerodha


def settings(**overrides):
    base = {
        "mode": "PAPER",
        "underlying": "NIFTY",
        "strategy_profile": "BALANCED",
        "buy_score_threshold": 70,
        "quote_stale_seconds": 3,
        "max_spread_pct": 0.60,
        "max_chase_points": 3.0,
        "max_chase_atr_fraction": 0.35,
        "slippage_buffer_points": 0.10,
        "limit_order_timeout_seconds": 30,
        "modify_limit_allowed": True,
        "max_buy_limit_modifications": 2,
        "minimum_sl_improvement_ticks": 2,
        "sl_modify_throttle_seconds": 10,
        "early_exit_min_conditions": 3,
        "premium_stagnation_candles": 3,
        "allow_target_extension": True,
        "target_extension_atr_fraction": 0.8,
        "target_extension_target_fraction": 0.5,
        "target_extension_profit_protection_pct": 40,
        "adaptive_scan_seconds_aggressive": 1,
        "adaptive_scan_seconds_balanced": 2,
        "adaptive_scan_seconds_conservative": 3,
        "max_plan_age_seconds_aggressive": 3,
        "max_plan_age_seconds_balanced": 5,
        "max_plan_age_seconds_conservative": 8,
        "low_aggression_score_boost": 10,
        "low_aggression_quantity_pct": 50,
        "reduce_quantity_on_low_aggression": True,
        "partial_exit_enabled": True,
    }
    base.update(overrides)
    return base


def ready_decision():
    return {
        "allowed": True,
        "selected_side": "CE",
        "selected_contract": {
            "tradingsymbol": "NIFTY26JUN22500CE",
            "instrument_token": "1",
            "option_type": "CE",
            "ltp": 142.40,
            "bid": 142.25,
            "ask": 142.45,
            "spread_pct": 0.1405,
            "tick_size": 0.05,
            "premium_expansion_confirmed": True,
            "premium_momentum": {"premium_return_1": 1.2, "premium_return_3": 4.5},
            "option_atr14": 5,
            "relative_volume": 1.7,
        },
        "trade_plan": {"entry_price": 142.45, "target": 148.95, "stoploss": 137.45, "quantity": 50, "lot_size": 50},
        "trade_score": {"score": 82},
        "theta_premium_risk": {"theta_risk": "MEDIUM"},
        "market_cue": {"cue": "strong_bullish", "recommended_side": "CE", "confidence": 82},
        "regime": {"regime": "strong_bullish", "recommended_side": "CE", "confidence": 85},
        "data_quality": {"allowed": True},
        "governor": {"allowed": True},
    }


def valid_plan(now=1000.0):
    return ReadyTradePlanCache().refresh_from_decision(ready_decision(), settings(), now_epoch=now)


def quote(**overrides):
    base = {
        "ltp": 142.40,
        "bid": 142.25,
        "ask": 142.45,
        "tick_size": 0.05,
        "premium_return_1": 1.2,
        "option_atr14": 5,
        "timestamp_epoch": 1000.0,
        "now_epoch": 1000.0,
    }
    base.update(overrides)
    return base


def index_features():
    return {"close": 22540, "vwap": 22490, "ema9": 22520, "ema20": 22480, "ema50": 22400}


def option_features(**overrides):
    base = {
        "side": "CE",
        "option_vwap": 140,
        "premium_return_1": 1.2,
        "premium_return_3": 4.5,
        "premium_expansion_confirmed": True,
        "relative_volume": 1.7,
        "spread_pct": 0.14,
        "option_atr14": 5,
        "theta_risk": "MEDIUM",
    }
    base.update(overrides)
    return base


def trade(**overrides):
    base = {
        "trade_id": "T1",
        "mode": "PAPER",
        "tradingsymbol": "NIFTY26JUN22500CE",
        "side": "CE",
        "entry_price": 142.45,
        "initial_stoploss": 137.45,
        "stoploss": 137.45,
        "target": 148.95,
        "quantity": 100,
        "lot_size": 50,
        "tick_size": 0.05,
        "stoploss_order_id": "SL1",
    }
    base.update(overrides)
    return base


def service_payload(mode="PAPER"):
    return {
        "mode": mode,
        "timestamp": "2026-06-04 10:00:00",
        "spot": 22540,
        "settings": {
            "mode": mode,
            "underlying": "NIFTY",
            "buy_score_threshold": 35,
            "atm_scan_strike_span": 0,
            "premium_expansion_required": False,
            "max_capital_per_trade_pct": 60,
            "max_risk_per_trade_pct": 5,
            "paper_starting_balance": 20000,
            "confirm_real_mode": mode == "REAL",
        },
        "market_cue": {"phase": "LUNCH", "technical_score": 75, "option_oi_score": 25, "news_score": 1},
        "features": {"close": 22540, "vwap": 22490, "ema9": 22520, "ema20": 22480, "ema50": 22400, "rsi14": 64, "rsi_slope_3": 5, "relative_volume": 1.8, "atr_pct": 0.25, "trend_strength_score": 75},
        "instruments": [{"name": "NIFTY", "tradingsymbol": "NIFTY26JUN22500CE", "instrument_token": "1", "instrument_type": "CE", "strike": 22500, "expiry": "2026-06-25", "lot_size": 50, "tick_size": 0.05}],
        "quotes": {"1": {"ltp": 142.40, "bid": 142.25, "ask": 142.45, "bid_qty": 2000, "ask_qty": 2000, "volume": 100000, "oi": 1000000, "premium_return_1": 1.2, "premium_return_3": 4.5, "relative_volume": 1.7, "option_vwap": 140, "option_atr14": 5, "timestamp_epoch": time.time()}},
    }


class OptionsAutoLiveAdaptiveTests(unittest.TestCase):
    def test_ready_plan_expires_after_max_age(self):
        cache = ReadyTradePlanCache()
        cache.refresh_from_decision(ready_decision(), settings(), now_epoch=100)

        plan = cache.get("NIFTY", now_epoch=106)

        self.assertEqual(plan["status"], "STALE")
        self.assertIn("Ready trade plan expired.", plan["blockers"])

    def test_fast_validation_passes_valid_ce_under_latency_target(self):
        result = LowLatencyDecisionEngine().validate_final_entry(
            valid_plan(),
            quote(),
            settings(),
            {"market_cue": {"recommended_side": "CE"}, "regime": {"recommended_side": "CE"}, "mode_guard_allowed": True, "governor_allowed": True},
            now_epoch=1001,
        )

        self.assertTrue(result["allowed"])
        self.assertLess(result["latency_ms"], 200)
        self.assertEqual(result["entry_limit"], 142.45)

    def test_fast_validation_blocks_stale_quote_chase_and_opposite_cue(self):
        engine = LowLatencyDecisionEngine()
        stale = engine.validate_final_entry(valid_plan(), quote(age_seconds=4), settings(), {"market_cue": {"recommended_side": "CE"}, "regime": {"recommended_side": "CE"}}, now_epoch=1001)
        chase = engine.validate_final_entry(valid_plan(), quote(ltp=146.2, ask=146.3, bid=146.1), settings(), {"market_cue": {"recommended_side": "CE"}, "regime": {"recommended_side": "CE"}}, now_epoch=1001)
        opposite = engine.validate_final_entry(valid_plan(), quote(), settings(), {"market_cue": {"recommended_side": "PE"}, "regime": {"recommended_side": "CE"}}, now_epoch=1001)

        self.assertIn("Quote stale.", stale["blockers"])
        self.assertIn("Entry is chasing premium.", chase["blockers"])
        self.assertIn("Market cue reversed.", opposite["blockers"])

    def test_candidate_scan_only_scans_atm_window(self):
        instruments = []
        for strike in range(22000, 23100, 50):
            instruments.append({"tradingsymbol": f"NIFTY{strike}CE", "instrument_type": "CE", "strike": strike})
            instruments.append({"tradingsymbol": f"NIFTY{strike}PE", "instrument_type": "PE", "strike": strike})

        candidates = LowLatencyDecisionEngine().scan_atm_candidates(instruments, 22540, {"atm_scan_strike_span": 4})
        strikes = {item["strike"] for item in candidates}

        self.assertLessEqual(len(strikes), 9)
        self.assertIn(22550, strikes)
        self.assertNotIn(22000, strikes)

    def test_premium_expansion_false_blocks_pre_entry(self):
        result = LiveAdaptiveEngine().evaluate_pre_entry(
            {"option_type": "CE", "score": 90, "spread_pct": 0.1},
            index_features(),
            option_features(premium_expansion_confirmed=False),
            {"recommended_side": "CE"},
            {"recommended_side": "CE"},
            settings(),
            {},
            {"score": 100},
        )

        self.assertIn("Option premium is not confirming index direction.", result["blockers"])

    def test_pending_entry_cancels_on_spread_wait_and_stale_signal(self):
        engine = LiveAdaptiveEngine()
        pending = {"entry_id": "E1", "price": 142.45, "planned_entry": 142.45, "side": "CE", "created_epoch": 1000}

        wide = engine.evaluate_pending_entry(pending, {}, quote(spread_pct=0.95, ask=143, bid=141, now_epoch=1001), index_features(), option_features(spread_pct=0.95), {"recommended_side": "CE"}, {"recommended_side": "CE"}, settings())
        wait = engine.evaluate_pending_entry(pending, {}, quote(now_epoch=1001), index_features(), option_features(), {"recommended_side": "CE"}, {"recommended_side": "WAIT"}, settings())
        stale = engine.evaluate_pending_entry(pending, {}, quote(signal_age_seconds=21, now_epoch=1001), index_features(), option_features(), {"recommended_side": "CE"}, {"recommended_side": "CE"}, settings())

        self.assertEqual(wide["action"], "CANCEL_ENTRY")
        self.assertEqual(wait["action"], "CANCEL_ENTRY")
        self.assertIn("Signal is stale.", stale["blockers"])

    def test_pending_entry_modifies_only_within_chase_limits(self):
        engine = LiveAdaptiveEngine()
        pending = {"entry_id": "E1", "price": 142.45, "planned_entry": 142.45, "side": "CE", "created_epoch": 1000, "modification_count": 0}

        modify = engine.evaluate_pending_entry(pending, {}, quote(ltp=142.6, ask=142.7, bid=142.5, now_epoch=1001), index_features(), option_features(), {"recommended_side": "CE"}, {"recommended_side": "CE"}, settings())
        chase = engine.evaluate_pending_entry(pending, {}, quote(ltp=146.0, ask=146.2, bid=146.0, now_epoch=1001), index_features(), option_features(), {"recommended_side": "CE"}, {"recommended_side": "CE"}, settings())

        self.assertEqual(modify["action"], "MODIFY_ENTRY")
        self.assertEqual(chase["action"], "CANCEL_ENTRY")

    def test_active_trade_moves_sl_to_breakeven_and_locks_profit(self):
        engine = LiveAdaptiveEngine()
        breakeven = engine.evaluate_active_trade(trade(), quote(ltp=145.7), index_features(), option_features(), {"recommended_side": "CE"}, {"regime": "strong_bullish", "recommended_side": "CE"}, settings(allow_target_extension=False))
        locked = engine.evaluate_active_trade(trade(), quote(ltp=147.35), index_features(), option_features(), {"recommended_side": "CE"}, {"regime": "strong_bullish", "recommended_side": "CE"}, settings(allow_target_extension=False))

        self.assertAlmostEqual(breakeven["new_stoploss"], 142.45)
        self.assertAlmostEqual(locked["new_stoploss"], 144.75)

    def test_active_trade_trails_using_atr_in_strong_trend(self):
        result = LiveAdaptiveEngine().evaluate_active_trade(
            trade(stoploss=144.75),
            quote(ltp=150),
            index_features(),
            option_features(option_atr14=5),
            {"recommended_side": "CE"},
            {"regime": "strong_bullish", "recommended_side": "CE"},
            settings(allow_target_extension=False),
        )

        self.assertAlmostEqual(result["new_stoploss"], 146.0)

    def test_target_extension_only_in_winner_trending_and_tightens_sl(self):
        engine = LiveAdaptiveEngine()
        trending = engine.evaluate_active_trade(trade(stoploss=142.45), quote(ltp=148.2), index_features(), option_features(), {"recommended_side": "CE"}, {"regime": "strong_bullish", "recommended_side": "CE"}, settings())
        slowing = engine.evaluate_active_trade(trade(stoploss=142.45), quote(ltp=148.2), index_features(), option_features(premium_return_1=-0.2), {"recommended_side": "CE"}, {"regime": "strong_bullish", "recommended_side": "CE"}, settings())

        self.assertEqual(trending["action"], "MODIFY_TARGET")
        self.assertAlmostEqual(trending["new_target"], 152.2)
        self.assertAlmostEqual(trending["new_stoploss"], 144.75)
        self.assertIsNone(slowing["new_target"])

    def test_ce_and_pe_early_exit_after_three_invalidations(self):
        engine = LiveAdaptiveEngine()
        ce = engine.evaluate_active_trade(
            trade(),
            quote(ltp=139.8),
            {"close": 22400, "vwap": 22490, "ema9": 22450, "ema20": 22480},
            option_features(option_vwap=141, premium_return_1=-1.2, premium_return_3=-0.3),
            {"recommended_side": "WAIT"},
            {"recommended_side": "WAIT"},
            settings(allow_target_extension=False),
        )
        pe = engine.evaluate_active_trade(
            trade(side="PE"),
            quote(ltp=139.8),
            {"close": 22580, "vwap": 22490, "ema9": 22520, "ema20": 22480},
            option_features(option_vwap=141, premium_return_1=-1.2, premium_return_3=-0.3),
            {"recommended_side": "WAIT"},
            {"recommended_side": "WAIT"},
            settings(allow_target_extension=False),
        )

        self.assertEqual(ce["action"], "EXIT")
        self.assertEqual(pe["action"], "EXIT")

    def test_sl_never_widens_and_throttle_blocks_modification(self):
        engine = LiveAdaptiveEngine()
        no_widen = engine.evaluate_active_trade(trade(stoploss=145), quote(ltp=145.7), index_features(), option_features(), {"recommended_side": "CE"}, {"recommended_side": "CE"}, settings(allow_target_extension=False))
        throttled = engine.evaluate_active_trade(trade(stoploss=137.45, last_stoploss_modified_epoch=1000), quote(ltp=145.7, now_epoch=1005), index_features(), option_features(), {"recommended_side": "CE"}, {"recommended_side": "CE"}, settings(allow_target_extension=False))

        self.assertIsNone(no_widen["new_stoploss"])
        self.assertIsNone(throttled["new_stoploss"])
        self.assertIn("Stoploss modification throttle is active.", throttled["warnings"])

    def test_low_and_high_aggression_effects(self):
        engine = LiveAdaptiveEngine()
        low = engine.adjusted_score_threshold(settings(), {"side": "CE"}, {"recommended_side": "WAIT"}, {"regime": "neutral_sideways", "recommended_side": "WAIT"}, option_features(spread_pct=0.9, theta_risk="HIGH"), {"recent_loss": True}, {"score": 70})
        high = engine.aggression(index_features(), option_features(), {"cue": "strong_bullish", "recommended_side": "CE"}, {"regime": "strong_bullish", "recommended_side": "CE"}, settings(), {}, {"score": 100, "session_health": 100, "bot_health": 100})

        self.assertEqual(low["aggression"]["level"], "LOW")
        self.assertEqual(low["threshold"], 80)
        self.assertEqual(low["quantity_pct"], 50)
        self.assertEqual(high["level"], "HIGH")
        self.assertEqual(high["scan_interval_seconds"], 1)
        self.assertTrue(high["allow_target_extension"])

    def test_slow_lane_task_does_not_run_inside_fast_validation(self):
        engine = LowLatencyDecisionEngine()
        result = engine.validate_final_entry(valid_plan(), quote(), settings(), {"market_cue": {"recommended_side": "CE"}, "regime": {"recommended_side": "CE"}}, now_epoch=1001)

        self.assertFalse(result["slow_lane_tasks_used"])
        self.assertTrue(engine.fast_lane_contains_slow_task(["news_fetch"]))
        self.assertLess(P0_CRITICAL_PROTECTION, P4_SLOW)

    def test_real_dry_run_sends_zero_orders_and_reports_guarded_readiness(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda _mode: FakeOptionsZerodha(spot=22540, option_price=142.4))
            result = service.real_dry_run(service_payload("REAL"))

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["orders_sent"], 0)
        self.assertEqual(result["adaptive_dry_run"]["orders_sent"], 0)
        self.assertEqual(result["blockers"], [])
        self.assertTrue(result["real_execution_enabled"])
        self.assertIn("guarded", result["real_execution_reason"])

    def test_paper_mode_executes_dynamic_cancel_in_simulation_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda _mode: FakeOptionsZerodha(spot=22540, option_price=142.4))
            result = service.execute_paper_plan(service_payload("PAPER"))
            self.assertEqual(result["paper_order"]["status"], "OPEN")

            processed = service.process_paper_market({"market": {"ltp": 142.5, "bid": 141, "ask": 143, "spread_pct": 0.95, "premium_return_1": -0.7, "regime": {"recommended_side": "WAIT"}}})

        self.assertEqual(processed["adaptive_pending_updates"][0]["adaptive"]["action"], "CANCEL_ENTRY")
        self.assertEqual(service.paper_broker.orders[0]["status"], "CANCELLED")

    def test_paper_mode_dynamic_active_trade_sl_update(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        pending = lifecycle.create_pending({"allowed": True, "settings": {}, "trade_plan": {"tradingsymbol": "NIFTY26JUN22500CE", "side": "CE", "entry_price": 142.45, "stoploss": 137.45, "target": 148.95, "quantity": 50, "lot_size": 50}}, now_epoch=100)
        lifecycle.approve(pending["approval_id"], now_epoch=101)
        lifecycle.process_market({"ltp": 142.4, "low": 142.4, "high": 143, "now_epoch": 102})
        trade_row = lifecycle.active_trades[0]
        engine = LiveAdaptiveEngine()
        adaptive = engine.evaluate_active_trade(trade_row, quote(ltp=145.7), index_features(), option_features(), {"recommended_side": "CE"}, {"recommended_side": "CE"}, settings(allow_target_extension=False))
        lifecycle.update_stoploss(trade_row["trade_id"], adaptive["new_stoploss"])

        self.assertGreaterEqual(lifecycle.active_trades[0]["stoploss"], trade_row["entry_price"])

    def test_paper_start_runs_continuous_scanner_and_auto_approval_cycle(self):
        client = FakeOptionsZerodha(spot=22540, option_price=142.4)
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            payload = service_payload("PAPER")
            payload["settings"].update({"auto_entry_enabled": True, "ask_permission_before_entry": True})

            started = service.start_paper(payload)
            service._live_scan_stop.set()
            initial_quote_calls = len(client.quote_calls)
            with service._lock:
                cycle = service._run_live_scan_cycle_locked()

            service.stop_live_scan({"mode": "PAPER"})

        self.assertTrue(started["live_scan"]["running"])
        self.assertGreater(len(client.quote_calls), initial_quote_calls)
        self.assertEqual(cycle["live_scan_action"]["action"], "APPROVAL_CREATED")
        self.assertEqual(service.session.status, "PAPER_STOPPED")

    def test_stop_engine_preserves_active_paper_trade(self):
        client = FakeOptionsZerodha(spot=22540, option_price=142.4)
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            service.execute_paper_plan(service_payload("PAPER"))
            service.process_paper_market({"market": {"ltp": 142.4, "bid": 142.25, "ask": 142.45, "low": 142.4, "high": 143}})

            stopped = service.stop_live_scan({"mode": "PAPER"})

        self.assertFalse(stopped["live_scan"]["running"])
        self.assertEqual(stopped["session"]["status"], "PAPER_STOPPED")
        self.assertEqual(len(stopped["session"]["active_trades"]), 1)
        self.assertEqual(service.paper_broker.orders[0]["status"], "COMPLETE")

    def test_kill_switch_cancels_pending_entry_but_not_active_paper_trade(self):
        client = FakeOptionsZerodha(spot=22540, option_price=142.4)
        with tempfile.TemporaryDirectory() as pending_dir, tempfile.TemporaryDirectory() as active_dir:
            pending_service = OptionsAutoTerminalService(pending_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            pending_service.execute_paper_plan(service_payload("PAPER"))
            killed_pending = pending_service.kill_switch({"mode": "PAPER", "reason": "operator test"})

            active_service = OptionsAutoTerminalService(active_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            active_service.execute_paper_plan(service_payload("PAPER"))
            active_service.process_paper_market({"market": {"ltp": 142.4, "bid": 142.25, "ask": 142.45, "low": 142.4, "high": 143}})
            killed_active = active_service.kill_switch({"mode": "PAPER", "reason": "operator test"})

        self.assertEqual(killed_pending["session"]["status"], "PAPER_KILL_SWITCH_ACTIVE")
        self.assertEqual(pending_service.paper_broker.orders[0]["status"], "CANCELLED")
        self.assertEqual(len(killed_pending["session"]["active_trades"]), 0)
        self.assertEqual(killed_active["session"]["status"], "PAPER_KILL_SWITCH_ACTIVE")
        self.assertEqual(len(killed_active["session"]["active_trades"]), 1)
        self.assertEqual(active_service.paper_broker.orders[0]["status"], "COMPLETE")

    def test_real_dry_run_runs_continuous_scanner_without_sending_orders(self):
        client = FakeOptionsZerodha(spot=22540, option_price=142.4, label="REAL")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = service_payload("REAL")
            payload["settings"].update({"confirm_real_mode": True, "dry_run_real_only": True})

            started = service.real_dry_run(payload)
            service._live_scan_stop.set()
            initial_quote_calls = len(client.quote_calls)
            with service._lock:
                cycle = service._run_live_scan_cycle_locked()

            service.stop_live_scan({"mode": "REAL"})

        self.assertTrue(started["live_scan"]["running"])
        self.assertGreater(len(client.quote_calls), initial_quote_calls)
        self.assertEqual(cycle["live_scan_action"]["action"], "REAL_SCAN_ONLY")
        self.assertEqual(cycle["live_scan_action"]["orders_sent"], 0)
        self.assertEqual(service.session.status, "REAL_STOPPED")


if __name__ == "__main__":
    unittest.main()
