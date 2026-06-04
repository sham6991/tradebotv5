from __future__ import annotations

import json
import os
import time
from typing import Any

import pandas as pd

from options_auto.backtest.backtest_engine import OptionsAutoBacktestEngine
from options_auto.backtest.market_replay import MarketReplayEngine
from options_auto.backtest.backtest_report_writer import BacktestReportWriter
from options_auto.config.options_auto_defaults import default_settings, normalize_settings
from options_auto.constants import MODE_BACKTEST, MODE_PAPER, MODE_REAL, MODE_SHADOW, REAL_EXECUTION_DISABLED_REASON, SIDE_WAIT
from options_auto.core.logger import OptionsAutoLogger
from options_auto.core.mode_guard import ModeGuard, normalize_mode
from options_auto.core.promotion import PromotionManager
from options_auto.core.session_state import OptionsAutoSessionState
from options_auto.core.watchdog import WatchdogService
from options_auto.execution.execution_safety import DataQualityEngine, RealOrderPreflight
from options_auto.execution.kite_api_manager import KiteApiManager, RateLimiter
from options_auto.execution.paper_broker import PaperBroker
from options_auto.execution.paper_lifecycle import PaperLifecycleEngine
from options_auto.execution.reconciliation import ReconciliationEngine
from options_auto.execution.real_execution_controller import RealExecutionController, results_folder_writable
from options_auto.intelligence.adaptive_risk_engine import PositionSizer, RiskEngine
from options_auto.intelligence.decision_explainer import explain_score
from options_auto.intelligence.entry_timing_engine import EntryTimingEngine
from options_auto.intelligence.exit_manager import ExitManager
from options_auto.intelligence.master_governor import MasterGovernor
from options_auto.intelligence.market_cue_engine import MarketCueEngine
from options_auto.intelligence.missed_trade_learning import MissedTradeLearning
from options_auto.intelligence.options_greeks_risk_engine import OptionsGreeksRiskEngine
from options_auto.intelligence.position_manager import PositionManager
from options_auto.intelligence.professional_discipline import ProfessionalDisciplineEngine
from options_auto.intelligence.regime_classifier import RegimeClassifier
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

    def defaults(self) -> dict[str, Any]:
        return {
            "settings": default_settings(),
            "feature": "Options Auto",
            "real_execution": {
                "enabled": False,
                "reason": REAL_EXECUTION_DISABLED_REASON,
            },
        }

    def status(self) -> dict[str, Any]:
        return {
            "settings": self.settings,
            "session": self.session.to_dict(),
            "paper_account": self.paper_broker.snapshot(),
            "paper_lifecycle": self.paper_lifecycle.snapshot(),
            "real_safety": self.real_controller.snapshot(),
            "logs": self.logger.tail(100),
            "result_root": self.result_root(),
            "shadow_report": self.shadow_engine.report(),
        }

    def result_root(self) -> str:
        return os.path.join(self.base_result_folder, "options_auto")

    def configure(self, payload: dict[str, Any] | None = None, kite_profile: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = normalize_settings(payload)
        mode = normalize_mode(settings.get("mode"))
        self.settings = settings
        self.mode_guard = ModeGuard(
            mode=mode,
            kite_profile=dict(kite_profile or {}),
            real_mode_confirmed=bool(settings.get("confirm_real_mode")),
            real_orders_enabled=bool(settings.get("real_orders_enabled")),
        )
        self.session = OptionsAutoSessionState(self.mode_guard)
        if mode == MODE_PAPER and not self._paper_lifecycle_active():
            self.paper_broker = PaperBroker(float(settings["paper_starting_balance"]))
            self.paper_lifecycle = PaperLifecycleEngine(self.paper_broker)
        elif mode == MODE_PAPER:
            self.logger.log("WARN", "Options Auto paper lifecycle preserved during configure", mode=mode)
        self.logger.log("INFO", "Options Auto configured", mode=mode, underlying=settings.get("underlying"))
        return self.status()

    def evaluate(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        settings_payload = {**self.settings, **dict(payload.get("settings") or {})}
        mode = normalize_mode(payload.get("mode") or settings_payload.get("mode") or self.settings.get("mode"))
        profile = payload.get("kite_profile") or {}
        self.configure({**settings_payload, "mode": mode}, kite_profile=profile)

        market_cue = self.market_cue_engine.evaluate(payload.get("market_cue") or payload, phase=payload.get("market_phase") or payload.get("cue_phase") or "")
        regime = self.regime_classifier.classify(payload.get("features") or {}, market_cue.to_dict())
        side = payload.get("side") or regime.recommended_side or market_cue.recommended_side or SIDE_WAIT
        instruments = list(payload.get("instruments") or [])
        quotes = dict(payload.get("quotes") or {})
        spot = float(payload.get("spot") or payload.get("index_ltp") or 0)
        settings = dict(self.settings)
        settings["available_capital"] = self._available_capital(mode)
        context = {
            "regime_alignment": regime.confidence if side == regime.recommended_side else 25,
            "market_cue_score": market_cue.confidence if side == market_cue.recommended_side else 35,
            "news_score": (payload.get("market_cue") or payload).get("news_score", 0) if isinstance(payload.get("market_cue") or payload, dict) else 0,
            "time_of_day_score": float(payload.get("time_of_day_score") or 65),
            "option_momentum_score": float(payload.get("option_momentum_score") or 55),
        }
        selection = self.strike_selector.select(instruments, quotes, spot, side, settings, context)
        blockers = list(selection.blockers)
        selected = selection.selected or {}
        if mode == MODE_REAL:
            preflight = self.real_preflight.validate(mode, self.kite_client_provider("LIVE"), self.settings, results_writable=True)
            blockers.extend(preflight.blockers)
        else:
            preflight = {"allowed": True, "state": "NOT_REAL_MODE", "blockers": [], "warnings": []}
        quote_decision = {"allowed": True, "blockers": []}
        if selected:
            quote = {
                "ltp": selected.get("ltp"),
                "spread_pct": selected.get("spread_pct"),
                "age_seconds": payload.get("quote_age_seconds", 0),
            }
            quote_decision = self.data_quality.validate_quote(quote, settings).to_dict()
            blockers.extend(quote_decision["blockers"])
        risk = self.risk_engine.evaluate(settings, payload.get("risk_state") or {}, now_epoch=time.time())
        sizing = self.position_sizer.quantity(
            selected.get("ltp") or selected.get("ask") or 0,
            selected.get("lot_size") or 0,
            settings["available_capital"],
            settings,
        ) if selected else {"quantity": 0, "lots": 0, "reason": "No selected contract."}
        if selected and sizing.get("quantity", 0) <= 0:
            blockers.append(sizing.get("reason") or "Calculated quantity is below one lot.")
        timing = self.entry_timing.evaluate(payload.get("signal_candle") or {}, {"ltp": selected.get("ltp"), "intended_entry": payload.get("intended_entry")}, settings) if selected else {"allowed": True, "blockers": [], "warnings": []}
        options_risk = self.options_risk.evaluate(selected, settings) if selected else {"allowed": True, "blockers": [], "warnings": []}
        blockers.extend(timing.get("blockers") or [])
        blockers.extend(options_risk.get("blockers") or [])
        discipline = self.discipline_engine.evaluate(
            {
                "aggressiveness": regime.aggressiveness,
                "chase_detected": bool(payload.get("chase_detected")),
                "manual_override_to_increase_risk": bool(payload.get("manual_override_to_increase_risk")),
            },
            payload.get("risk_state") or {},
        )
        execution = preflight if isinstance(preflight, dict) else preflight.to_dict()
        strategy = {
            "selected": bool(selected),
            "blockers": blockers,
        }
        governor = self.master_governor.evaluate(
            self.mode_guard.to_dict(),
            quote_decision,
            risk,
            discipline,
            execution,
            market={"blockers": [regime.no_trade_reason] if regime.no_trade_reason and not selected else []},
            strategy=strategy,
        )
        blockers = list(dict.fromkeys((blockers or []) + (governor.get("blockers") or [])))
        trade_plan = self._trade_plan(selected, sizing, regime.to_dict()) if selected else {}

        decision = {
            "mode": mode,
            "market_cue": market_cue.to_dict(),
            "regime": regime.to_dict(),
            "selection": selection.to_dict(),
            "data_quality": quote_decision,
            "risk": risk,
            "discipline": discipline,
            "entry_timing": timing,
            "options_risk": options_risk,
            "execution": execution,
            "governor": governor,
            "position_size": sizing,
            "trade_plan": trade_plan,
            "allowed": not blockers and bool(selected) and bool(governor.get("allowed")),
            "blockers": blockers,
            "real_execution_enabled": False,
            "real_execution_reason": REAL_EXECUTION_DISABLED_REASON,
        }
        if selected:
            decision["explanation"] = explain_score(selected.get("breakdown", {}), blockers)
        else:
            decision["explanation"] = explain_score({}, blockers)
        self.session.record_decision(decision)
        if mode == MODE_SHADOW:
            self.shadow_engine.record(decision)
        if blockers:
            self.session.record_rejection("; ".join(blockers), {"mode": mode})
        self.logger.log("INFO", "Options Auto evaluation completed", mode=mode, allowed=decision["allowed"], blockers=blockers)
        return {**decision, "session": self.session.to_dict(), "paper_account": self.paper_broker.snapshot()}

    def _trade_plan(self, selected: dict[str, Any], sizing: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
        entry = float(selected.get("ask") or selected.get("ltp") or 0)
        if entry <= 0:
            return {}
        stop_distance = max(0.5, entry * 0.18 * float(regime.get("stoploss_multiplier") or 1.0))
        target_distance = max(0.5, stop_distance * float(regime.get("target_multiplier") or 1.5))
        return {
            "tradingsymbol": selected.get("tradingsymbol"),
            "instrument_token": selected.get("instrument_token") or selected.get("token"),
            "side": selected.get("option_type"),
            "entry_price": round(entry, 2),
            "stoploss": round(max(0.05, entry - stop_distance), 2),
            "target": round(entry + target_distance, 2),
            "quantity": int(sizing.get("quantity") or 0),
            "lots": int(sizing.get("lots") or 0),
            "lot_size": int(selected.get("lot_size") or 0),
            "order_type": "LIMIT",
            "stoploss_order_type": "SL",
            "target_order_type": "LIMIT",
        }

    def start_shadow(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {**dict(payload or {}), "mode": MODE_SHADOW}
        result = self.evaluate(payload)
        self.session.status = "SHADOW_RUNNING"
        result["session"] = self.session.to_dict()
        result["message"] = "Shadow mode is running in decision-only mode. No paper or real order will be placed."
        return result

    def start_paper(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {**dict(payload or {}), "mode": MODE_PAPER}
        result = self.evaluate(payload)
        self.mode_guard.assert_paper_allowed()
        self.session.status = "PAPER_READY"
        result["session"] = self.session.to_dict()
        result["message"] = "Paper evaluation completed. Order simulation remains local to Options Auto paper broker."
        return result

    def execute_paper_plan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.start_paper(payload)
        if not result.get("allowed"):
            return {**result, "paper_order": None, "message": "Paper order not simulated because governor blocked the setup."}
        pending = self.paper_lifecycle.create_pending(result, int(self.settings.get("approval_timeout_seconds") or 30))
        approved = self.paper_lifecycle.approve(pending["approval_id"])
        self.session.orders.extend([approved["entry_order"], approved["target_order"], approved["stoploss_order"]])
        self.session.active_trades = list(self.paper_lifecycle.active_trades)
        self.session.status = "PAPER_TRADE_ACTIVE"
        self.logger.log("INFO", "Options Auto paper lifecycle trade active", trade_id=approved["trade"]["trade_id"])
        return {
            **result,
            "approval": pending,
            "paper_order": approved["entry_order"],
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
        if result.get("trade"):
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
        exit_updates = self._apply_paper_exit_decisions(market)
        result = self.paper_lifecycle.process_market(market)
        result["exit_updates"] = exit_updates
        self.session.active_trades = list(self.paper_lifecycle.active_trades)
        self.session.status = "PAPER_TRADE_ACTIVE" if self.session.active_trades else "PAPER_IDLE"
        self.logger.log("INFO", "Options Auto paper market processed", updates=len(result.get("updates") or []))
        return {**result, "paper_account": self.paper_broker.snapshot(), "session": self.session.to_dict()}

    def real_dry_run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {**dict(payload or {}), "mode": MODE_REAL}
        result = self.evaluate(payload)
        self.session.status = "REAL_DRY_RUN_ONLY"
        result["session"] = self.session.to_dict()
        result["message"] = REAL_EXECUTION_DISABLED_REASON
        return result

    def real_preflight_check(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        settings = {**self.settings, **dict(payload.get("settings") or {})}
        mode = normalize_mode(payload.get("mode") or settings.get("mode") or MODE_REAL)
        self.configure({**settings, "mode": mode}, kite_profile=payload.get("kite_profile") or {})
        client = self.kite_client_provider("LIVE") if mode == MODE_REAL else None
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

    def place_real_order(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.mode_guard.assert_real_order_allowed()
        raise PermissionError(REAL_EXECUTION_DISABLED_REASON)

    def backtest(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        self.configure({**dict(payload.get("settings") or {}), "mode": MODE_BACKTEST})
        candles = self._frame(payload.get("index_candles") or payload.get("candles") or [])
        options = [self._frame(frame) for frame in payload.get("option_candles") or []]
        result = self.backtest_engine.run(candles, options, self.settings)
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

    def _paper_lifecycle_active(self) -> bool:
        return bool(getattr(self.paper_lifecycle, "pending_approval", None) or getattr(self.paper_lifecycle, "active_trades", None))

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

    def _apply_paper_exit_decisions(self, market: dict[str, Any]) -> list[dict[str, Any]]:
        updates = []
        forced_exit_actions = {"THETA_EXIT", "IV_CRUSH_EXIT", "END_OF_DAY_EXIT", "TIME_EXIT", "REVERSAL_EXIT"}
        for trade in list(self.paper_lifecycle.active_trades):
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
        return updates
