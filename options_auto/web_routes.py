from __future__ import annotations

from options_auto.constants import MODE_REAL
from options_auto.terminal_service import OptionsAutoTerminalService


class OptionsAutoWebRoutes:
    def __init__(self, app_state, base_result_folder: str):
        self.app_state = app_state
        self.service = OptionsAutoTerminalService(
            base_result_folder,
            kite_client_provider=lambda mode: app_state.zerodha_clients_by_mode.get(
                "LIVE" if str(mode).upper() in {MODE_REAL, "LIVE"} else "PAPER"
            ),
        )

    def can_handle_get(self, path: str) -> bool:
        return path == "/options-auto" or path.startswith("/api/options-auto")

    def can_handle_post(self, path: str) -> bool:
        return path.startswith("/api/options-auto")

    def handle_get(self, handler, path: str, _parsed):
        if path.startswith("/api/options-auto"):
            self._sync_connection_state()
        if path == "/options-auto":
            return handler.send_static_file("options_auto.html")
        if path == "/api/options-auto/defaults":
            return handler.send_json(self.service.defaults())
        if path == "/api/options-auto/status":
            return handler.send_json(self._with_account_status(self.service.status()))
        if path == "/api/options-auto/ui-summary":
            return handler.send_json(self.ui_summary())
        if path == "/api/options-auto/lifecycle":
            return handler.send_json(self.service.status().get("real_order_lifecycle") or {})
        if path == "/api/options-auto/account-status":
            return handler.send_json(self.account_status())
        if path == "/api/options-auto/shadow/report":
            return handler.send_json(self.service.shadow_report())
        return handler.send_json({"error": "Options Auto route not found"}, status=404)

    def handle_post(self, handler, path: str, payload: dict):
        self._sync_connection_state()
        if path == "/api/options-auto/configure":
            return handler.send_json(self._with_account_status(self.service.configure(payload, self._profile_for_payload(payload))))
        if path == "/api/options-auto/evaluate":
            return handler.send_json(self._with_account_status(self.service.evaluate(self._with_profile(payload))))
        if path == "/api/options-auto/stop":
            return handler.send_json(self._with_account_status(self.service.stop_live_scan(payload)))
        if path == "/api/options-auto/kill-switch":
            return handler.send_json(self._with_account_status(self.service.kill_switch(payload)))
        if path == "/api/options-auto/shadow/start":
            return handler.send_json(self._with_account_status(self.service.start_shadow(self._with_profile(payload))))
        if path == "/api/options-auto/shadow/stop":
            return handler.send_json(self._with_account_status(self.service.stop_shadow(payload)))
        if path == "/api/options-auto/shadow/outcome":
            return handler.send_json(self.service.shadow_record_outcome(payload))
        if path == "/api/options-auto/paper/start":
            blockers = self._mode_blockers("PAPER")
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.start_paper(self._with_profile(payload))))
        if path == "/api/options-auto/paper/stop":
            return handler.send_json(self._with_account_status(self.service.stop_live_scan({**dict(payload or {}), "mode": "PAPER"})))
        if path == "/api/options-auto/paper/reset-account":
            return handler.send_json(self._with_account_status(self.service.reset_paper_account(payload)))
        if path == "/api/options-auto/paper/execute-plan":
            blockers = self._mode_blockers("PAPER")
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.execute_paper_plan(self._with_profile(payload))))
        if path == "/api/options-auto/paper/request-approval":
            blockers = self._mode_blockers("PAPER")
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.request_paper_approval(self._with_profile(payload))))
        if path == "/api/options-auto/paper/approve":
            return handler.send_json(self._with_account_status(self.service.approve_paper(payload)))
        if path == "/api/options-auto/paper/reject":
            return handler.send_json(self._with_account_status(self.service.reject_paper(payload)))
        if path == "/api/options-auto/paper/process-market":
            return handler.send_json(self._with_account_status(self.service.process_paper_market(payload)))
        if path == "/api/options-auto/real/dry-run":
            blockers = self._mode_blockers("LIVE", require_connection=True)
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.real_dry_run(self._with_profile(payload))))
        if path == "/api/options-auto/real/stop":
            return handler.send_json(self._with_account_status(self.service.stop_live_scan({**dict(payload or {}), "mode": "REAL"})))
        if path == "/api/options-auto/real/start-engine":
            blockers = self._mode_blockers("LIVE", require_connection=True)
            if blockers:
                raise PermissionError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.start_real_engine(self._with_profile(payload))))
        if path == "/api/options-auto/real/preflight":
            blockers = self._mode_blockers("LIVE")
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.real_preflight_check(self._with_profile(payload))))
        if path == "/api/options-auto/real/reconcile":
            blockers = self._mode_blockers("LIVE")
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.real_reconcile(payload)))
        if path == "/api/options-auto/real/lifecycle-poll":
            blockers = self._mode_blockers("LIVE")
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.real_lifecycle_poll(payload)))
        if path == "/api/options-auto/real/emergency-plan":
            blockers = self._mode_blockers("LIVE")
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.real_emergency_plan(self._with_profile(payload))))
        if path == "/api/options-auto/real/stop-new-entries":
            return handler.send_json(self._with_account_status(self.service.real_stop_new_entries(payload)))
        if path == "/api/options-auto/real/safe-mode":
            return handler.send_json(self._with_account_status(self.service.real_safe_mode(payload)))
        if path == "/api/options-auto/market-cue/fii-dii-upload":
            return handler.send_json(self._with_account_status(self.service.upload_fii_dii_csv(payload)))
        if path == "/api/options-auto/market-cue/premarket":
            return handler.send_json(self._with_account_status(self.service.premarket_market_cue(payload)))
        if path == "/api/options-auto/real/place-order":
            blockers = self._mode_blockers("LIVE", require_connection=True)
            if blockers:
                raise PermissionError(blockers[0])
            return handler.send_json(self._with_account_status(self.service.place_real_order(self._with_profile(payload))))
        if path == "/api/options-auto/backtest/run":
            return handler.send_json(self._with_account_status(self.service.backtest(payload)))
        if path == "/api/options-auto/readiness":
            return handler.send_json(self._with_account_status(self.service.readiness(payload)))
        if path == "/api/options-auto/health":
            return handler.send_json(self._with_account_status(self.service.health_status(payload)))
        if path == "/api/options-auto/exit/evaluate":
            return handler.send_json(self.service.exit_decision(payload))
        if path == "/api/options-auto/promotion/status":
            return handler.send_json(self.service.promotion_status(payload))
        if path == "/api/options-auto/drift/status":
            return handler.send_json(self.service.drift_status(payload))
        if path == "/api/options-auto/missed-trades/status":
            return handler.send_json(self.service.missed_trade_status(payload))
        if path == "/api/options-auto/replay/run":
            return handler.send_json(self.service.market_replay(payload))
        if path == "/api/options-auto/telegram/command":
            return handler.send_json(self.service.telegram_command(payload))
        return handler.send_json({"error": "Options Auto route not found"}, status=404)

    def account_status(self) -> dict:
        return self._account_status_snapshot()

    def _account_status_snapshot(self) -> dict:
        paper = self.app_state.connection_status("PAPER")
        live = self.app_state.connection_status("LIVE")
        return {
            "paper": paper,
            "real": live,
            "real_margin": self.app_state.account_margins.get("LIVE"),
            "paper_balance": self.app_state.account_margins.get("PAPER"),
            "connection_page_url": "/#zerodha",
            "mode_lock": self._mode_lock(paper, live),
        }

    def _sync_connection_state(self) -> dict:
        account = self._account_status_snapshot()
        if hasattr(self.service, "sync_main_app_connection_state"):
            self.service.sync_main_app_connection_state(account)
        return account

    def _with_account_status(self, payload: dict) -> dict:
        payload = _redact_sensitive(dict(payload))
        payload["account_status"] = self._sync_connection_state()
        return payload

    def ui_summary(self) -> dict:
        if hasattr(self.service, "ui_summary_snapshot"):
            status = self._with_account_status(self.service.ui_summary_snapshot())
        else:
            status = self._with_account_status(self.service.status())
        settings = status.get("settings") or {}
        account = status.get("account_status") or {}
        lifecycle = status.get("real_order_lifecycle") or {}
        session = status.get("session") or {}
        feed = status.get("options_live_feed") or {}
        live_scan = status.get("live_scan") or {}
        lock = ((status.get("contract_lock") or {}).get("lock") or {})
        mode = str(settings.get("mode") or "PAPER").upper()
        trade_mode = "REAL" if mode == "REAL" else "PAPER"
        real_connected = bool((account.get("real") or {}).get("connected"))
        paper_connected = bool((account.get("paper") or {}).get("connected"))
        selected_connected = real_connected if trade_mode == "REAL" else paper_connected
        live_scan_mode = str(live_scan.get("mode") or "").upper()
        session_started = bool(live_scan.get("running")) and live_scan_mode == trade_mode and selected_connected
        session_state = "RUNNING" if session_started else "DISCONNECTED" if not selected_connected else "SESSION_NOT_STARTED"
        data_health = feed.get("health") or {}
        feed_stale = bool(data_health.get("stale") or data_health.get("feed_stale"))
        protection_state = str(lifecycle.get("protected_state") or "FLAT").upper()
        lifecycle_state = str(lifecycle.get("state") or "IDLE").upper()
        session_active_trades = list(session.get("active_trades") or [])
        broker_open_positions = _broker_open_positions_from_lifecycle(lifecycle) if trade_mode == "REAL" else []
        broker_position_open = bool(broker_open_positions)
        position_open = broker_position_open or (session_started and bool(session_active_trades))
        active_instrument = _first_position_symbol(broker_open_positions) if broker_position_open else _first_position_symbol(session_active_trades) if session_started else ""
        blockers = []
        if mode == "REAL" and not real_connected:
            blockers.append("Real money locked")
        if mode != "REAL" and not paper_connected:
            blockers.append("Paper Zerodha data connection not connected")
        if selected_connected and not session_started:
            blockers.append("Session not started")
        if feed_stale:
            blockers.append("Options feed is stale")
        if not (lock.get("ce") and lock.get("pe")):
            blockers.append("Contracts are not locked")
        if broker_position_open and not session_started:
            blockers.append("Broker-open real position exists while scanner is stopped")
        if (session_started or broker_position_open) and ("FAILED" in protection_state or "UNPROTECTED" in lifecycle_state or "RECONCILIATION" in protection_state):
            blockers.append("Position protection requires manual attention")
        protection_failed = any("protection" in item.lower() for item in blockers)
        protection_active = (session_started or broker_position_open) and protection_state == "PROTECTIVE_EXIT_ACTIVE"
        oco_active = (session_started or broker_position_open) and lifecycle_state == "OCO_ACTIVE"
        return {
            "mode": mode,
            "real_money_state": "ARMED" if mode == "REAL" and real_connected else "LOCKED",
            "kite": "CONNECTED" if selected_connected else "DISCONNECTED",
            "data": "STALE" if feed_stale else "HEALTHY" if data_health else "WAITING",
            "engine": session_state,
            "session_started": session_started,
            "session_state": session_state,
            "selected_mode_connected": selected_connected,
            "position": "OPEN" if position_open else "FLAT",
            "protection": "FAILED" if protection_failed else "PROTECTED" if protection_active else "INACTIVE",
            "oco": "ACTIVE" if oco_active else "INACTIVE",
            "kill_switch": bool((status.get("real_safety") or {}).get("safe_mode")),
            "can_trade": not blockers,
            "blockers": blockers,
            "active_instrument": active_instrument,
            "broker_open_positions": broker_open_positions,
            "session_pnl": status.get("paper_account", {}).get("realized_pnl") or 0,
            "last_update": status.get("session", {}).get("updated_at") or "",
            "settings": settings,
            "session": session,
            "paper_account": status.get("paper_account") or {},
            "paper_lifecycle": status.get("paper_lifecycle") or {},
            "real_safety": status.get("real_safety") or {},
            "ready_trade_plan_cache": status.get("ready_trade_plan_cache") or {},
            "adaptive": status.get("adaptive") or {},
            "performance": status.get("performance") or {},
            "latency": status.get("latency") or {},
            "fii_dii": status.get("fii_dii") or {},
            "index_ticks": status.get("index_ticks") or [],
            "live_index_candles": status.get("live_index_candles") or {},
            "contract_lock": status.get("contract_lock") or {},
            "live_scan": live_scan,
            "scan_scheduler": status.get("scan_scheduler") or {},
            "stale_diagnostics": status.get("stale_diagnostics") or {},
            "options_live_feed": feed,
            "real_order_lifecycle": lifecycle,
            "runtime_persistence": status.get("runtime_persistence") or {},
            "reference_cache": status.get("reference_cache") or {},
            "feature_cache": status.get("feature_cache") or {},
            "api_budget": status.get("api_budget") or {},
            "account_status": account,
        }

    def _with_profile(self, payload: dict | None) -> dict:
        payload = dict(payload or {})
        payload["kite_profile"] = self._profile_for_payload(payload)
        return payload

    def _profile_for_payload(self, payload: dict | None) -> dict:
        settings = (payload or {}).get("settings") or {}
        mode = str((payload or {}).get("mode") or settings.get("mode") or "PAPER").upper()
        mode = "LIVE" if mode in {"REAL", "LIVE"} else "PAPER"
        return self.app_state.zerodha_auth_profiles.get(mode) or {}

    def _mode_lock(self, paper_connection: dict | None = None, live_connection: dict | None = None) -> dict:
        paper_connection = paper_connection if paper_connection is not None else self.app_state.connection_status("PAPER")
        live_connection = live_connection if live_connection is not None else self.app_state.connection_status("LIVE")
        if live_connection.get("connected"):
            return {"mode": "REAL", "reason": "Real Money Zerodha is connected."}
        if paper_connection.get("connected"):
            return {"mode": "PAPER", "reason": "Paper Data Zerodha is connected."}
        return {"mode": "", "reason": ""}

    def _mode_blockers(self, requested_mode: str, require_connection: bool = False) -> list[str]:
        requested_mode = "LIVE" if str(requested_mode).upper() in {"REAL", "LIVE"} else "PAPER"
        paper_connected = bool(self.app_state.connection_status("PAPER").get("connected"))
        live_connected = bool(self.app_state.connection_status("LIVE").get("connected"))
        if paper_connected and live_connected:
            return ["Paper and Real Money connections are both active; disconnect one before Options Auto trading."]
        if requested_mode == "LIVE":
            if paper_connected and not live_connected:
                return ["Real Trading locked because Paper mode is active."]
            if require_connection and not live_connected:
                return ["Connect Real Money Zerodha in the main app before Options Auto real trading."]
        if requested_mode == "PAPER" and live_connected:
            return ["Paper trading is simulation-only while Real Money mode is active; real order APIs stay locked to REAL mode."]
        blockers = []
        if hasattr(self.app_state, "blocking_connection_modes"):
            blockers = list(self.app_state.blocking_connection_modes(requested_mode) or [])
        if blockers:
            return [f"{self.app_state.auth_label(blockers[0])} is already connected."]
        if require_connection and not self.app_state.connection_status(requested_mode).get("connected"):
            action = "real trading" if requested_mode == "LIVE" else "paper trading"
            return [f"Connect {self.app_state.auth_label(requested_mode)} before Options Auto {action}."]
        return []


def _broker_open_positions_from_lifecycle(lifecycle: dict) -> list[dict]:
    positions = lifecycle.get("broker_open_positions")
    if not positions:
        reconciliation = lifecycle.get("reconciliation") or lifecycle.get("last_reconciliation") or {}
        positions = reconciliation.get("open_positions") or []
    return [dict(item) for item in positions if isinstance(item, dict)]


def _first_position_symbol(rows: list[dict]) -> str:
    if not rows:
        return ""
    first = rows[0] or {}
    return str(first.get("tradingsymbol") or first.get("symbol") or first.get("instrument") or "")


SENSITIVE_KEYS = {"access_token", "api_secret", "request_token", "enctoken", "authorization"}


def _redact_sensitive(value):
    if isinstance(value, dict):
        return {key: ("***" if str(key).lower() in SENSITIVE_KEYS else _redact_sensitive(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value
