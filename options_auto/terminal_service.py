from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, time as dt_time
from typing import Any

import pandas as pd

from options_auto.backtest.backtest_engine import OptionsAutoBacktestEngine
from options_auto.backtest.market_replay import MarketReplayEngine
from options_auto.backtest.backtest_report_writer import BacktestReportWriter
from options_auto.config.options_auto_defaults import default_settings, normalize_settings
from options_auto.config.symbol_config import SYMBOL_CONFIG
from options_auto.constants import MODE_BACKTEST, MODE_PAPER, MODE_REAL, MODE_SHADOW, REAL_EXECUTION_DISABLED_REASON, SIDE_WAIT
from options_auto.core.logger import OptionsAutoLogger
from options_auto.core.mode_guard import ModeGuard, normalize_mode
from options_auto.core.performance_monitor import PerformanceMonitor
from options_auto.core.promotion import PromotionManager
from options_auto.core.session_state import OptionsAutoSessionState
from options_auto.core.watchdog import WatchdogService
from options_auto.data.index_data_provider import OptionsAutoIndexDataProvider
from options_auto.data.live_index_candles import LiveIndexCandleStore
from options_auto.data.option_chain_builder import OptionChainBuilder
from options_auto.data.options_quote_provider import OptionsQuoteProvider
from options_auto.execution.execution_safety import DataQualityEngine, RealOrderPreflight
from options_auto.execution.kite_api_manager import KiteApiManager, RateLimiter
from options_auto.execution.kite_order_adapter import KiteOrderAdapter
from options_auto.execution.paper_broker import PaperBroker
from options_auto.execution.paper_lifecycle import PaperLifecycleEngine
from options_auto.execution.reconciliation import ReconciliationEngine
from options_auto.execution.real_execution_controller import RealExecutionController, results_folder_writable
from options_auto.data.fii_dii_loader import fii_dii_status_from_upload, parse_fii_dii_csv_text
from options_auto.intelligence.adaptive_risk_engine import PositionSizer, RiskEngine
from options_auto.intelligence.decision_pipeline import evaluate_options_auto_decision
from options_auto.intelligence.entry_timing_engine import EntryTimingEngine, round_to_tick
from options_auto.intelligence.exit_manager import ExitManager, build_long_option_trade_plan
from options_auto.intelligence.live_adaptive_engine import LiveAdaptiveEngine
from options_auto.intelligence.low_latency_decision_engine import LowLatencyDecisionEngine
from options_auto.intelligence.master_governor import MasterGovernor
from options_auto.intelligence.market_cue_engine import MarketCueEngine
from options_auto.intelligence.missed_trade_learning import MissedTradeLearning
from options_auto.intelligence.options_greeks_risk_engine import OptionsGreeksRiskEngine
from options_auto.intelligence.position_manager import PositionManager
from options_auto.intelligence.professional_discipline import ProfessionalDisciplineEngine
from options_auto.intelligence.regime_classifier import RegimeClassifier
from options_auto.intelligence.ready_trade_plan_cache import ReadyTradePlanCache
from options_auto.intelligence.strategy_drift import StrategyDriftMonitor
from options_auto.intelligence.strike_selector import StrikeSelector
from options_auto.shadow_mode import ShadowModeEngine
from options_auto.telegram_safety import TelegramSafety


class OptionsAutoTerminalService:
    def __init__(self, base_result_folder: str, kite_client_provider=None):
        self.base_result_folder = base_result_folder
        self.kite_client_provider = kite_client_provider or (lambda _mode: None)
        self.logger = OptionsAutoLogger()
        self.settings = default_settings()
        self.mode_guard = ModeGuard(mode=MODE_PAPER)
        self.session = OptionsAutoSessionState(self.mode_guard)
        self.paper_broker = PaperBroker(self.settings["paper_starting_balance"])
        self.paper_lifecycle = PaperLifecycleEngine(self.paper_broker)
        self.market_cue_engine = MarketCueEngine()
        self.regime_classifier = RegimeClassifier()
        self.strike_selector = StrikeSelector()
        self.data_quality = DataQualityEngine()
        self.real_preflight = RealOrderPreflight()
        self.real_api_manager = KiteApiManager(limiter=RateLimiter(max_calls=10, per_seconds=1.0))
        self.backtest_engine = OptionsAutoBacktestEngine()
        self.market_replay_engine = MarketReplayEngine()
        self.report_writer = BacktestReportWriter(base_result_folder)
        self.risk_engine = RiskEngine()
        self.position_sizer = PositionSizer()
        self.discipline_engine = ProfessionalDisciplineEngine()
        self.master_governor = MasterGovernor()
        self.watchdog = WatchdogService()
        self.promotion = PromotionManager()
        self.drift_monitor = StrategyDriftMonitor()
        self.missed_trade_learning = MissedTradeLearning()
        self.exit_manager = ExitManager()
        self.position_manager = PositionManager()
        self.entry_timing = EntryTimingEngine()
        self.options_risk = OptionsGreeksRiskEngine()
        self.reconciliation = ReconciliationEngine()
        self.real_controller = RealExecutionController(self.real_api_manager, self.reconciliation)
        self.shadow_engine = ShadowModeEngine()
        self.telegram_safety = TelegramSafety()
        self.performance_monitor = PerformanceMonitor(
            final_validation_warning_ms=float(self.settings.get("final_validation_latency_warning_ms") or 200),
            action_warning_ms=float(self.settings.get("action_latency_warning_ms") or 500),
        )
        self.ready_plan_cache = ReadyTradePlanCache()
        self.low_latency_engine = LowLatencyDecisionEngine(self.performance_monitor)
        self.live_adaptive = LiveAdaptiveEngine(log_path=os.path.join(self.result_root(), "adaptive_action_log.jsonl"))
        self.latest_fii_dii_snapshot: dict[str, Any] | None = None
        self.index_ticks: list[dict[str, Any]] = []
        self.live_index_candles = LiveIndexCandleStore()
        self._lock = threading.RLock()
        self._live_scan_stop = threading.Event()
        self._live_scan_thread: threading.Thread | None = None
        self._live_scan_mode = ""
        self._live_scan_payload: dict[str, Any] = {}
        self._live_scan_interval_seconds = 0.0
        self._live_scan_started_at = ""
        self._live_scan_last_cycle = ""
        self._live_scan_last_error = ""
        self._live_scan_cycle_count = 0

    def defaults(self) -> dict[str, Any]:
        return {
            "settings": default_settings(),
            "feature": "Options Auto",
            "real_execution": {
                "enabled": False,
                "reason": "Connect Real Money Zerodha and pass guarded preflight before live Options Auto orders.",
            },
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "settings": self.settings,
                "session": self.session.to_dict(),
                "paper_account": self.paper_broker.snapshot(),
                "paper_lifecycle": self.paper_lifecycle.snapshot(),
                "real_safety": self.real_controller.snapshot(),
                "logs": self.logger.tail(100),
                "result_root": self.result_root(),
                "shadow_report": self.shadow_engine.report(),
                "ready_trade_plan_cache": self.ready_plan_cache.snapshot(),
                "adaptive": self.live_adaptive.snapshot(),
                "performance": self.performance_monitor.snapshot(),
                "fii_dii": self.latest_fii_dii_status(),
                "index_ticks": self.index_ticks[-80:],
                "live_index_candles": self.live_index_candles.snapshot(),
                "live_scan": self._live_scan_state_locked(),
            }

    def result_root(self) -> str:
        return os.path.join(self.base_result_folder, "options_auto")

    def configure(self, payload: dict[str, Any] | None = None, kite_profile: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._stop_live_scan_locked(reason="configure")
            return self._configure_locked(payload, kite_profile=kite_profile, preserve_session=False)

    def _configure_locked(
        self,
        payload: dict[str, Any] | None = None,
        kite_profile: dict[str, Any] | None = None,
        preserve_session: bool = False,
    ) -> dict[str, Any]:
        settings = normalize_settings(payload)
        mode = normalize_mode(settings.get("mode"))
        previous_session = self.session if preserve_session and self.mode_guard.mode == mode else None
        self.settings = settings
        self.mode_guard = ModeGuard(
            mode=mode,
            kite_profile=dict(kite_profile or {}),
            real_mode_confirmed=bool(settings.get("confirm_real_mode")),
            real_orders_enabled=bool(settings.get("real_orders_enabled")),
        )
        if previous_session is not None:
            self.session = previous_session
            self.session.mode_guard = self.mode_guard
        else:
            self.session = OptionsAutoSessionState(self.mode_guard)
        if mode == MODE_PAPER and not self._paper_lifecycle_active():
            self.paper_broker = PaperBroker(float(settings["paper_starting_balance"]))
            self.paper_lifecycle = PaperLifecycleEngine(self.paper_broker)
        elif mode == MODE_PAPER:
            self.logger.log("WARN", "Options Auto paper lifecycle preserved during configure", mode=mode)
        self.logger.log("INFO", "Options Auto configured", mode=mode, underlying=settings.get("underlying"))
        return self.status()

    def evaluate(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            return self._evaluate_locked(payload)

    def _evaluate_locked(self, payload: dict[str, Any] | None = None, preserve_session: bool = False) -> dict[str, Any]:
        payload = dict(payload or {})
        settings_payload = {**self.settings, **dict(payload.get("settings") or {})}
        mode = normalize_mode(payload.get("mode") or settings_payload.get("mode") or self.settings.get("mode"))
        profile = payload.get("kite_profile") or {}
        self._configure_locked({**settings_payload, "mode": mode}, kite_profile=profile, preserve_session=preserve_session)
        return self._evaluate_current_config_locked(payload, mode)

    def _evaluate_current_config_locked(self, payload: dict[str, Any] | None = None, mode: str | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        mode = normalize_mode(mode or payload.get("mode") or self.settings.get("mode"))
        instruments = list(payload.get("instruments") or [])
        quotes = dict(payload.get("quotes") or {})
        settings = dict(self.settings)
        index_history = pd.DataFrame() if mode in {MODE_PAPER, MODE_REAL} else self._frame(payload.get("index_history") or payload.get("index_candles") or payload.get("candles") or [])
        live_data = self._live_options_market_context(mode, settings, payload, index_history)
        if live_data.get("blocked"):
            decision = self._blocked_data_decision(mode, settings, live_data)
            self.session.record_decision(decision)
            self.session.record_rejection("; ".join(decision["blockers"]), {"mode": mode, "stage": "DATA"})
            self.logger.log("WARN", "Options Auto data blocked evaluation", mode=mode, blockers=decision["blockers"])
            return {**decision, "session": self.session.to_dict(), "paper_account": self.paper_broker.snapshot()}
        if live_data:
            payload = {**payload, **dict(live_data.get("payload") or {})}
            instruments = list(live_data.get("instruments") or instruments)
            quotes = dict(live_data.get("quotes") or quotes)
            if mode in {MODE_PAPER, MODE_REAL}:
                index_history = self._frame(payload.get("index_history") or payload.get("index_candles") or [])
        account_state = {
            "available_capital": self._available_capital(mode),
            "available_balance": self.paper_broker.available_balance,
        }
        market_cue_payload = self._market_cue_payload(payload)
        decision = evaluate_options_auto_decision(
            mode=mode,
            settings=settings,
            index_history=index_history,
            option_candidates=instruments,
            quotes=quotes,
            market_cue_payload=market_cue_payload,
            risk_state=payload.get("risk_state") or {},
            account_state=account_state,
            timestamp=payload.get("timestamp") or payload.get("datetime"),
        )
        if live_data:
            decision.update(live_data.get("diagnostics") or {})
        self._record_index_tick_locked(decision, mode)
        blockers = decision.get("blockers") or []
        if mode in {MODE_PAPER, MODE_REAL} and self.settings.get("ready_plan_cache_enabled"):
            ready_plan = self.ready_plan_cache.refresh_from_decision(decision, self.settings)
            decision["ready_trade_plan"] = ready_plan
        self.session.record_decision(decision)
        if mode == MODE_SHADOW:
            self.shadow_engine.record(decision)
        if blockers:
            self.session.record_rejection("; ".join(blockers), {"mode": mode})
        self.logger.log("INFO", "Options Auto evaluation completed", mode=mode, allowed=decision["allowed"], blockers=blockers)
        return {**decision, "session": self.session.to_dict(), "paper_account": self.paper_broker.snapshot()}

    def _record_index_tick_locked(self, decision: dict[str, Any], mode: str) -> None:
        if mode not in {MODE_PAPER, MODE_REAL}:
            return
        spot_value = decision.get("spot_value")
        if spot_value in ("", None):
            spot_value = (decision.get("spot") or {}).get("spot") if isinstance(decision.get("spot"), dict) else None
        try:
            spot = float(spot_value)
        except (TypeError, ValueError):
            return
        if spot <= 0:
            return
        spot_payload = decision.get("spot") if isinstance(decision.get("spot"), dict) else {}
        tick = {
            "observed_at": datetime.now().isoformat(timespec="seconds"),
            "decision_timestamp": decision.get("timestamp") or "",
            "exchange_timestamp": spot_payload.get("timestamp") or "",
            "mode": mode,
            "underlying": spot_payload.get("underlying") or self.settings.get("underlying") or "",
            "spot": spot,
            "spot_source": decision.get("spot_source") or spot_payload.get("spot_source") or "",
            "quote_key": spot_payload.get("quote_key") or "",
            "age_seconds": spot_payload.get("age_seconds"),
            "live_scan_cycle": self._live_scan_cycle_count,
        }
        self.index_ticks.append(tick)
        if len(self.index_ticks) > 200:
            self.index_ticks = self.index_ticks[-200:]

    def _trade_plan(self, selected: dict[str, Any], sizing: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
        return build_long_option_trade_plan(selected, sizing, regime, self.settings)

    def _live_options_market_context(self, mode: str, settings: dict[str, Any], payload: dict[str, Any], index_history: pd.DataFrame) -> dict[str, Any]:
        if mode not in {MODE_PAPER, MODE_REAL}:
            return {}
        client_mode = "LIVE" if mode == MODE_REAL else "PAPER"
        client = self.kite_client_provider(client_mode) or self.kite_client_provider(mode)
        underlying = str(settings.get("underlying") or payload.get("underlying") or "NIFTY").upper()
        spot_provider = OptionsAutoIndexDataProvider(lambda requested_mode: client if str(requested_mode).upper() in {client_mode, mode} else None)
        spot = spot_provider.get_spot(underlying, mode, payload=payload, index_candles=index_history)
        source = "zerodha_real_data" if mode == MODE_REAL else "zerodha_paper_data"
        base_diagnostics = {
            "data_source": source,
            "market_data": source,
            "order_execution": "real_zerodha_orders" if mode == MODE_REAL else "paper_simulation",
            "data_mode": "index_option_quote_polling",
            "spot": spot,
            "spot_source": spot.get("spot_source"),
            "spot_value": spot.get("spot"),
            "missing_quote_keys": [],
            "warnings": list(spot.get("warnings") or []),
            "next_action": spot.get("next_action") or "",
        }
        if spot.get("blockers"):
            return {
                "blocked": True,
                "blockers": list(spot.get("blockers") or []),
                "warnings": list(spot.get("warnings") or []),
                "next_action": spot.get("next_action") or "",
                "diagnostics": base_diagnostics,
            }

        candle_context = self._live_index_candle_context(client, underlying, mode, settings, spot)
        base_diagnostics["data_mode"] = "live_index_tick_candles_option_quote_polling"
        base_diagnostics["live_index_candle_source"] = candle_context.get("source")
        base_diagnostics["live_index_candle_count"] = candle_context.get("candle_count")
        base_diagnostics["live_index_candle_interval"] = candle_context.get("interval")
        base_diagnostics["live_index_latest_candle"] = candle_context.get("latest_candle") or {}
        base_diagnostics["live_index_candle_backfill"] = candle_context.get("backfill") or {}
        base_diagnostics["warnings"].extend(candle_context.get("warnings") or [])
        if not candle_context.get("candles"):
            return {
                "blocked": True,
                "blockers": [f"Live {underlying} index candles are unavailable; retrying Zerodha live quote/history fetch."],
                "warnings": candle_context.get("warnings") or [],
                "next_action": "Keep Paper/Real Zerodha connected; Options Auto will retry and backfill the candle gap.",
                "diagnostics": base_diagnostics,
            }

        config = SYMBOL_CONFIG.get(underlying) or SYMBOL_CONFIG["NIFTY"]
        option_exchange = str(config.get("option_exchange") or ("BFO" if underlying == "SENSEX" else "NFO")).upper()
        instruments = self._client_instruments(client, option_exchange)
        chain = OptionChainBuilder().build(
            instruments,
            underlying,
            float(spot.get("spot") or 0),
            span=_int_setting(settings.get("atm_scan_strike_span"), 4),
            strike_step=config.get("strike_step"),
            expiry=payload.get("expiry") or payload.get("option_expiry"),
        )
        diagnostics = {
            **base_diagnostics,
            "atm_strike": chain.get("atm"),
            "strike_step": chain.get("strike_step"),
            "candidate_span": chain.get("span"),
            "candidate_count": chain.get("contracts_requested"),
            "contracts_found": chain.get("contracts_found"),
            "contracts_requested": chain.get("contracts_requested"),
            "missing_contracts": chain.get("missing_contracts") or [],
            "options_data_health": {
                "underlying": underlying,
                "spot_source": spot.get("spot_source"),
                "spot": spot.get("spot"),
                "atm_strike": chain.get("atm"),
                "strike_step": chain.get("strike_step"),
                "candidate_span": chain.get("span"),
                "candidate_count": chain.get("contracts_requested"),
                "contracts_found": chain.get("contracts_found"),
            },
        }
        if not chain.get("contracts"):
            return {
                "blocked": True,
                "blockers": [f"No {underlying} option contracts found around ATM {chain.get('atm')} on {option_exchange}."],
                "warnings": [],
                "next_action": "Refresh Paper Data Zerodha instruments or select a valid expiry.",
                "diagnostics": diagnostics,
            }

        quote_result = OptionsQuoteProvider(client, source=source).quote_candidates(chain["contracts"])
        valid_quote_count = int(quote_result.get("valid_quote_count") or 0)
        diagnostics.update({
            "quotes": quote_result.get("quotes") or {},
            "missing_quote_keys": quote_result.get("missing_quote_keys") or [],
            "quote_warnings": quote_result.get("warnings") or [],
            "valid_quote_count": valid_quote_count,
            "requested_quote_keys": quote_result.get("requested_quote_keys") or [],
            "options_data_health": {
                **diagnostics["options_data_health"],
                "valid_quote_count": valid_quote_count,
                "missing_quote_keys": quote_result.get("missing_quote_keys") or [],
            },
        })
        if valid_quote_count <= 0:
            return {
                "blocked": True,
                "blockers": [f"No valid {underlying} option quotes returned from {'Real Zerodha' if mode == MODE_REAL else 'Paper Data Zerodha'}."],
                "warnings": quote_result.get("warnings") or [],
                "next_action": "Check option quote permissions, instrument expiry, and Zerodha quote availability.",
                "diagnostics": diagnostics,
            }

        return {
            "payload": {
                "spot": spot.get("spot"),
                "data_source": source,
                "quote_age_seconds": spot.get("age_seconds") or 0,
                "index_history": candle_context.get("candles") or [],
                "index_candles": candle_context.get("candles") or [],
            },
            "instruments": chain["contracts"],
            "quotes": quote_result.get("quotes") or {},
            "diagnostics": diagnostics,
        }

    def _live_index_candle_context(self, client: Any, underlying: str, mode: str, settings: dict[str, Any], spot: dict[str, Any]) -> dict[str, Any]:
        token = None
        warnings: list[str] = []
        try:
            config = SYMBOL_CONFIG.get(underlying) or SYMBOL_CONFIG["NIFTY"]
            token = self._index_token(client, underlying, str(config.get("index_exchange") or "NSE"))
        except Exception as exc:
            warnings.append(f"Index token lookup for candle backfill failed; live tick candle still active: {exc}")
        result = self.live_index_candles.update(
            client=client,
            instrument_token=token,
            underlying=underlying,
            mode=mode,
            interval=settings.get("chart_interval") or "3minute",
            spot=float(spot.get("spot") or 0),
            timestamp=spot.get("timestamp"),
            volume=spot.get("volume"),
        )
        result["warnings"] = list(dict.fromkeys(warnings + list(result.get("warnings") or [])))
        return result

    def _blocked_data_decision(self, mode: str, settings: dict[str, Any], live_data: dict[str, Any]) -> dict[str, Any]:
        blockers = list(dict.fromkeys(live_data.get("blockers") or ["Options Auto market data is unavailable."]))
        warnings = list(dict.fromkeys(live_data.get("warnings") or []))
        diagnostics = dict(live_data.get("diagnostics") or {})
        next_action = live_data.get("next_action") or diagnostics.get("next_action") or ""
        return {
            "mode": mode,
            "timestamp": pd.Timestamp.now().isoformat(),
            "market_cue": {},
            "regime": {"recommended_side": SIDE_WAIT, "regime": "blocked_by_data", "no_trade_reason": blockers[0]},
            "selected_side": SIDE_WAIT,
            "selected_contract": {},
            "selection": {"side": SIDE_WAIT, "selected": None, "score": 0.0, "candidates": [], "blockers": blockers},
            "trade_score": {"score": 0.0, "breakdown": {}, "weights": {}},
            "data_quality": {"allowed": False, "state": "BLOCKED_BY_DATA", "blockers": blockers, "warnings": warnings},
            "theta_premium_risk": {"allowed": False, "blockers": blockers, "warnings": warnings},
            "options_risk": {"allowed": False, "blockers": blockers, "warnings": warnings},
            "risk": {"allowed": True, "blockers": [], "warnings": []},
            "discipline": {"allowed": True, "blockers": [], "warnings": []},
            "entry_timing": {"allowed": False, "state": "BLOCKED_BY_DATA", "blockers": blockers, "warnings": warnings},
            "execution": {"allowed": mode != MODE_REAL, "blockers": [], "warnings": []},
            "governor": {"allowed": False, "state": "BLOCKED_BY_DATA", "blockers": blockers, "warnings": warnings, "mode": mode},
            "position_size": {"quantity": 0, "lots": 0, "reason": blockers[0]},
            "trade_plan": {},
            "allowed": False,
            "blockers": blockers,
            "warnings": warnings,
            "explanation": blockers[0],
            "next_action": next_action,
            "decision_snapshot": {"allowed": False, "blockers": blockers, "governor_state": "BLOCKED_BY_DATA"},
            "real_execution_enabled": mode == MODE_REAL,
            "real_execution_reason": "Real execution blocked until Options Auto market data is available." if mode == MODE_REAL else REAL_EXECUTION_DISABLED_REASON,
            **diagnostics,
        }

    def start_shadow(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {**dict(payload or {}), "mode": MODE_SHADOW}
        result = self.evaluate(payload)
        self.session.status = "SHADOW_RUNNING"
        result["session"] = self.session.to_dict()
        result["message"] = "Shadow mode is running in decision-only mode. No paper or real order will be placed."
        return result

    def start_paper(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            payload = {**dict(payload or {}), "mode": MODE_PAPER}
            result = self._evaluate_locked(payload)
            self.mode_guard.assert_paper_allowed()
            self._sync_paper_lifecycle_to_session_locked()
            self.session.status = "PAPER_SCANNING"
            self._start_live_scan_locked(MODE_PAPER, payload, status="PAPER_SCANNING")
            result["session"] = self.session.to_dict()
            result["live_scan"] = self._live_scan_state_locked()
            result["message"] = "Paper live scanner started. It will keep polling Zerodha paper data and re-check entries until stopped."
            return result

    def _start_live_scan_locked(self, mode: str, payload: dict[str, Any], status: str) -> None:
        self._stop_live_scan_locked(reason="restart")
        self._live_scan_stop = threading.Event()
        self._live_scan_mode = normalize_mode(mode)
        self._live_scan_payload = self._scan_payload(payload, self._live_scan_mode)
        self._live_scan_interval_seconds = self._live_scan_interval_locked()
        self._live_scan_started_at = datetime.now().isoformat(timespec="seconds")
        self._live_scan_last_cycle = ""
        self._live_scan_last_error = ""
        self._live_scan_cycle_count = 0
        self.session.status = status
        stop_event = self._live_scan_stop
        self._live_scan_thread = threading.Thread(target=self._live_scan_loop, args=(stop_event,), daemon=True)
        self._live_scan_thread.start()
        self.logger.log("INFO", "Options Auto live scanner started", mode=self._live_scan_mode, interval_seconds=self._live_scan_interval_seconds)

    def _stop_live_scan_locked(self, reason: str = "") -> None:
        if self._live_scan_thread and self._live_scan_thread.is_alive() and not self._live_scan_stop.is_set():
            self.logger.log("INFO", "Options Auto live scanner stop requested", mode=self._live_scan_mode, reason=reason)
        self._live_scan_stop.set()
        self.live_index_candles.stop()

    def _live_scan_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(max(0.2, float(self._live_scan_interval_seconds or 1.0))):
            with self._lock:
                if stop_event is not self._live_scan_stop:
                    break
                if stop_event.is_set() or self._live_scan_mode not in {MODE_PAPER, MODE_REAL}:
                    break
                try:
                    if self._live_scan_mode == MODE_REAL:
                        self._sync_real_option_positions_locked()
                    self._run_live_scan_cycle_locked()
                    self._live_scan_cycle_count += 1
                    self._live_scan_last_cycle = datetime.now().isoformat(timespec="seconds")
                    self._live_scan_last_error = ""
                    self._live_scan_interval_seconds = self._live_scan_interval_locked()
                except Exception as exc:
                    self._live_scan_last_error = str(exc)
                    self.logger.log("ERROR", "Options Auto live scanner cycle failed", mode=self._live_scan_mode, error=str(exc))

    def _run_live_scan_cycle_locked(self) -> dict[str, Any]:
        mode = self._live_scan_mode
        payload = {
            **dict(self._live_scan_payload or {}),
            "mode": mode,
            "settings": dict(self.settings),
            "kite_profile": dict(self.mode_guard.kite_profile or {}),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        result = self._evaluate_current_config_locked(payload, mode)
        live_scan_action: dict[str, Any]
        if mode == MODE_PAPER:
            live_scan_action = self._paper_live_scan_action_locked(result, payload)
            if self.session.status not in {"PAPER_APPROVAL_PENDING", "PAPER_ENTRY_PENDING", "PAPER_TRADE_ACTIVE"}:
                self.session.status = "PAPER_SCANNING"
        else:
            live_scan_action = self._real_live_scan_action_locked(result, payload)
            if self.session.status != "REAL_ENTRY_ORDER_OPEN":
                self.session.status = "REAL_DRY_RUN_SCANNING" if live_scan_action.get("dry_run") else "REAL_SCANNING"
        result["live_scan_action"] = live_scan_action
        self.session.last_decision = {**dict(self.session.last_decision or {}), "live_scan_action": live_scan_action}
        return result

    def _paper_live_scan_action_locked(self, decision: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.get("auto_entry_enabled"):
            return {"action": "SCAN_ONLY", "reason": "Auto entry is disabled."}
        if self._paper_lifecycle_active():
            return {"action": "HOLD", "reason": "Paper approval, pending entry, or active trade already exists."}
        if not decision.get("allowed"):
            return {"action": "HOLD", "reason": "Decision blocked by governor.", "blockers": decision.get("blockers") or []}
        if self.settings.get("ask_permission_before_entry", True):
            pending = self.paper_lifecycle.create_pending(decision, int(self.settings.get("approval_timeout_seconds") or 30))
            self.session.status = "PAPER_APPROVAL_PENDING"
            self.logger.log("INFO", "Options Auto paper live scan approval pending", approval_id=pending.get("approval_id"))
            return {"action": "APPROVAL_CREATED", "approval_id": pending.get("approval_id")}
        final_validation = self._execute_paper_decision_locked(decision, payload)
        return final_validation

    def _execute_paper_decision_locked(self, decision: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        final_validation = self._final_entry_validation(decision, payload)
        if self.settings.get("fast_final_validation_enabled") and not final_validation.get("allowed"):
            blockers = list(dict.fromkeys((decision.get("blockers") or []) + final_validation.get("blockers", [])))
            self.session.record_rejection("; ".join(blockers), {"mode": MODE_PAPER, "stage": "LIVE_SCAN_FINAL_VALIDATION"})
            return {"action": "BLOCKED_FINAL_VALIDATION", "final_validation": final_validation, "blockers": blockers}
        if final_validation.get("entry_limit") and decision.get("trade_plan"):
            decision["trade_plan"] = {**decision["trade_plan"], "entry_price": final_validation["entry_limit"]}
        pending = self.paper_lifecycle.create_pending(decision, int(self.settings.get("approval_timeout_seconds") or 30))
        approved = self.paper_lifecycle.approve(pending["approval_id"])
        self.session.orders.append(approved["entry_order"])
        self.session.active_trades = list(self.paper_lifecycle.active_trades)
        self.session.status = "PAPER_ENTRY_PENDING"
        self.logger.log("INFO", "Options Auto paper live scan entry pending", order_id=approved["entry_order"]["order_id"])
        return {
            "action": "PAPER_ENTRY_PENDING",
            "approval_id": pending.get("approval_id"),
            "order_id": approved["entry_order"].get("order_id"),
            "final_validation": final_validation,
        }

    def _real_live_scan_action_locked(self, decision: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        dry_run = bool(self.settings.get("dry_run_real_only") or not self.settings.get("real_orders_enabled"))
        action = {
            "action": "REAL_SCAN_ONLY",
            "dry_run": dry_run,
            "orders_sent": 0,
            "reason": "Real live scanner refreshes market data continuously; order sending remains behind guarded Start Real Engine.",
        }
        if decision.get("allowed"):
            action["adaptive_dry_run"] = self._real_adaptive_dry_run(decision, payload)
            self.session.record_safety_event("Real Options Auto setup found by live scanner", {"dry_run": dry_run, "orders_sent": 0})
        return action

    def _scan_payload(self, payload: dict[str, Any], mode: str) -> dict[str, Any]:
        payload = dict(payload or {})
        allowed_keys = {
            "expiry",
            "option_expiry",
            "market_cue",
            "fii_dii_status",
            "features",
            "index_history",
            "index_candles",
            "candles",
            "risk_state",
            "market_phase",
            "cue_phase",
            "underlying",
        }
        return {key: payload[key] for key in allowed_keys if key in payload} | {"mode": mode}

    def _live_scan_interval_locked(self) -> float:
        profile = str(self.settings.get("strategy_profile") or "BALANCED").strip().lower()
        if profile == "AGGRESSIVE":
            key = "adaptive_scan_seconds_aggressive"
        elif profile == "CONSERVATIVE":
            key = "adaptive_scan_seconds_conservative"
        else:
            key = "adaptive_scan_seconds_balanced"
        try:
            value = float(self.settings.get(key) or 2)
        except (TypeError, ValueError):
            value = 2.0
        return max(1.0, min(60.0, value))

    def _live_scan_state_locked(self) -> dict[str, Any]:
        running = bool(self._live_scan_thread and self._live_scan_thread.is_alive() and not self._live_scan_stop.is_set())
        return {
            "running": running,
            "mode": self._live_scan_mode,
            "interval_seconds": self._live_scan_interval_seconds,
            "started_at": self._live_scan_started_at,
            "last_cycle": self._live_scan_last_cycle,
            "last_error": self._live_scan_last_error,
            "cycle_count": self._live_scan_cycle_count,
        }

    def execute_paper_plan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.start_paper(payload)
        if not result.get("allowed"):
            return {**result, "paper_order": None, "message": "Paper order not simulated because governor blocked the setup."}
        final_validation = self._final_entry_validation(result, payload or {})
        if self.settings.get("fast_final_validation_enabled") and not final_validation.get("allowed"):
            blocked = {**result, "allowed": False, "blockers": list(dict.fromkeys((result.get("blockers") or []) + final_validation.get("blockers", []))), "final_validation": final_validation, "paper_order": None}
            self.session.record_rejection("; ".join(blocked["blockers"]), {"mode": MODE_PAPER, "stage": "FINAL_VALIDATION"})
            return {**blocked, "message": "Paper entry skipped because fast final validation blocked the setup."}
        if final_validation.get("entry_limit") and result.get("trade_plan"):
            result["trade_plan"] = {**result["trade_plan"], "entry_price": final_validation["entry_limit"]}
        result["final_validation"] = final_validation
        pending = self.paper_lifecycle.create_pending(result, int(self.settings.get("approval_timeout_seconds") or 30))
        approved = self.paper_lifecycle.approve(pending["approval_id"])
        self.session.orders.append(approved["entry_order"])
        self.session.active_trades = list(self.paper_lifecycle.active_trades)
        self.session.status = "PAPER_ENTRY_PENDING"
        self.logger.log("INFO", "Options Auto paper lifecycle entry pending", order_id=approved["entry_order"]["order_id"])
        return {
            **result,
            "approval": pending,
            "paper_order": approved["entry_order"],
            "pending_entry": approved.get("pending_entry"),
            "paper_lifecycle": self.paper_lifecycle.snapshot(),
            "paper_account": self.paper_broker.snapshot(),
            "session": self.session.to_dict(),
        }

    def request_paper_approval(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.start_paper(payload)
        if not result.get("allowed"):
            return {**result, "approval": None, "message": "Approval not created because governor blocked the setup."}
        pending = self.paper_lifecycle.create_pending(result, int(self.settings.get("approval_timeout_seconds") or 30))
        self.session.status = "PAPER_APPROVAL_PENDING"
        self.logger.log("INFO", "Options Auto paper approval pending", approval_id=pending["approval_id"])
        return {**result, "approval": pending, "paper_lifecycle": self.paper_lifecycle.snapshot(), "session": self.session.to_dict()}

    def approve_paper(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        result = self.paper_lifecycle.approve(payload.get("approval_id"), now_epoch=payload.get("now_epoch"))
        if result.get("status") == "ENTRY_PENDING":
            self.session.orders.append(result["entry_order"])
            self.session.status = "PAPER_ENTRY_PENDING"
        elif result.get("trade"):
            self.session.orders.extend([result["entry_order"], result["target_order"], result["stoploss_order"]])
            self.session.active_trades = list(self.paper_lifecycle.active_trades)
            self.session.status = "PAPER_TRADE_ACTIVE"
        else:
            self.session.status = "PAPER_APPROVAL_EXPIRED"
        self.logger.log("INFO", "Options Auto paper approval processed", status=result.get("status"))
        return {**result, "paper_lifecycle": self.paper_lifecycle.snapshot(), "paper_account": self.paper_broker.snapshot(), "session": self.session.to_dict()}

    def reject_paper(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        result = self.paper_lifecycle.reject(payload.get("approval_id"))
        self.session.status = "PAPER_APPROVAL_REJECTED"
        self.logger.log("INFO", "Options Auto paper approval rejected", approval_id=result.get("approval_id"))
        return {**result, "paper_lifecycle": self.paper_lifecycle.snapshot(), "session": self.session.to_dict()}

    def process_paper_market(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        market = payload.get("market") or payload
        adaptive_pending_updates = self._apply_adaptive_pending_entries(market)
        exit_updates = self._apply_paper_exit_decisions(market)
        result = self.paper_lifecycle.process_market(market)
        result["exit_updates"] = exit_updates
        result["adaptive_pending_updates"] = adaptive_pending_updates
        for update in result.get("updates") or []:
            if update.get("action") == "ENTRY_FILLED":
                for key in ("entry_order", "target_order", "stoploss_order"):
                    if update.get(key):
                        self.session.orders.append(update[key])
        self.session.active_trades = list(self.paper_lifecycle.active_trades)
        if self.session.active_trades:
            self.session.status = "PAPER_TRADE_ACTIVE"
        elif self.paper_lifecycle.pending_entries:
            self.session.status = "PAPER_ENTRY_PENDING"
        else:
            self.session.status = "PAPER_IDLE"
        self.logger.log("INFO", "Options Auto paper market processed", updates=len(result.get("updates") or []))
        return {**result, "paper_account": self.paper_broker.snapshot(), "session": self.session.to_dict()}

    def real_dry_run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            payload = {**dict(payload or {}), "mode": MODE_REAL}
            result = self._evaluate_locked(payload)
            result["dry_run"] = True
            result["orders_sent"] = 0
            result["adaptive_dry_run"] = self._real_adaptive_dry_run(result, payload)
            result["real_position_sync"] = self._sync_real_option_positions_locked(payload)
            self.session.status = "REAL_DRY_RUN_SCANNING"
            self._start_live_scan_locked(MODE_REAL, payload, status="REAL_DRY_RUN_SCANNING")
            result["session"] = self.session.to_dict()
            result["live_scan"] = self._live_scan_state_locked()
            result["message"] = "Real dry-run scanner started. It keeps polling Real Zerodha data and sends zero orders."
            return result

    def real_preflight_check(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        settings = {**self.settings, **dict(payload.get("settings") or {})}
        mode = normalize_mode(payload.get("mode") or settings.get("mode") or MODE_REAL)
        client = self.kite_client_provider("LIVE") if mode == MODE_REAL else None
        settings = self._real_capability_settings(settings, mode, client, payload)
        self._configure_locked({**settings, "mode": mode}, kite_profile=payload.get("kite_profile") or {}, preserve_session=bool(self._live_scan_state_locked().get("running")))
        self.real_api_manager.client = client
        broker_orders = self._broker_orders(client, payload)
        positions = self._broker_positions(client, payload)
        trade_plan = payload.get("trade_plan") or self.session.last_decision.get("trade_plan") or {}
        watchdog = self.watchdog.evaluate({
            "mode": mode,
            "ui_alive": payload.get("ui_alive", True),
            "data_feed_alive": payload.get("data_feed_alive", True),
            "kite_connected": bool(client) if mode == MODE_REAL else False,
            "active_position": bool(self.session.active_trades),
            "position_protected": self._active_positions_protected(),
            "last_update_age_seconds": payload.get("last_update_age_seconds", 0),
            "memory_pct": payload.get("memory_pct", 0),
        }, self.settings)
        result = self.real_controller.preflight(
            self.mode_guard,
            client,
            self.settings,
            local_orders=self.session.orders,
            active_trades=self.session.active_trades,
            broker_orders=broker_orders,
            positions=positions,
            trade_plan=trade_plan,
            profile=payload.get("kite_profile") or self.mode_guard.kite_profile,
            results_writable=self._results_writable(),
            watchdog_ready=watchdog.get("new_entries_allowed", False),
            market_open=bool(payload.get("market_open", True)),
            instruments_valid=bool(payload.get("instruments_valid", True)),
            static_ip_confirmed=payload.get("static_ip_confirmed"),
        )
        self.session.record_safety_event("Real preflight checked", {"state": result["state"], "blockers": result["blockers"]})
        result["watchdog"] = watchdog
        result["session"] = self.session.to_dict()
        self.logger.log("INFO", "Options Auto real preflight checked", allowed=result["allowed"], blockers=result["blockers"])
        return result

    def real_reconcile(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        client = self.kite_client_provider("LIVE")
        broker_orders = self._broker_orders(client, payload)
        positions = self._broker_positions(client, payload)
        trade_plan = payload.get("trade_plan") or self.session.last_decision.get("trade_plan") or {}
        result = self.real_controller.reconcile(self.session.orders, broker_orders, positions, trade_plan)
        self.session.record_safety_event("Real reconciliation checked", {"state": result["state"], "blockers": result["blockers"]})
        result["session"] = self.session.to_dict()
        self.logger.log("INFO", "Options Auto real reconciliation checked", ok=result["ok"], blockers=result["blockers"])
        return result

    def real_stop_new_entries(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        result = self.real_controller.stop_new_entries(payload.get("source") or "UI", payload.get("reason") or "")
        self.session.record_safety_event("Real Stop New Entries activated", result)
        self.logger.log("WARN", "Options Auto real Stop New Entries activated", source=result["source"])
        return {**result, "session": self.session.to_dict()}

    def stop_live_scan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            payload = dict(payload or {})
            mode = normalize_mode(payload.get("mode") or self._live_scan_mode or self.settings.get("mode"))
            self._stop_live_scan_locked(reason=payload.get("reason") or "stop requested")
            if mode == MODE_PAPER:
                self._sync_paper_lifecycle_to_session_locked()
            elif mode == MODE_REAL:
                self._sync_real_option_positions_locked(payload)
            if mode == MODE_REAL:
                self.session.status = "REAL_STOPPED"
            elif mode == MODE_PAPER:
                self.session.status = "PAPER_STOPPED"
            else:
                self.session.status = "IDLE"
            result = {"stopped": True, "mode": mode, "session": self.session.to_dict(), "live_scan": self._live_scan_state_locked()}
            self.logger.log("INFO", "Options Auto live scanner stopped", mode=mode)
            return result

    def kill_switch(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            payload = dict(payload or {})
            mode = normalize_mode(payload.get("mode") or self._live_scan_mode or self.settings.get("mode"))
            self._stop_live_scan_locked(reason="kill switch")
            paper_cancel = {}
            real_runtime = {}
            if mode == MODE_PAPER:
                if self.paper_lifecycle.pending_approval:
                    self.paper_lifecycle.pending_approval["status"] = "KILL_SWITCH_CANCELLED"
                    self.paper_lifecycle.pending_approval = None
                if self.paper_lifecycle.pending_entries:
                    paper_cancel = self.paper_lifecycle.cancel_pending_entry(reason="Kill switch cancelled pending paper entries.")
                self._sync_paper_lifecycle_to_session_locked()
            if mode == MODE_REAL:
                real_runtime = self.real_controller.enter_safe_mode(payload.get("source") or "UI", payload.get("reason") or "Options Auto kill switch activated.")
                self._sync_real_option_positions_locked(payload)
            self.session.status = f"{mode}_KILL_SWITCH_ACTIVE"
            event = {
                "mode": mode,
                "source": payload.get("source") or "UI",
                "reason": payload.get("reason") or "Operator activated Options Auto kill switch.",
                "paper_cancel": paper_cancel,
                "real_runtime": real_runtime,
            }
            self.session.record_safety_event("Options Auto kill switch activated", event)
            self.logger.log("WARN", "Options Auto kill switch activated", mode=mode)
            return {"killed": True, **event, "session": self.session.to_dict(), "live_scan": self._live_scan_state_locked()}

    def real_safe_mode(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        result = self.real_controller.enter_safe_mode(payload.get("source") or "UI", payload.get("reason") or "")
        self.session.record_safety_event("Real Safe Mode activated", result)
        self.logger.log("WARN", "Options Auto real Safe Mode activated", source=result["source"])
        return {**result, "session": self.session.to_dict()}

    def real_emergency_plan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        mode = normalize_mode(payload.get("mode") or (payload.get("settings") or {}).get("mode") or self.settings.get("mode"))
        if mode != self.mode_guard.mode:
            self.configure({**self.settings, **dict(payload.get("settings") or {}), "mode": mode}, kite_profile=payload.get("kite_profile") or {})
        client = self.kite_client_provider("LIVE") if mode == MODE_REAL else None
        positions = payload.get("positions")
        if positions is None:
            positions = self._broker_positions(client, payload)
        result = self.real_controller.emergency_exit_plan(self.mode_guard, positions, self.settings, confirmed=bool(payload.get("confirmed")))
        self.session.record_safety_event("Real emergency exit plan generated", {"actions": result["actions"], "blockers": result["blockers"]})
        result["session"] = self.session.to_dict()
        self.logger.log("WARN", "Options Auto real emergency plan generated", actions=len(result["actions"]))
        return result

    def place_real_order(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            payload = {**dict(payload or {}), "mode": MODE_REAL}
            client = self.kite_client_provider("LIVE")
            if not client:
                return self._blocked_real_order(["Real Money Zerodha is not connected."])
            settings = self._real_capability_settings({**self.settings, **dict(payload.get("settings") or {})}, MODE_REAL, client, payload)
            payload["settings"] = settings
            self._configure_locked(settings, kite_profile=payload.get("kite_profile") or {}, preserve_session=False)
            self.real_api_manager.client = client

            preflight = self.real_preflight_check({
                **payload,
                "settings": settings,
                "market_open": payload.get("market_open", True),
                "instruments_valid": payload.get("instruments_valid", True),
            })
            if not preflight.get("allowed"):
                blocked = self._blocked_real_order(preflight.get("blockers") or ["Real preflight failed."], preflight=preflight)
                self._start_live_scan_locked(MODE_REAL, payload, status="REAL_SCANNING")
                return {**blocked, "live_scan": self._live_scan_state_locked()}

            decision = dict(payload.get("decision") or {})
            if not decision:
                decision = self._evaluate_current_config_locked(payload, MODE_REAL)
            if not decision.get("allowed"):
                blocked = self._blocked_real_order(decision.get("blockers") or ["Decision pipeline blocked real order."], preflight=preflight, decision=decision)
                self._start_live_scan_locked(MODE_REAL, payload, status="REAL_SCANNING")
                return {**blocked, "live_scan": self._live_scan_state_locked()}

            final_validation = self._final_entry_validation(decision, payload)
            if not final_validation.get("allowed"):
                blocked = self._blocked_real_order(final_validation.get("blockers") or ["Fast final validation blocked real order."], preflight=preflight, decision=decision, final_validation=final_validation)
                self._start_live_scan_locked(MODE_REAL, payload, status="REAL_SCANNING")
                return {**blocked, "live_scan": self._live_scan_state_locked()}

            selected = dict(decision.get("selected_contract") or {})
            trade_plan = {**dict(decision.get("trade_plan") or {}), "entry_price": final_validation.get("entry_limit") or (decision.get("trade_plan") or {}).get("entry_price")}
            order_request, order_blockers = self._real_entry_order_request(selected, trade_plan, preflight)
            if order_blockers:
                blocked = self._blocked_real_order(order_blockers, preflight=preflight, decision=decision, final_validation=final_validation)
                self._start_live_scan_locked(MODE_REAL, payload, status="REAL_SCANNING")
                return {**blocked, "live_scan": self._live_scan_state_locked()}

            adapter = KiteOrderAdapter(self.real_api_manager, self.mode_guard)
            controller_result = self.real_controller.place_entry_buy_limit(self.mode_guard, adapter, order_request, preflight)
            if not controller_result.get("real_order_sent"):
                blocked = self._blocked_real_order(controller_result.get("blockers") or ["Real entry order failed."], preflight=preflight, decision=decision, final_validation=final_validation, execution=controller_result)
                self._start_live_scan_locked(MODE_REAL, payload, status="REAL_SCANNING")
                return {**blocked, "live_scan": self._live_scan_state_locked()}

            entry_order = controller_result["entry_order"]
            self.session.orders.append(entry_order)
            self.session.status = "REAL_ENTRY_ORDER_OPEN"
            self.session.record_safety_event("Real Options Auto entry order sent", {"order_id": entry_order.get("order_id"), "tradingsymbol": entry_order.get("tradingsymbol")})
            self.logger.log("WARN", "Options Auto real entry order sent", order_id=entry_order.get("order_id"), tradingsymbol=entry_order.get("tradingsymbol"))
            self._start_live_scan_locked(MODE_REAL, payload, status="REAL_SCANNING")
            return {
                "allowed": True,
                "real_order_sent": True,
                "order_stage": "ENTRY_ORDER_OPEN",
                "entry_order": entry_order,
                "trade_plan": trade_plan,
                "preflight": preflight,
                "final_validation": final_validation,
                "execution": controller_result,
                "session": self.session.to_dict(),
                "live_scan": self._live_scan_state_locked(),
                "message": "Real BUY LIMIT entry sent through guarded Kite adapter. Real scanner remains active for fresh data and monitoring.",
            }

    def upload_fii_dii_csv(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        file_name = payload.get("file_name") or payload.get("name") or "fii_dii.csv"
        csv_text = payload.get("csv_text") or payload.get("text") or ""
        file_path = payload.get("csv_file") or payload.get("file") or payload.get("path")
        if file_path and not csv_text:
            file_name = payload.get("file_name") or os.path.basename(str(file_path))
            try:
                with open(file_path, "r", encoding="utf-8-sig") as handle:
                    csv_text = handle.read()
            except OSError as exc:
                parsed = {"status": "FAILED", "file_name": file_name, "warnings": [f"Could not read CSV: {exc}"], "fii_net": None, "dii_net": None}
                snapshot = fii_dii_status_from_upload(parsed, payload.get("phase") or "PREMARKET")
                self.latest_fii_dii_snapshot = snapshot
                return snapshot
        parsed = parse_fii_dii_csv_text(csv_text, file_name=file_name)
        snapshot = fii_dii_status_from_upload(parsed, payload.get("phase") or "PREMARKET")
        self.latest_fii_dii_snapshot = snapshot
        self.logger.log("INFO", "Options Auto FII/DII CSV uploaded", status=snapshot.get("status"), file_name=file_name)
        return snapshot

    def latest_fii_dii_status(self, phase: str = "PREMARKET") -> dict[str, Any]:
        phase = str(phase or "PREMARKET").upper()
        if phase != "PREMARKET":
            return {
                "status": "IGNORED",
                "file_name": (self.latest_fii_dii_snapshot or {}).get("file_name", ""),
                "uploaded_at": (self.latest_fii_dii_snapshot or {}).get("uploaded_at", ""),
                "fii_net": None,
                "dii_net": None,
                "score": 0.0,
                "fii_dii_score": 0.0,
                "warning": "FII/DII ignored outside pre-market.",
                "used_for_phase": phase,
            }
        if self.latest_fii_dii_snapshot:
            return dict(self.latest_fii_dii_snapshot)
        warning = "FII/DII CSV not uploaded; treated as neutral for pre-market cue."
        return {
            "status": "NEUTRAL_MISSING_UPLOAD",
            "file_name": "",
            "uploaded_at": "",
            "fii_net": None,
            "dii_net": None,
            "score": 0.0,
            "fii_dii_score": 0.0,
            "warning": warning,
            "warnings": [warning],
            "used_for_phase": "PREMARKET",
        }

    def premarket_market_cue(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        phase = str(payload.get("phase") or "PREMARKET").upper()
        cue = self.market_cue_engine.evaluate(self._market_cue_payload({**payload, "phase": phase}), phase=phase).to_dict()
        return {"market_cue": cue, "fii_dii_status": cue.get("fii_dii_status"), "settings": self.settings}

    def backtest(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        self.configure({**dict(payload.get("settings") or {}), "mode": MODE_BACKTEST})
        candles = self._frame(payload.get("index_candles") or payload.get("candles") or [])
        options = [self._frame(frame) for frame in payload.get("option_candles") or []]
        source_metadata: dict[str, Any] = {"data_source": "provided_candles"}
        if self._should_fetch_backtest_history(payload, candles, options):
            candles, options, source_metadata = self._load_backtest_history(payload, self.settings)
        result = self.backtest_engine.run(candles, options, self.settings)
        result["data_source"] = source_metadata.get("data_source")
        result["data_source_label"] = source_metadata.get("data_source_label")
        result["source_metadata"] = source_metadata
        self.session.status = "BACKTEST_COMPLETE"
        self.session.record_decision({
            "mode": MODE_BACKTEST,
            "allowed": False,
            "blockers": ["Backtest is decision-only in foundation phase."],
            "summary": result,
        })
        report = self.report_writer.write(self.mode_guard.session_id, result)
        result["report"] = report
        self.logger.log("INFO", "Options Auto backtest completed", rows=result["rows"])
        return {**result, "session": self.session.to_dict()}

    def _should_fetch_backtest_history(self, payload: dict[str, Any], candles: pd.DataFrame, options: list[pd.DataFrame]) -> bool:
        source = str(payload.get("data_source") or payload.get("source") or "").strip().lower()
        if source in {"zerodha", "zerodha_historical", "paper_zerodha"}:
            return True
        return bool(payload.get("trade_date") or payload.get("backtest_date") or payload.get("date")) and (candles.empty or not options)

    def _load_backtest_history(self, payload: dict[str, Any], settings: dict[str, Any]) -> tuple[pd.DataFrame, list[pd.DataFrame], dict[str, Any]]:
        client = self.kite_client_provider(MODE_PAPER) or self.kite_client_provider("PAPER")
        if not client:
            raise ValueError("Connect Paper Data Zerodha in the main app before running Options Auto historical backtest.")
        underlying = str(payload.get("underlying") or settings.get("underlying") or "NIFTY").upper()
        config = SYMBOL_CONFIG.get(underlying) or SYMBOL_CONFIG["NIFTY"]
        trade_day = _parse_trade_day(payload.get("trade_date") or payload.get("backtest_date") or payload.get("date"))
        from_dt = datetime.combine(trade_day, dt_time(9, 15))
        to_dt = datetime.combine(trade_day, dt_time(15, 30))
        interval = str(payload.get("interval") or settings.get("chart_interval") or "3minute")
        index_token = self._index_token(client, underlying, str(config.get("index_exchange") or "NSE"))
        index_frame = self._historical_frame(client, index_token, from_dt, to_dt, interval, f"{underlying} index")
        spot_result = OptionsAutoIndexDataProvider().get_spot(underlying, MODE_BACKTEST, payload=payload, index_candles=index_frame)
        if spot_result.get("blockers"):
            raise ValueError((spot_result.get("blockers") or [f"Could not infer ATM strike from {underlying} historical candles."])[0])
        option_exchange = str(config.get("option_exchange") or "NFO").upper()
        expiry = payload.get("expiry") or payload.get("option_expiry")
        chain = OptionChainBuilder().build(
            self._client_instruments(client, option_exchange),
            underlying,
            float(spot_result.get("spot") or 0),
            span=_int_setting(settings.get("atm_scan_strike_span"), 4),
            strike_step=config.get("strike_step"),
            expiry=expiry,
        )
        contracts = list(chain.get("contracts") or [])
        if not contracts:
            raise ValueError(
                f"Expired option instrument metadata unavailable for selected date."
                if trade_day < date.today()
                else f"No {underlying} option contracts found around ATM {chain.get('atm')} on {option_exchange}."
            )
        option_frames = []
        for contract in contracts:
            frame = self._historical_frame(
                client,
                contract.get("instrument_token"),
                from_dt,
                to_dt,
                interval,
                str(contract.get("tradingsymbol") or contract.get("instrument_token") or "option"),
            )
            option_frames.append(_decorate_option_frame(frame, contract, underlying, option_exchange))
        metadata = {
            "data_source": "zerodha_historical",
            "data_source_label": "Zerodha Historical (Paper Data)",
            "underlying": underlying,
            "trade_date": trade_day.isoformat(),
            "from": from_dt.isoformat(sep=" "),
            "to": to_dt.isoformat(sep=" "),
            "interval": interval,
            "index_token": index_token,
            "spot": spot_result.get("spot"),
            "spot_source": spot_result.get("spot_source"),
            "atm_strike": chain.get("atm"),
            "strike_step": chain.get("strike_step"),
            "candidate_span": chain.get("span"),
            "contracts_requested": chain.get("contracts_requested"),
            "contracts_found": chain.get("contracts_found"),
            "contracts": [_contract_summary(contract) for contract in contracts],
            "historical_proxy_quote_warning": "Backtest uses OHLC-derived historical quote proxies for option bid/ask/depth.",
        }
        return index_frame, option_frames, metadata

    def _index_token(self, client: Any, underlying: str, exchange: str) -> Any:
        if underlying == "NIFTY" and hasattr(client, "get_nifty50_token"):
            return client.get_nifty50_token()
        wanted = {
            "NIFTY": {"NIFTY 50", "NIFTY"},
            "SENSEX": {"SENSEX"},
        }.get(underlying, {underlying})
        for instrument in self._client_instruments(client, exchange):
            symbol = str(instrument.get("tradingsymbol") or "").upper()
            name = str(instrument.get("name") or "").upper()
            if symbol in wanted or name in wanted:
                token = instrument.get("instrument_token")
                if token not in ("", None):
                    return token
        raise ValueError(f"Could not find {underlying} index instrument token on {exchange}.")

    def _find_option_contract(self, client: Any, underlying: str, exchange: str, option_type: str, strike: float, expiry: Any, trade_day: date) -> dict[str, Any]:
        wanted_expiry = _expiry_text(expiry)
        matches = []
        for instrument in self._client_instruments(client, exchange):
            if str(instrument.get("instrument_type") or "").upper() != option_type:
                continue
            if float(instrument.get("strike") or 0) != float(strike):
                continue
            instrument_name = str(instrument.get("name") or instrument.get("underlying") or "").upper()
            tradingsymbol = str(instrument.get("tradingsymbol") or "").upper()
            if instrument_name not in {"", underlying} and not tradingsymbol.startswith(underlying):
                continue
            expiry_text = _expiry_text(instrument.get("expiry"))
            if wanted_expiry and expiry_text != wanted_expiry:
                continue
            if expiry_text and expiry_text < trade_day.isoformat():
                continue
            matches.append(dict(instrument))
        if not matches and hasattr(client, "find_option_contract"):
            try:
                return dict(client.find_option_contract(option_type=option_type, strike=strike, expiry=expiry, name=underlying))
            except Exception:
                pass
        if not matches:
            raise ValueError(f"No {underlying} {option_type} contract found for ATM strike {strike} on {exchange}.")
        matches.sort(key=lambda item: (_expiry_text(item.get("expiry")) or "9999-12-31", str(item.get("tradingsymbol") or "")))
        return matches[0]

    def _client_instruments(self, client: Any, exchange: str) -> list[dict[str, Any]]:
        if not client:
            return []
        if hasattr(client, "instruments"):
            return list(client.instruments(exchange) or [])
        kite = getattr(client, "kite", None)
        if kite and hasattr(kite, "instruments"):
            return list(kite.instruments(exchange) or [])
        return []

    def _historical_frame(self, client: Any, token: Any, from_dt: datetime, to_dt: datetime, interval: str, label: str) -> pd.DataFrame:
        if token in ("", None):
            raise ValueError(f"Missing instrument token for {label}.")
        if hasattr(client, "historical_candles"):
            frame = client.historical_candles(token, from_dt, to_dt, interval=interval)
        elif hasattr(client, "historical_data"):
            frame = client.historical_data(int(token), from_dt, to_dt, interval)
        else:
            kite = getattr(client, "kite", None)
            if not kite or not hasattr(kite, "historical_data"):
                raise ValueError("Connected Paper Data Zerodha client does not expose historical candles.")
            frame = kite.historical_data(instrument_token=int(token), from_date=from_dt, to_date=to_dt, interval=interval)
        if isinstance(frame, pd.DataFrame):
            result = self._frame(frame.to_dict("records"))
        else:
            result = self._frame(frame or [])
        if result.empty:
            raise ValueError(f"Zerodha returned no historical candles for {label}.")
        return result

    def readiness(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        mode = normalize_mode(payload.get("mode") or self.settings.get("mode"))
        watchdog = self.watchdog.evaluate({
            "mode": mode,
            "ui_alive": True,
            "data_feed_alive": payload.get("data_feed_alive", True),
            "kite_connected": bool(self.kite_client_provider("LIVE")) if mode == MODE_REAL else bool(self.kite_client_provider("PAPER")),
            "order_monitor_alive": payload.get("order_monitor_alive", True),
            "oco_monitor_alive": payload.get("oco_monitor_alive", True),
            "active_position": bool(self.session.active_trades),
            "position_protected": self._active_positions_protected(),
            "last_update_age_seconds": payload.get("last_update_age_seconds", 0),
            "memory_pct": payload.get("memory_pct", 0),
            "cpu_pct": payload.get("cpu_pct", 0),
            "latency_log": payload.get("latency_log") or {},
            "locked": payload.get("locked", False),
        }, self.settings)
        reconciliation = self.reconciliation.reconcile(self.session.orders, payload.get("broker_orders") or [], payload.get("positions") or [])
        real = self.real_preflight.validate(mode, self.kite_client_provider("LIVE"), self.settings).to_dict() if mode == MODE_REAL else {"allowed": True, "state": "NOT_REAL_MODE", "blockers": [], "warnings": []}
        return {
            "mode": mode,
            "watchdog": watchdog,
            "reconciliation": reconciliation,
            "real_preflight": real,
            "ready": watchdog["new_entries_allowed"] and reconciliation["ok"] and not real.get("blockers"),
        }

    def health_status(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        mode = normalize_mode(payload.get("mode") or self.settings.get("mode"))
        active_trades = payload.get("active_trades")
        if active_trades is None:
            active_trades = self.session.active_trades
        position_protected = payload.get("position_protected")
        if position_protected is None:
            position_protected = not active_trades or all(bool(trade.get("position_protected")) for trade in active_trades)
        watchdog = self.watchdog.evaluate({
            "mode": mode,
            "ui_alive": payload.get("ui_alive", True),
            "data_feed_alive": payload.get("data_feed_alive", True),
            "kite_connected": payload.get("kite_connected", bool(self.kite_client_provider("LIVE")) if mode == MODE_REAL else bool(self.kite_client_provider("PAPER"))),
            "order_monitor_alive": payload.get("order_monitor_alive", True),
            "oco_monitor_alive": payload.get("oco_monitor_alive", True),
            "active_position": bool(active_trades),
            "position_protected": position_protected,
            "last_update_age_seconds": payload.get("last_update_age_seconds", 0),
            "memory_pct": payload.get("memory_pct", 0),
            "cpu_pct": payload.get("cpu_pct", 0),
            "latency_log": payload.get("latency_log") or {},
            "locked": payload.get("locked", False),
        }, {**self.settings, **dict(payload.get("settings") or {})})
        result = {
            "mode": mode,
            "watchdog": watchdog,
            "real_safety": self.real_controller.snapshot(),
            "session": self.session.to_dict(),
            "new_entries_allowed": watchdog["new_entries_allowed"],
            "slow_tasks_paused": watchdog["slow_tasks_paused"],
        }
        self.logger.log("INFO", "Options Auto health checked", mode=watchdog["mode"], new_entries_allowed=watchdog["new_entries_allowed"])
        return result

    def exit_decision(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        decision = self.exit_manager.evaluate(payload.get("trade") or {}, payload.get("market") or {}, {**self.settings, **dict(payload.get("settings") or {})})
        if payload.get("apply"):
            decision["position_update"] = self.position_manager.apply_exit_decision(payload.get("trade") or {}, decision, payload.get("market") or {})
        self.logger.log("INFO", "Options Auto exit manager evaluated", action=decision.get("action"))
        return decision

    def shadow_report(self) -> dict[str, Any]:
        report = self.shadow_engine.report()
        report["saved_report"] = self._write_json_report("shadow", "shadow_report.json", report)
        return report

    def shadow_record_outcome(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        signal = self.shadow_engine.record_outcome(int(payload.get("index") or 0), float(payload.get("actual_pnl") or 0), payload.get("outcome") or "")
        self.logger.log("INFO", "Options Auto shadow outcome recorded", index=payload.get("index"), outcome=signal.get("outcome"))
        return {"signal": signal, "report": self.shadow_report()}

    def promotion_status(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.promotion.evaluate((payload or {}).get("metrics") or payload or {})

    def drift_status(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.drift_monitor.evaluate((payload or {}).get("trades") or [])

    def missed_trade_status(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.missed_trade_learning.evaluate((payload or {}).get("decisions") or [])

    def market_replay(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        candles = self._frame(payload.get("candles") or [])
        result = self.market_replay_engine.replay(candles, payload.get("decisions") or [])
        result["saved_report"] = self._write_json_report("replay", "market_replay.json", result)
        self.logger.log("INFO", "Options Auto market replay completed", rows=result["rows"])
        return result

    def telegram_command(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        result = self.telegram_safety.validate(
            payload.get("command"),
            payload.get("user_id"),
            self.settings,
            confirmed=bool(payload.get("confirmed")),
            command_id=payload.get("command_id") or "",
            now_epoch=payload.get("now_epoch"),
            position_snapshot=payload.get("position_snapshot") if "position_snapshot" in payload else self.session.active_trades,
        )
        self.logger.log("INFO", "Options Auto Telegram command checked", command=result["command"], allowed=result["allowed"])
        return result

    def _frame(self, rows: Any) -> pd.DataFrame:
        if isinstance(rows, pd.DataFrame):
            return rows
        if isinstance(rows, list):
            return pd.DataFrame(rows)
        return pd.DataFrame()

    def _real_capability_settings(self, settings: dict[str, Any], mode: str, client: Any | None, payload: dict[str, Any]) -> dict[str, Any]:
        settings = normalize_settings({**dict(settings or {}), "mode": mode})
        if mode != MODE_REAL:
            return settings
        if client:
            settings["confirm_real_mode"] = True
            explicit_dry_run = "dry_run_real_only" in payload or "dry_run_real_only" in dict(payload.get("settings") or {})
            if not explicit_dry_run:
                settings["dry_run_real_only"] = False
            settings["real_orders_enabled"] = not bool(settings.get("dry_run_real_only"))
        return settings

    def _real_entry_order_request(self, selected: dict[str, Any], trade_plan: dict[str, Any], preflight: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        blockers = []
        symbol = trade_plan.get("tradingsymbol") or selected.get("tradingsymbol")
        exchange = trade_plan.get("exchange") or selected.get("exchange") or ("BFO" if "SENSEX" in str(symbol or "").upper() else "NFO")
        token = selected.get("instrument_token") or selected.get("token") or trade_plan.get("instrument_token")
        lot_size = int(_number(trade_plan.get("lot_size"), selected.get("lot_size")))
        quantity = int(_number(trade_plan.get("quantity"), selected.get("quantity")))
        tick = _number(trade_plan.get("tick_size"), selected.get("tick_size") or 0.05)
        entry = round_to_tick(_number(trade_plan.get("entry_price")), tick)
        product = str(trade_plan.get("product") or self.settings.get("order_product") or "NRML").upper()
        margin = _number((preflight.get("evidence") or {}).get("checks", {}).get("available_margin"))
        if not symbol:
            blockers.append("Selected contract tradingsymbol is missing.")
        if not token:
            blockers.append("Selected contract instrument token is missing.")
        if exchange not in {"NFO", "BFO"}:
            blockers.append("Selected contract exchange must be NFO or BFO.")
        if lot_size <= 0:
            blockers.append("Selected contract lot size is invalid.")
        if quantity <= 0:
            blockers.append("Real order quantity is invalid.")
        if lot_size > 0 and quantity % lot_size != 0:
            blockers.append("Real order quantity must be a multiple of lot size.")
        if tick <= 0:
            blockers.append("Selected contract tick size is invalid.")
        if entry <= 0:
            blockers.append("Real order entry price is invalid.")
        if product not in {"NRML", "MIS"}:
            blockers.append("Real order product must be NRML or MIS.")
        if margin and entry > 0 and quantity > 0 and margin < entry * quantity:
            blockers.append("Available margin is insufficient for the entry order value.")
        order_request = {
            "tradingsymbol": symbol,
            "exchange": exchange,
            "instrument_token": token,
            "transaction_type": "BUY",
            "order_type": "LIMIT",
            "quantity": quantity,
            "price": entry,
            "product": product,
            "validity": "DAY",
            "variety": "regular",
            "tag": "OPTIONS_AUTO",
        }
        return order_request, list(dict.fromkeys(blockers))

    def _blocked_real_order(
        self,
        blockers: list[str],
        preflight: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        final_validation: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        blockers = list(dict.fromkeys(blockers or ["Real order blocked by safety checks."]))
        self.session.record_safety_event("Real Options Auto order blocked", {"blockers": blockers})
        return {
            "allowed": False,
            "real_order_sent": False,
            "order_stage": "BLOCKED",
            "blockers": blockers,
            "message": "Real order blocked because " + "; ".join(blockers[:4]),
            "preflight": preflight or {},
            "decision": decision or {},
            "final_validation": final_validation or {},
            "execution": execution or {},
            "session": self.session.to_dict(),
        }

    def _market_cue_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload or {})
        cue_payload = {**payload, **dict(payload.get("market_cue") or {})}
        phase = str(cue_payload.get("phase") or cue_payload.get("market_phase") or cue_payload.get("cue_phase") or "LUNCH").upper()
        cue_payload["require_fii_dii_upload"] = bool(self.settings.get("require_fii_dii_upload"))
        if phase == "PREMARKET":
            if self.latest_fii_dii_snapshot:
                cue_payload["fii_dii_status"] = dict(self.latest_fii_dii_snapshot)
            elif payload.get("fii_dii_status"):
                cue_payload["fii_dii_status"] = dict(payload.get("fii_dii_status") or {})
        elif self.latest_fii_dii_snapshot:
            cue_payload["fii_dii_status"] = self.latest_fii_dii_status(phase)
        return cue_payload

    def _available_capital(self, mode: str) -> float:
        if mode == MODE_PAPER:
            return float(self.paper_broker.available_balance or 0)
        if mode == MODE_REAL:
            client = self.kite_client_provider("LIVE")
            if client and hasattr(client, "available_margin"):
                try:
                    return float(client.available_margin() or 0)
                except Exception as exc:
                    self.logger.log("WARN", "Real margin lookup failed during Options Auto evaluation", error=str(exc))
                    return 0.0
        return float(self.settings.get("paper_starting_balance") or 0)

    def _sync_paper_lifecycle_to_session_locked(self) -> dict[str, Any]:
        self.session.active_trades = list(self.paper_lifecycle.active_trades)
        self.session.orders = list(self.paper_broker.orders[-100:])
        if self.paper_lifecycle.active_trades:
            state = "PAPER_TRADE_ACTIVE"
        elif self.paper_lifecycle.pending_entries:
            state = "PAPER_ENTRY_PENDING"
        elif self.paper_lifecycle.pending_approval:
            state = "PAPER_APPROVAL_PENDING"
        else:
            state = self.session.status
        return {
            "state": state,
            "active_trades": len(self.paper_lifecycle.active_trades),
            "pending_entries": len(self.paper_lifecycle.pending_entries),
            "pending_approval": bool(self.paper_lifecycle.pending_approval),
        }

    def _sync_real_option_positions_locked(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        client = self.kite_client_provider("LIVE")
        if not client:
            return {"synced": False, "reason": "Real Zerodha client is not connected.", "active_trades": 0}
        broker_orders = self._broker_orders(client, payload)
        positions = self._normalise_positions(self._broker_positions(client, payload))
        option_positions = [position for position in positions if self._is_open_option_position(position)]
        option_orders = [order for order in broker_orders if self._is_option_order(order)]
        active = [self._trade_from_real_position(position, option_orders) for position in option_positions]
        active = [trade for trade in active if trade]
        self.session.active_trades = active
        self.session.orders = option_orders[-100:]
        if active:
            if self.session.status not in {"REAL_SCANNING", "REAL_DRY_RUN_SCANNING", "REAL_ENTRY_ORDER_OPEN"}:
                self.session.status = "REAL_POSITION_ACTIVE"
        return {
            "synced": True,
            "active_trades": len(active),
            "option_orders": len(option_orders),
            "source": "zerodha_real_positions",
        }

    def _normalise_positions(self, positions: list[dict[str, Any]] | dict[str, Any] | None) -> list[dict[str, Any]]:
        if isinstance(positions, dict):
            rows: list[dict[str, Any]] = []
            for key in ("net", "day", "positions"):
                value = positions.get(key)
                if isinstance(value, list):
                    rows.extend([dict(item) for item in value if isinstance(item, dict)])
            return rows
        return [dict(item) for item in list(positions or []) if isinstance(item, dict)]

    def _is_open_option_position(self, position: dict[str, Any]) -> bool:
        quantity = _number(position.get("quantity"), position.get("net_quantity"))
        if quantity <= 0:
            return False
        exchange = str(position.get("exchange") or position.get("segment") or "").upper()
        symbol = str(position.get("tradingsymbol") or position.get("symbol") or "").upper()
        return exchange in {"NFO", "BFO", "NFO-OPT", "BFO-OPT"} and self._looks_like_option_symbol(symbol)

    def _is_option_order(self, order: dict[str, Any]) -> bool:
        exchange = str(order.get("exchange") or order.get("segment") or "").upper()
        symbol = str(order.get("tradingsymbol") or order.get("symbol") or "").upper()
        return exchange in {"NFO", "BFO", "NFO-OPT", "BFO-OPT"} and self._looks_like_option_symbol(symbol)

    def _trade_from_real_position(self, position: dict[str, Any], orders: list[dict[str, Any]]) -> dict[str, Any]:
        symbol = str(position.get("tradingsymbol") or position.get("symbol") or "").upper()
        symbol_orders = [order for order in orders if str(order.get("tradingsymbol") or order.get("symbol") or "").upper() == symbol]
        sell_orders = [order for order in symbol_orders if str(order.get("transaction_type") or "").upper() == "SELL" and str(order.get("status") or "OPEN").upper() not in {"COMPLETE", "CANCELLED", "REJECTED"}]
        target = next((order for order in sell_orders if str(order.get("order_type") or "").upper() == "LIMIT"), {})
        stoploss = next((order for order in sell_orders if str(order.get("order_type") or "").upper() in {"SL", "SL-M", "SL-LIMIT", "STOPLOSS", "STOPLOSS_LIMIT"}), {})
        quantity = abs(int(_number(position.get("quantity"), position.get("net_quantity"))))
        entry = _number(position.get("average_price"), position.get("buy_price") or 0)
        ltp = _number(position.get("last_price"), position.get("close_price") or entry)
        return {
            "trade_id": f"OA-REAL-{symbol}",
            "mode": MODE_REAL,
            "status": "POSITION_ACTIVE",
            "tradingsymbol": symbol,
            "exchange": position.get("exchange") or "NFO",
            "product": position.get("product") or "NRML",
            "side": "CE" if symbol.endswith("CE") else "PE" if symbol.endswith("PE") else "",
            "quantity": quantity,
            "entry_price": entry,
            "last_ltp": ltp,
            "unrealized_pnl": _number(position.get("pnl")),
            "target": _number(target.get("price")),
            "stoploss": _number(stoploss.get("trigger_price"), stoploss.get("price")),
            "target_order_id": target.get("order_id") or target.get("id") or "",
            "stoploss_order_id": stoploss.get("order_id") or stoploss.get("id") or "",
            "oco_active": bool(target and stoploss),
            "position_protected": bool(target and stoploss),
            "source": "zerodha_real_positions",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _looks_like_option_symbol(self, symbol: str) -> bool:
        symbol = str(symbol or "").upper()
        return symbol.endswith("CE") or symbol.endswith("PE")

    def _paper_lifecycle_active(self) -> bool:
        return bool(
            getattr(self.paper_lifecycle, "pending_approval", None)
            or getattr(self.paper_lifecycle, "pending_entries", None)
            or getattr(self.paper_lifecycle, "active_trades", None)
        )

    def _active_positions_protected(self) -> bool:
        if not self.session.active_trades:
            return True
        return all(bool(trade.get("position_protected") and trade.get("oco_active")) for trade in self.session.active_trades)

    def _results_writable(self) -> bool:
        return results_folder_writable(self.result_root())

    def _broker_orders(self, client: Any | None, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if payload.get("broker_orders") is not None:
            return list(payload.get("broker_orders") or [])
        if not client:
            return []
        self.real_api_manager.client = client
        result = self.real_api_manager.call("orders", lambda: self._call_client(client, ("orders", "orderbook")), priority="RECONCILIATION")
        return list(result.get("value") or []) if result.get("ok") else []

    def _broker_positions(self, client: Any | None, payload: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
        if payload.get("positions") is not None:
            return payload.get("positions") or []
        if not client:
            return []
        self.real_api_manager.client = client
        result = self.real_api_manager.call("positions", lambda: self._call_client(client, ("positions",)), priority="RECONCILIATION")
        return result.get("value") if result.get("ok") else []

    def _call_client(self, client: Any, names: tuple[str, ...]) -> Any:
        for name in names:
            if hasattr(client, name):
                return getattr(client, name)()
            kite = getattr(client, "kite", None)
            if kite and hasattr(kite, name):
                return getattr(kite, name)()
        raise AttributeError(f"Kite client does not expose any of: {', '.join(names)}")

    def _write_json_report(self, report_type: str, filename: str, payload: dict[str, Any]) -> str:
        folder = os.path.join(self.result_root(), report_type, self.mode_guard.session_id)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
        return path

    def _final_entry_validation(self, decision: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        plan = decision.get("ready_trade_plan") or self.ready_plan_cache.get(self.settings.get("underlying"))
        selected = dict(decision.get("selected_contract") or {})
        quote = self._latest_quote_for_selected(selected, payload)
        if not quote and selected:
            quote = {
                "ltp": selected.get("ltp"),
                "bid": selected.get("bid"),
                "ask": selected.get("ask"),
                "tick_size": selected.get("tick_size") or 0.05,
                "premium_return_1": (selected.get("premium_momentum") or {}).get("premium_return_1"),
                "option_atr14": selected.get("option_atr14") or selected.get("atr14"),
                "age_seconds": payload.get("quote_age_seconds", 0),
            }
        state = {
            "mode_guard_allowed": True,
            "governor_allowed": bool((decision.get("governor") or {}).get("allowed", decision.get("allowed"))),
            "rate_limiter_healthy": True,
            "data_quality_score": 100 if (decision.get("data_quality") or {}).get("allowed", True) else 0,
            "market_cue": decision.get("market_cue"),
            "regime": decision.get("regime"),
        }
        return self.low_latency_engine.validate_final_entry(plan or {}, quote, self.settings, state)

    def _latest_quote_for_selected(self, selected: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        quotes = dict(payload.get("quotes") or {})
        keys = [
            str(selected.get("instrument_token") or ""),
            str(selected.get("token") or ""),
            str(selected.get("tradingsymbol") or "").upper(),
        ]
        for key in keys:
            if key and key in quotes:
                return dict(quotes[key] or {})
        if payload.get("latest_quote"):
            return dict(payload.get("latest_quote") or {})
        return {}

    def _apply_adaptive_pending_entries(self, market: dict[str, Any]) -> list[dict[str, Any]]:
        updates = []
        for pending in list(self.paper_lifecycle.pending_entries):
            order = dict(pending.get("entry_order") or {})
            decision = dict(pending.get("decision") or {})
            selected = dict(decision.get("selected_contract") or {})
            latest_quote = {
                "ltp": market.get("ltp") or market.get("last_price") or selected.get("ltp"),
                "bid": market.get("bid") or selected.get("bid"),
                "ask": market.get("ask") or selected.get("ask"),
                "spread_pct": market.get("spread_pct") or selected.get("spread_pct"),
                "premium_return_1": market.get("premium_return_1") or (selected.get("premium_momentum") or {}).get("premium_return_1"),
                "tick_size": selected.get("tick_size") or 0.05,
                "age_seconds": market.get("age_seconds"),
                "now_epoch": market.get("now_epoch") or time.time(),
            }
            option_features = {
                "premium_return_1": latest_quote.get("premium_return_1"),
                "premium_return_3": market.get("premium_return_3") or (selected.get("premium_momentum") or {}).get("premium_return_3"),
                "spread_pct": latest_quote.get("spread_pct"),
                "upper_wick_pct": market.get("upper_wick_pct"),
                "option_atr14": selected.get("option_atr14") or selected.get("atr14"),
                "relative_volume": market.get("relative_volume") or selected.get("relative_volume"),
                "premium_expansion_confirmed": market.get("premium_expansion_confirmed", selected.get("premium_expansion_confirmed")),
            }
            adaptive = self.live_adaptive.evaluate_pending_entry(
                {**pending, **order, "planned_entry": (pending.get("trade_plan") or {}).get("entry_price"), "side": (pending.get("trade_plan") or {}).get("side")},
                selected,
                latest_quote,
                ((decision.get("decision_snapshot") or {}).get("index_features") or {}),
                option_features,
                market.get("market_cue") or decision.get("market_cue") or {},
                market.get("regime") or decision.get("regime") or {},
                self.settings,
            )
            update = {"entry_id": pending.get("entry_id"), "adaptive": adaptive}
            if adaptive.get("action") == "CANCEL_ENTRY" and self.settings.get("pending_entry_dynamic_cancel_enabled", True):
                update["cancel"] = self.paper_lifecycle.cancel_pending_entry(pending.get("entry_id"), adaptive.get("reason") or "Adaptive cancel.")
            elif adaptive.get("action") == "MODIFY_ENTRY" and adaptive.get("new_entry_limit"):
                update["modify"] = self.paper_lifecycle.modify_pending_entry(pending.get("entry_id"), adaptive["new_entry_limit"])
            updates.append(update)
        return updates

    def _real_adaptive_dry_run(self, result: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        plan = result.get("ready_trade_plan") or self.ready_plan_cache.get(self.settings.get("underlying"))
        selected = dict(result.get("selected_contract") or {})
        latest_quote = self._latest_quote_for_selected(selected, payload)
        final_validation = self.low_latency_engine.validate_final_entry(
            plan or {},
            latest_quote or {
                "ltp": selected.get("ltp"),
                "bid": selected.get("bid"),
                "ask": selected.get("ask"),
                "tick_size": selected.get("tick_size") or 0.05,
                "premium_return_1": (selected.get("premium_momentum") or {}).get("premium_return_1"),
                "option_atr14": selected.get("option_atr14") or selected.get("atr14"),
                "age_seconds": payload.get("quote_age_seconds", 0),
            },
            self.settings,
            {
                "mode_guard_allowed": False,
                "governor_allowed": bool((result.get("governor") or {}).get("allowed", False)),
                "rate_limiter_healthy": True,
                "data_quality_score": 100 if (result.get("data_quality") or {}).get("allowed", True) else 0,
                "market_cue": result.get("market_cue"),
                "regime": result.get("regime"),
            },
        )
        return {
            "dry_run": True,
            "recommended_action": "ENTER" if final_validation.get("allowed") else "HOLD",
            "order_request_preview": {
                "tradingsymbol": selected.get("tradingsymbol"),
                "transaction_type": "BUY",
                "quantity": (result.get("trade_plan") or {}).get("quantity"),
                "order_type": "LIMIT",
                "price": final_validation.get("entry_limit"),
            },
            "final_validation": final_validation,
            "safety_required": ["ModeGuard", "MasterGovernor", "RealExecutionController", "KiteApiManager", "OCO", "Reconciliation"],
            "orders_sent": 0,
        }

    def _apply_paper_exit_decisions(self, market: dict[str, Any]) -> list[dict[str, Any]]:
        updates = []
        forced_exit_actions = {"THETA_EXIT", "IV_CRUSH_EXIT", "END_OF_DAY_EXIT", "TIME_EXIT", "REVERSAL_EXIT"}
        for trade in list(self.paper_lifecycle.active_trades):
            adaptive = self.live_adaptive.evaluate_active_trade(
                trade,
                {
                    "ltp": market.get("ltp") or market.get("last_price"),
                    "bid": market.get("bid"),
                    "ask": market.get("ask"),
                    "spread_pct": market.get("spread_pct"),
                    "now_epoch": market.get("now_epoch") or time.time(),
                },
                market.get("index_features") or {},
                market.get("option_features") or market,
                market.get("market_cue") or {},
                market.get("regime") or {},
                self.settings,
                broker_orders=market.get("broker_orders") or [],
            )
            adaptive_update = {"trade_id": trade.get("trade_id"), "adaptive": adaptive}
            if adaptive.get("new_stoploss"):
                adaptive_update["stoploss_update"] = self.paper_lifecycle.update_stoploss(trade["trade_id"], adaptive["new_stoploss"])
            if adaptive.get("new_target"):
                adaptive_update["target_update"] = self.paper_lifecycle.update_target(trade["trade_id"], adaptive["new_target"])
            if adaptive.get("action") == "PARTIAL_EXIT" and int(adaptive.get("partial_quantity") or 0) > 0:
                adaptive_update["partial_exit"] = self.paper_lifecycle.partial_exit(
                    trade["trade_id"],
                    int(adaptive["partial_quantity"]),
                    float(market.get("ltp") or market.get("last_price") or trade.get("last_ltp") or trade.get("entry_price")),
                    "ADAPTIVE_PARTIAL_EXIT",
                )
            if adaptive.get("action") == "EXIT":
                adaptive_update["force_exit"] = self.paper_lifecycle.force_exit(
                    trade["trade_id"],
                    float(market.get("ltp") or market.get("last_price") or trade.get("last_ltp") or trade.get("entry_price")),
                    "ADAPTIVE_EXIT",
                )
            adaptive_visible = adaptive.get("action") != "HOLD" or any(
                key in adaptive_update for key in ("stoploss_update", "target_update", "partial_exit", "force_exit")
            )
            if adaptive.get("action") == "EXIT" and adaptive_update.get("force_exit"):
                if adaptive_visible:
                    updates.append(adaptive_update)
                continue
            decision = self.exit_manager.evaluate(trade, market, self.settings)
            update = {"trade_id": trade.get("trade_id"), "decision": decision}
            if decision.get("stoploss_change"):
                update["stoploss_update"] = self.paper_lifecycle.update_stoploss(trade["trade_id"], decision["new_stoploss"])
            if decision.get("action") == "PARTIAL_EXIT" and int(decision.get("partial_quantity") or 0) > 0:
                update["partial_exit"] = self.paper_lifecycle.partial_exit(
                    trade["trade_id"],
                    int(decision["partial_quantity"]),
                    float(market.get("ltp") or market.get("last_price") or trade.get("last_ltp") or trade.get("entry_price")),
                )
            if decision.get("action") in forced_exit_actions:
                update["force_exit"] = self.paper_lifecycle.force_exit(
                    trade["trade_id"],
                    float(market.get("ltp") or market.get("last_price") or trade.get("last_ltp") or trade.get("entry_price")),
                    decision["action"],
                )
            updates.append(update)
            if adaptive_visible:
                updates.append(adaptive_update)
        return updates


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


def _int_setting(value: Any, default: int) -> int:
    if value in ("", None):
        return int(default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _parse_trade_day(value: Any) -> date:
    text = str(value or "").strip()
    if not text:
        return date.today()
    return pd.to_datetime(text, errors="raise").date()


def _round_to_step(value: float, step: float) -> float:
    step = float(step or 1)
    return round(float(value) / step) * step


def _first_close(frame: pd.DataFrame) -> float:
    if frame is None or frame.empty or "close" not in frame.columns:
        return 0.0
    for value in frame["close"]:
        close = _number(value)
        if close > 0:
            return close
    return 0.0


def _expiry_text(value: Any) -> str:
    if value in ("", None):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _decorate_option_frame(frame: pd.DataFrame, contract: dict[str, Any], underlying: str, exchange: str) -> pd.DataFrame:
    result = frame.copy()
    option_type = str(contract.get("instrument_type") or contract.get("option_type") or "").upper()
    result["name"] = str(contract.get("name") or underlying).upper()
    result["underlying"] = underlying
    result["tradingsymbol"] = contract.get("tradingsymbol") or ""
    result["instrument_token"] = contract.get("instrument_token") or contract.get("token") or ""
    result["instrument_type"] = option_type
    result["option_type"] = option_type
    result["exchange"] = contract.get("exchange") or exchange
    result["expiry"] = _expiry_text(contract.get("expiry"))
    result["strike"] = _number(contract.get("strike"))
    result["lot_size"] = int(_number(contract.get("lot_size"), 50) or 50)
    result["tick_size"] = _number(contract.get("tick_size"), 0.05) or 0.05
    return result


def _contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "tradingsymbol": contract.get("tradingsymbol"),
        "instrument_token": contract.get("instrument_token") or contract.get("token"),
        "instrument_type": contract.get("instrument_type"),
        "strike": contract.get("strike"),
        "expiry": _expiry_text(contract.get("expiry")),
        "exchange": contract.get("exchange"),
    }
