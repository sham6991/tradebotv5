import argparse
import json
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

from backtest import run_backtest
from backtest_zerodha_data import fetch_zerodha_backtest_data
from engine import parse_option_metadata_from_text
from event_replay import build_session_replay, format_replay_report
from execution_v2 import Executor
from indicators import clean_and_add_indicators
from intraday.web_routes import IntradayWebRoutes
from live_backtest_optimizer import date_range_from_months, run_live_backtest_optimizer
from market_cue import MarketCueService
from options_auto.web_routes import OptionsAutoWebRoutes
from parity_replay import build_parity_report
from position_reconciler import PositionReconciler
from reporting import timestamped_file
from result_paths import live_result_category, result_category_folder, unique_paths
from runtime_errors import classify_runtime_error
import settings_service
from strategy import ensure_option_formula_columns
from trade_settings_optimizer import OptimizerStopped, run_risk_settings_optimizer
from trading_tab_optimizer import run_trading_tab_optimizer
from ui_replay import REPLAY_FILTERS, latest_replay_database, replay_table_row
from settings_service import DEFAULT_SETTINGS, SETTING_LABELS, SETTINGS_PROFILE_PATH
from zerodha_auth import DEFAULT_REDIRECT_URL, ZerodhaAuthStore
from zerodha_client import ZerodhaClient


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_FOLDER = os.path.join(BASE_DIR, "results")
STATIC_DIR = os.path.join(BASE_DIR, "web_static")
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")
WEB_HOST = "127.0.0.1"
WEB_PORT = 8007
WEB_REDIRECT_PATH = "/zerodha/callback"
ZERODHA_MODES = {"PAPER", "LIVE"}
ZERODHA_MODE_ALIASES = {"BACKTEST": "PAPER", "VIRTUAL": "PAPER"}
LIVE_BLOCKING_MODES = {
    "PAPER": {"LIVE"},
    "LIVE": {"PAPER"},
}
LIVE_START_SAFETY_MAX_AGE_SECONDS = 300
LIVE_START_NETWORK_PASS_STATUSES = {"CONNECTED"}


def result_folder(category, create=True):
    return result_category_folder(RESULT_FOLDER, category, create=create)


def json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def normalise_interval(value):
    return settings_service.normalise_interval(value)


def normalise_order_product(value):
    return settings_service.normalise_order_product(value)


def normalise_trend_set(value):
    return settings_service.normalise_trend_set(value)


def parse_instrument_token(value, label):
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        raise ValueError(f"{label} is required. Use Fetch or enter the numeric Zerodha instrument token.")
    try:
        token = int(text)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a numeric Zerodha instrument token, got {value!r}.")
    if token <= 0:
        raise ValueError(f"{label} must be a positive Zerodha instrument token.")
    return token


def setting_value(values, key):
    return settings_service.setting_value(values, key)


RUNTIME_ACCOUNT_KEYS = settings_service.RUNTIME_ACCOUNT_KEYS


def runtime_dir():
    return settings_service.runtime_dir(SETTINGS_PROFILE_PATH)


def real_account_snapshot_path():
    return settings_service.real_account_snapshot_path(SETTINGS_PROFILE_PATH)


def load_real_account_snapshot():
    return settings_service.load_real_account_snapshot(SETTINGS_PROFILE_PATH)


def save_real_account_snapshot(snapshot):
    return settings_service.save_real_account_snapshot(snapshot, SETTINGS_PROFILE_PATH, json_default=json_default)


def sanitize_settings_profile(values, profile=""):
    return settings_service.sanitize_settings_profile(values, profile)


def normalized_settings_profile(values, profile=""):
    return settings_service.normalized_settings_profile(values, profile)


def persisted_settings_profile(profile, values):
    return settings_service.persisted_settings_profile(profile, values)


def persisted_settings_profiles(profiles):
    return settings_service.persisted_settings_profiles(profiles)


def settings_from_values(values):
    return settings_service.settings_from_values(values)


def parse_runtime_setting_value(key, value):
    return settings_service.parse_runtime_setting_value(key, value)


def load_settings_profiles():
    return settings_service.load_settings_profiles(SETTINGS_PROFILE_PATH)


def save_settings_profile(profile, values):
    return settings_service.save_settings_profile(profile, values, SETTINGS_PROFILE_PATH)


def apply_backtest_settings_to_live(values=None):
    return settings_service.apply_backtest_settings_to_live(values, SETTINGS_PROFILE_PATH)


def load_csv_dataframe(path, instrument="", option_data=False, strike="", expiry="", option_type=""):
    extension = os.path.splitext(str(path or ""))[1].lower()
    if extension in {".xlsx", ".xls", ".xlsm"}:
        df = clean_and_add_indicators(pd.read_excel(path))
    else:
        df = clean_and_add_indicators(pd.read_csv(path))
    parsed = parse_option_metadata_from_text(os.path.basename(path))
    if option_data:
        df = ensure_option_formula_columns(df)
        df.attrs["data_kind"] = "option"
        strike = strike or parsed.get("strike", "")
        expiry = expiry or parsed.get("expiry", "")
        option_type = option_type or parsed.get("option_type", "")
    if instrument:
        df.attrs["instrument"] = instrument
        df.attrs["tradingsymbol"] = instrument
    if strike:
        df.attrs["strike"] = strike
    if expiry:
        df.attrs["expiry"] = expiry
    if option_type:
        df.attrs["option_type"] = option_type
    return df


class WebTradeBotApp:
    def __init__(self, host=WEB_HOST, port=WEB_PORT):
        self.host = host
        self.port = int(port)
        self.lock = threading.RLock()
        self.executor = Executor()
        self.auth_store = ZerodhaAuthStore()
        self.zerodha_clients_by_mode = {mode: None for mode in ZERODHA_MODES}
        self.zerodha_auth_profiles = {mode: None for mode in ZERODHA_MODES}
        self.zerodha_auth_login_times = {mode: "" for mode in ZERODHA_MODES}
        self.pending_auth = {}
        self.account_margins = {
            "PAPER": self.paper_balance_snapshot(),
            "LIVE": {"available": None, "updated_at": "", "error": ""},
        }
        self.status = "Ready"
        self.current_mode = "PAPER"
        self.current_token_map = {}
        self.tick_buffer = {"NIFTY": [], "CE": [], "PE": []}
        self.tick_rate_windows = {"NIFTY": deque(), "CE": deque(), "PE": deque()}
        self.tick_rates = {"NIFTY": 0, "CE": 0, "PE": 0}
        self.live_log_active_rows = []
        self.live_order_history_rows = []
        self.live_trade_snapshot = {}
        self.live_health_snapshot = {}
        self.session_summary = self.empty_session_summary("PAPER")
        self.network_health = {
            "PAPER": self.empty_network_health("PAPER"),
            "LIVE": self.empty_network_health("LIVE"),
        }
        self.recovery_status = {
            "PAPER": self.empty_recovery_status("PAPER"),
            "LIVE": self.empty_recovery_status("LIVE"),
        }
        self.alerts = []
        self.trades = []
        self.last_backtest = None
        self.last_replay = None
        self.optimizer_progress = {
            "risk_settings": self.empty_optimizer_progress("risk_settings", "Risk Settings Optimizer"),
            "trading_tab": self.empty_optimizer_progress("trading_tab", "Trading Tab Optimizer"),
        }
        self.optimizer_stop_events = {
            "risk_settings": threading.Event(),
            "trading_tab": threading.Event(),
        }
        self.market_cue = MarketCueService(kite_client_provider=self.virtual_zerodha_client)
        self.intraday_routes = IntradayWebRoutes(self, RESULT_FOLDER)
        self.options_auto_routes = OptionsAutoWebRoutes(self, RESULT_FOLDER)

    @property
    def base_url(self):
        return f"http://{self.host}:{self.port}"

    @property
    def local_url(self):
        return f"http://{self.host}:{self.port}"

    @property
    def redirect_url(self):
        return f"{self.base_url}{WEB_REDIRECT_PATH}"

    def set_status(self, text):
        with self.lock:
            self.status = text

    def empty_optimizer_progress(self, kind, label):
        return {
            "kind": kind,
            "label": label,
            "active": False,
            "stage": "",
            "message": "Idle",
            "percent": 0,
            "completed": 0,
            "total": 0,
            "started_at": "",
            "updated_at": "",
            "completed_at": "",
            "error": "",
            "stop_requested": False,
        }

    def start_optimizer_progress(self, kind, label, message):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if kind in self.optimizer_stop_events:
            self.optimizer_stop_events[kind].clear()
        with self.lock:
            self.optimizer_progress[kind] = {
                **self.empty_optimizer_progress(kind, label),
                "active": True,
                "stage": "Starting",
                "message": message,
                "started_at": now,
                "updated_at": now,
                "stop_requested": False,
            }
            self.status = message

    def update_optimizer_progress(self, kind, update):
        update = dict(update or {})
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            current = dict(self.optimizer_progress.get(kind) or self.empty_optimizer_progress(kind, kind))
            current.update(update)
            current["active"] = True
            current["updated_at"] = update.get("updated_at") or now
            current["error"] = ""
            current["stop_requested"] = bool(self.optimizer_stop_events.get(kind) and self.optimizer_stop_events[kind].is_set())
            self.optimizer_progress[kind] = current
            label = current.get("label") or kind
            percent = float(current.get("percent") or 0)
            self.status = f"{label}: {percent:.1f}% - {current.get('message', '')}"

    def finish_optimizer_progress(self, kind, message):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            current = dict(self.optimizer_progress.get(kind) or self.empty_optimizer_progress(kind, kind))
            current.update({
                "active": False,
                "stage": "Complete",
                "message": message,
                "percent": 100,
                "completed_at": now,
                "updated_at": now,
                "error": "",
                "stop_requested": False,
            })
            self.optimizer_progress[kind] = current
            if kind in self.optimizer_stop_events:
                self.optimizer_stop_events[kind].clear()
            self.status = message

    def stop_optimizer_progress(self, kind, message):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            current = dict(self.optimizer_progress.get(kind) or self.empty_optimizer_progress(kind, kind))
            current.update({
                "active": False,
                "stage": "Stopped",
                "message": message,
                "updated_at": now,
                "completed_at": now,
                "error": "",
                "stop_requested": True,
            })
            self.optimizer_progress[kind] = current
            if kind in self.optimizer_stop_events:
                self.optimizer_stop_events[kind].clear()
            self.status = message

    def fail_optimizer_progress(self, kind, message):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            current = dict(self.optimizer_progress.get(kind) or self.empty_optimizer_progress(kind, kind))
            current.update({
                "active": False,
                "stage": "Failed",
                "message": message,
                "updated_at": now,
                "completed_at": now,
                "error": message,
                "stop_requested": False,
            })
            self.optimizer_progress[kind] = current
            if kind in self.optimizer_stop_events:
                self.optimizer_stop_events[kind].clear()
            self.status = message

    def optimizer_progress_callback(self, kind):
        return lambda update: self.update_optimizer_progress(kind, update)

    def optimizer_stop_requested_callback(self, kind):
        return lambda: bool(self.optimizer_stop_events.get(kind) and self.optimizer_stop_events[kind].is_set())

    def normalise_optimizer_kind(self, value):
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "risk": "risk_settings",
            "risk_setting": "risk_settings",
            "risk_settings": "risk_settings",
            "trade": "trading_tab",
            "trading": "trading_tab",
            "trading_tab": "trading_tab",
            "trade_tab": "trading_tab",
        }
        if text not in aliases:
            raise ValueError("Optimizer kind must be risk_settings or trading_tab.")
        return aliases[text]

    def request_optimizer_stop(self, payload):
        kind = self.normalise_optimizer_kind((payload or {}).get("kind") or (payload or {}).get("optimizer"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        event = self.optimizer_stop_events[kind]
        event.set()
        with self.lock:
            current = dict(self.optimizer_progress.get(kind) or self.empty_optimizer_progress(kind, kind))
            if not current.get("active"):
                current.update({
                    "stage": "Stopped",
                    "message": f"{current.get('label', kind)} is not running.",
                    "updated_at": now,
                    "stop_requested": False,
                })
                event.clear()
            else:
                current.update({
                    "stage": "Stopping",
                    "message": f"Stop requested for {current.get('label', kind)}...",
                    "updated_at": now,
                    "stop_requested": True,
                })
            self.optimizer_progress[kind] = current
            self.status = current["message"]
            return {"kind": kind, "progress": current, "message": current["message"]}

    def auth_label(self, mode):
        mode = ZERODHA_MODE_ALIASES.get(str(mode or "").upper(), str(mode or "").upper())
        if mode == "LIVE":
            return "Real Money Zerodha"
        return "Virtual/Paper Data"

    def virtual_zerodha_client(self):
        return self.zerodha_clients_by_mode.get("PAPER")

    def sync_zerodha_client_for_mode(self, mode):
        mode = self.validate_zerodha_mode(mode)
        self.current_mode = mode
        self.executor.zerodha = self.zerodha_clients_by_mode.get(mode)
        return self.executor.zerodha

    def validate_zerodha_mode(self, mode):
        mode = str(mode or "").upper()
        mode = ZERODHA_MODE_ALIASES.get(mode, mode)
        if mode not in ZERODHA_MODES:
            raise ValueError("Mode must be PAPER/VIRTUAL or LIVE.")
        return mode

    def blocking_connection_modes(self, mode):
        mode = self.validate_zerodha_mode(mode)
        return [
            other
            for other in LIVE_BLOCKING_MODES.get(mode, set())
            if self.zerodha_clients_by_mode.get(other)
        ]

    def connection_blocked(self, mode):
        return bool(self.blocking_connection_modes(mode))

    def connection_status(self, mode):
        mode = self.validate_zerodha_mode(mode)
        profile = self.zerodha_auth_profiles.get(mode) or {}
        client = self.zerodha_clients_by_mode.get(mode)
        user_name = profile.get("user_name") or profile.get("user_shortname") or ""
        user_id = profile.get("user_id") or profile.get("client_id") or ""
        return {
            "connected": bool(client),
            "label": self.auth_label(mode),
            "user_name": user_name,
            "user_id": user_id,
            "login_at": self.zerodha_auth_login_times.get(mode, ""),
            "blocked": self.connection_blocked(mode),
        }

    def empty_network_health(self, mode):
        return {
            "mode": str(mode or "").upper(),
            "status": "Not Run",
            "quality": "Unknown",
            "checked_at": "",
            "total_ms": "",
            "steps": [],
            "message": "Run health check before starting feed/trading.",
        }

    def empty_recovery_status(self, mode):
        return {
            "mode": str(mode or "").upper(),
            "status": "Not Checked",
            "severity": "Unknown",
            "checked_at": "",
            "summary": "Run recovery check before restarting after a lost session.",
            "checks": [],
            "files": [],
            "findings": [],
            "recommendation": "Run recovery check.",
        }

    def empty_session_summary(self, mode):
        return {
            "mode": str(mode or "PAPER").upper(),
            "session_status": "Idle",
            "session_id": "",
            "account_balance": self.paper_balance_value() if str(mode or "").upper() == "PAPER" else None,
            "session_start_balance": None,
            "session_pnl": 0,
            "session_trade_count": 0,
            "updated_at": "",
        }

    def paper_balance_value(self):
        try:
            return float(load_settings_profiles()["paper"].get("balance", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def paper_balance_snapshot(self):
        return {
            "available": self.paper_balance_value(),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": "",
        }

    def save_paper_balance(self, balance):
        profiles = load_settings_profiles()
        paper = normalized_settings_profile(profiles.get("paper", {}))
        paper["balance"] = f"{float(balance):.2f}"
        save_settings_profile("paper", paper)
        self.account_margins["PAPER"] = self.paper_balance_snapshot()
        return paper

    def update_session_summary(self, health=None, mode=None):
        health = health or {}
        current_mode = str(mode or health.get("mode") or self.current_mode or "PAPER").upper()
        self.session_summary = {
            "mode": current_mode,
            "session_status": health.get("session_status", self.session_summary.get("session_status", "Idle")),
            "session_id": health.get("session_id", self.session_summary.get("session_id", "")),
            "account_balance": health.get("account_balance", self.session_summary.get("account_balance")),
            "session_start_balance": health.get("session_start_balance", self.session_summary.get("session_start_balance")),
            "session_pnl": health.get("session_pnl", self.session_summary.get("session_pnl", 0)),
            "session_trade_count": health.get("session_trade_count", self.session_summary.get("session_trade_count", 0)),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def status_payload(self):
        with self.lock:
            metrics = self.executor.feed_metrics()
            return {
                "status": self.status,
                "urls": {
                    "app": self.base_url,
                    "local_app": self.local_url,
                    "redirect": self.redirect_url,
                    "previous_desktop_redirect": DEFAULT_REDIRECT_URL,
                },
                "current_mode": self.current_mode,
                "connections": {
                    "PAPER": self.connection_status("PAPER"),
                    "LIVE": self.connection_status("LIVE"),
                },
                "account_margins": self.account_margins,
                "feed": metrics,
                "ticks": self.tick_buffer,
                "tick_rates": self.tick_rates,
                "active_orders": self.live_log_active_rows[-100:],
                "order_history": self.live_order_history_rows[-200:],
                "live_trade": self.live_trade_snapshot,
                "health": self.live_health_snapshot,
                "session_summary": self.session_summary,
                "network_health": self.network_health,
                "recovery_status": self.recovery_status,
                "optimizer_progress": self.optimizer_progress,
                "alerts": self.alerts[-50:],
                "trades": self.trades[-100:],
                "last_backtest": self.last_backtest,
                "last_replay": self.last_replay,
            }

    def run_network_health_check(self, mode):
        mode = str(mode or self.current_mode or "PAPER").upper()
        if mode not in {"PAPER", "LIVE"}:
            raise ValueError("Mode must be PAPER or LIVE.")
        started = time.perf_counter()
        client = self.zerodha_clients_by_mode.get(mode)
        steps = [self.measure_network_step("Zerodha API Reachability", self.check_zerodha_api_reachable)]
        if client:
            steps.extend([
                self.measure_network_step("Zerodha Profile", client.profile),
                self.measure_network_step("Zerodha Margin", client.available_margin),
            ])
            if mode == "LIVE":
                steps.append(self.measure_network_step("Zerodha Order Book", client.orders))
        else:
            steps.append({
                "name": f"{self.auth_label(mode)} Login",
                "status": "Skipped",
                "duration_ms": "",
                "error": "Connect this mode first for authenticated broker checks.",
            })

        total_ms = round((time.perf_counter() - started) * 1000, 2)
        failed_steps = [step for step in steps if step["status"] == "Failed"]
        measured = [step["duration_ms"] for step in steps if isinstance(step.get("duration_ms"), (int, float))]
        worst_ms = max(measured) if measured else 0
        if failed_steps:
            quality = "Bad"
            status = "Failed"
            message = failed_steps[0]["error"]
        elif not client:
            quality = "Partial"
            status = "Reachable"
            message = "Zerodha API is reachable. Connect this mode to test authenticated broker latency."
        elif worst_ms > 2000:
            quality = "Slow"
            status = "Connected"
            message = "Network is connected, but at least one broker call is slow."
        elif worst_ms > 800:
            quality = "Fair"
            status = "Connected"
            message = "Network is usable, but broker latency is a little high."
        else:
            quality = "Good"
            status = "Connected"
            message = "Network and read-only broker checks look healthy."

        result = {
            "mode": mode,
            "status": status,
            "quality": quality,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_ms": total_ms,
            "steps": steps,
            "message": message,
        }
        with self.lock:
            self.network_health[mode] = result
        self.set_status(f"{mode} network health: {quality}")
        return result

    def measure_network_step(self, name, callback):
        started = time.perf_counter()
        try:
            callback()
            return {
                "name": name,
                "status": "OK",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "error": "",
            }
        except Exception as exc:
            classification = classify_runtime_error(exc, context="network_health")
            return {
                "name": name,
                "status": "Failed",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "error": str(exc),
                "error_class": classification["class"],
                "error_category": classification["category"],
            }

    def check_zerodha_api_reachable(self):
        request = urllib.request.Request("https://api.kite.trade", method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=3):
                return True
        except urllib.error.HTTPError:
            return True

    def run_recovery_check(self, mode):
        mode = str(mode or self.current_mode or "PAPER").upper()
        if mode not in {"PAPER", "LIVE"}:
            raise ValueError("Mode must be PAPER or LIVE.")

        session = self.session_for_mode(mode)
        client = self.zerodha_clients_by_mode.get(mode)
        state = self.recovery_state_snapshot(mode, session)
        files = self.recovery_file_rows(mode)
        checks = []
        findings = []

        checks.append(self.recovery_check_row(
            "Trading Session",
            "Active" if session else "Not Running",
            f"{state.get('session_id', '')} {state.get('mode', mode)}".strip() if session else "No active in-memory session.",
        ))
        checks.append(self.recovery_check_row(
            "Open Position",
            "Present" if state["open_position"] else "Clear",
            self.position_summary(state["open_position"]) if state["open_position"] else "No local open position.",
        ))
        checks.append(self.recovery_check_row(
            "Pending Entry",
            "Present" if state["pending_entry"] else "Clear",
            self.pending_summary(state["pending_entry"]) if state["pending_entry"] else "No local pending entry.",
        ))
        checks.append(self.recovery_check_row(
            "Kill Switch",
            "Active" if state["kill_switch_active"] else "Clear",
            state["kill_switch_reason"] or "Kill switch is not active.",
        ))

        saved_position = self.read_recovery_json(mode, "open_position")
        saved_pending = self.read_recovery_json(mode, "pending_entry")
        saved_kill_switch = self.latest_kill_switch_state(mode)
        if saved_position and not state["open_position"]:
            state["open_position"] = saved_position
            findings.append(self.recovery_finding("WARN", "SAVED_OPEN_POSITION", "Saved open position exists but no active session is loaded."))
        if saved_pending and not state["pending_entry"]:
            state["pending_entry"] = saved_pending
            findings.append(self.recovery_finding("WARN", "SAVED_PENDING_ENTRY", "Saved pending entry exists but no active session is loaded."))
        if saved_kill_switch.get("active") and not state["kill_switch_active"]:
            state["kill_switch_active"] = True
            state["kill_switch_reason"] = saved_kill_switch.get("reason") or "Restored kill switch state"
            findings.append(self.recovery_finding(
                "ERROR",
                "RESTORED_KILL_SWITCH_ACTIVE",
                "Saved kill switch state is active. Resolve it before real-money trading.",
                saved_kill_switch,
            ))

        if mode == "LIVE" and client:
            findings.extend(self.reconcile_recovery_state(session, state))
            checks.extend(self.broker_order_checks(state, client))
        elif mode == "LIVE":
            checks.append(self.recovery_check_row(
                "Broker Reconciliation",
                "Skipped",
                "Connect LIVE Zerodha to verify saved order ids against broker state.",
            ))

        checks.append(self.recovery_check_row(
            "Recovery Files",
            "Found" if any(item["exists"] == "Yes" for item in files) else "Clear",
            "Saved state file exists." if any(item["exists"] == "Yes" for item in files) else "No saved recovery files found.",
        ))

        severity, status, recommendation = self.recovery_decision(state, checks, findings)
        result = {
            "mode": mode,
            "status": status,
            "severity": severity,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": self.recovery_summary(state, findings),
            "checks": checks,
            "files": files,
            "findings": findings,
            "recommendation": recommendation,
        }
        with self.lock:
            self.recovery_status[mode] = result
        self.set_status(f"{mode} recovery check: {status}")
        return result

    def session_for_mode(self, mode):
        if mode == "LIVE":
            return self.executor.live_real_session
        return self.executor.live_paper_session

    def recovery_state_snapshot(self, mode, session):
        if not session:
            return {
                "mode": mode,
                "session_id": "",
                "open_position": None,
                "pending_entry": None,
                "kill_switch_active": False,
                "kill_switch_reason": "",
                "startup_findings": [],
            }
        with session.state_lock:
            return {
                "mode": session.mode,
                "session_id": session.session_id,
                "open_position": self.safe_state_copy(session.open_position),
                "pending_entry": self.safe_state_copy(session.pending_entry),
                "kill_switch_active": bool(session.risk_guard.kill_switch_active),
                "kill_switch_reason": session.risk_guard.kill_switch_reason,
                "startup_findings": list(session.startup_reconciliation_findings),
            }

    def safe_state_copy(self, data):
        if not data:
            return None
        copied = dict(data)
        signal = dict(copied.get("signal", {}) or {})
        signal.pop("option", None)
        copied["signal"] = signal
        copied.pop("timer", None)
        return copied

    def recovery_file_rows(self, mode):
        prefix = mode.lower()
        paths = [
            ("Open Position", self.preferred_recovery_path(mode, "open_position")),
            ("Pending Entry", self.preferred_recovery_path(mode, "pending_entry")),
        ]
        kill_switch_files = sorted(
            (
                path for path in self.find_result_files(f"{mode}_*_kill_switch.json")
            ),
            key=lambda path: os.path.getmtime(path),
            reverse=True,
        )
        if kill_switch_files:
            paths.append(("Latest Kill Switch", kill_switch_files[0]))
        rows = []
        for label, path in paths:
            exists = os.path.exists(path)
            rows.append({
                "name": label,
                "exists": "Yes" if exists else "No",
                "updated_at": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S") if exists else "",
                "path": path,
            })
        return rows

    def find_result_files(self, pattern):
        try:
            import glob
            return glob.glob(os.path.join(RESULT_FOLDER, "**", pattern), recursive=True)
        except OSError:
            return []

    def latest_kill_switch_state(self, mode):
        files = sorted(
            self.find_result_files(f"{str(mode or '').upper()}_*_kill_switch.json"),
            key=lambda path: os.path.getmtime(path),
            reverse=True,
        )
        if not files:
            return {}
        try:
            with open(files[0], "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(data, dict):
            return data
        return {}

    def read_recovery_json(self, mode, kind):
        for path in self.recovery_path_candidates(mode, kind):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def preferred_recovery_path(self, mode, kind):
        candidates = self.recovery_path_candidates(mode, kind)
        for path in candidates:
            if os.path.exists(path):
                return path
        return candidates[0]

    def recovery_path_candidates(self, mode, kind):
        prefix = str(mode or "").lower()
        file_name = f"{prefix}_{kind}.json"
        return unique_paths([
            os.path.join(result_folder(live_result_category(mode), create=False), file_name),
            os.path.join(RESULT_FOLDER, file_name),
        ])

    def reconcile_recovery_state(self, session, state):
        if session:
            reconciler = PositionReconciler(session.orders)
            return [
                self.recovery_finding(item.get("level", "WARN"), item.get("code", ""), item.get("message", ""), item)
                for item in reconciler.reconcile(state["open_position"], state["pending_entry"])
            ]
        return self.simple_broker_reconcile(state)

    def simple_broker_reconcile(self, state):
        findings = []
        for label, order_id in self.recovery_order_ids(state):
            if not order_id:
                continue
            try:
                status = self.zerodha_clients_by_mode["LIVE"].order_status(order_id)
            except Exception as exc:
                classification = classify_runtime_error(exc, context="order_status")
                findings.append(self.recovery_finding(
                    "WARN",
                    "ORDER_STATUS_CHECK_FAILED",
                    f"{label} status check failed: {exc}",
                    {
                        "error": str(exc),
                        "error_class": classification["class"],
                        "error_category": classification["category"],
                    },
                ))
                continue
            if status in {"CANCELLED", "REJECTED", "UNKNOWN"}:
                findings.append(self.recovery_finding("WARN", "ORDER_STATUS_REVIEW", f"{label} status is {status}."))
            if status in {"COMPLETE", "FILLED"} and label != "Entry Order":
                findings.append(self.recovery_finding("ERROR", "EXIT_ORDER_ALREADY_FILLED", f"{label} appears filled at broker."))
        return findings

    def broker_order_checks(self, state, client):
        rows = []
        order_ids = self.recovery_order_ids(state)
        if not order_ids:
            return [self.recovery_check_row("Broker Order IDs", "Clear", "No local order ids to verify.")]
        for label, order_id in order_ids:
            if not order_id:
                continue
            try:
                status = client.order_status(order_id)
            except Exception as exc:
                classification = classify_runtime_error(exc, context="order_status")
                rows.append(self.recovery_check_row(
                    label,
                    "Error",
                    str(exc),
                    error_class=classification["class"],
                    error_category=classification["category"],
                ))
                continue
            rows.append(self.recovery_check_row(label, status or "Unknown", f"Order ID {order_id}"))
        return rows

    def recovery_order_ids(self, state):
        position = state.get("open_position") or {}
        pending = state.get("pending_entry") or {}
        return [
            ("Pending Entry Order", pending.get("order_id", "")),
            ("Entry Order", position.get("entry_order_id", "")),
            ("Target Order", position.get("target_order_id", "")),
            ("Stoploss Order", position.get("stoploss_order_id", "")),
        ]

    def recovery_decision(self, state, checks, findings):
        if any(item.get("level") == "ERROR" for item in findings) or state["kill_switch_active"]:
            return "Danger", "Do Not Start New Trade", "Resolve critical recovery findings before trading."
        if state["open_position"] or state["pending_entry"] or findings:
            return "Warning", "Review Required", "Review saved/open state and broker orders before starting new trades."
        if state["mode"] == "LIVE" and any(row["name"] == "Broker Reconciliation" and row["status"] == "Skipped" for row in checks):
            return "Warning", "Review Required", "Connect LIVE Zerodha and rerun recovery check to rule out broker-only positions."
        if any(row["status"] in {"Error", "Failed"} for row in checks):
            return "Warning", "Review Required", "Resolve failed recovery checks before trading."
        return "Good", "Safe To Trade", "No active local recovery state was found."

    def recovery_summary(self, state, findings):
        parts = []
        if state["open_position"]:
            parts.append("Open position present")
        if state["pending_entry"]:
            parts.append("Pending entry present")
        if state["kill_switch_active"]:
            parts.append("Kill switch active")
        if findings:
            parts.append(f"{len(findings)} finding(s)")
        return ", ".join(parts) if parts else "No local recovery state found."

    def recovery_check_row(self, name, status, detail, error_class="", error_category=""):
        row = {"name": name, "status": status, "detail": detail}
        if error_class:
            row["error_class"] = error_class
        if error_category:
            row["error_category"] = error_category
        return row

    def recovery_finding(self, level, code, message, raw=None):
        return {
            "level": str(level or "WARN").upper(),
            "code": str(code or ""),
            "message": str(message or ""),
            "order_id": str((raw or {}).get("order_id", "")) if isinstance(raw, dict) else "",
            "status": str((raw or {}).get("status", "")) if isinstance(raw, dict) else "",
            "error_class": str((raw or {}).get("error_class", "")) if isinstance(raw, dict) else "",
            "error_category": str((raw or {}).get("error_category", "")) if isinstance(raw, dict) else "",
        }

    def position_summary(self, position):
        signal = position.get("signal", {}) if position else {}
        return f"{signal.get('instrument', '')} qty {position.get('quantity', '')} entry {position.get('entry_price', '')}"

    def pending_summary(self, pending):
        signal = pending.get("signal", {}) if pending else {}
        return f"{signal.get('instrument', '')} order {pending.get('order_id', '')} limit {pending.get('limit_price', '')}"

    def active_live_session(self):
        return self.executor.live_real_session or self.executor.live_paper_session

    def candle_snapshot(self, name="NIFTY", limit=200):
        session = self.active_live_session()
        name = str(name or "NIFTY").upper()
        limit = max(1, min(int(limit or 200), 1000))
        if not session:
            return {
                "name": name,
                "session_id": "",
                "mode": "",
                "active": [],
                "completed": [],
                "message": "Start paper or live trading to see candle builder output.",
            }

        with session.state_lock:
            if name == "NIFTY":
                frame = session.nifty
                builder_key = "NIFTY"
            elif name == "CE":
                frame = session.options[0] if len(session.options) > 0 else pd.DataFrame()
                builder_key = "OPTION_0"
            elif name == "PE":
                frame = session.options[1] if len(session.options) > 1 else pd.DataFrame()
                builder_key = "OPTION_1"
            else:
                raise ValueError("Candle name must be NIFTY, CE, or PE.")
            active = session.candle_builder.snapshot(builder_key)
            completed = self.candle_rows(frame, limit)
            return {
                "name": name,
                "session_id": session.session_id,
                "mode": session.mode,
                "interval_minutes": session.interval_minutes,
                "active": [self.candle_row(active)] if active else [],
                "completed": completed,
                "stats": dict(session.candle_builder.stats),
                "message": "",
            }

    def candle_rows(self, frame, limit):
        if frame is None or frame.empty:
            return []
        rows = []
        columns = set(frame.columns)
        for _index, row in frame.tail(limit).iterrows():
            rows.append(self.candle_row({
                "datetime": row.get("datetime", row.get("date", "")) if "datetime" in columns or "date" in columns else "",
                "open": row.get("open", ""),
                "high": row.get("high", ""),
                "low": row.get("low", ""),
                "close": row.get("close", ""),
                "volume": row.get("volume", ""),
            }))
        return rows

    def candle_row(self, row):
        if not row:
            return {}
        return {
            "time": self.clean_json_value(row.get("datetime", row.get("time", ""))),
            "open": self.clean_json_value(row.get("open", "")),
            "high": self.clean_json_value(row.get("high", "")),
            "low": self.clean_json_value(row.get("low", "")),
            "close": self.clean_json_value(row.get("close", "")),
            "volume": self.clean_json_value(row.get("volume", "")),
        }

    def clean_json_value(self, value):
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        if hasattr(value, "isoformat"):
            return value.isoformat(sep=" ")
        return value

    def start_login(self, payload):
        mode = self.validate_zerodha_mode(payload.get("mode") or "PAPER")
        blockers = self.blocking_connection_modes(mode)
        if blockers:
            raise ValueError(f"{self.auth_label(blockers[0])} is already connected.")
        api_key = str(payload.get("api_key") or "").strip()
        api_secret = str(payload.get("api_secret") or "").strip()
        if not api_key or not api_secret:
            raise ValueError("API key and API secret are required.")
        client = ZerodhaClient(api_key, api_secret)
        self.pending_auth[mode] = {
            "api_key": api_key,
            "api_secret": api_secret,
            "client": client,
            "created_at": time.time(),
        }
        self.set_status(f"{self.auth_label(mode)} login started")
        return {"login_url": client.login_url(), "redirect_url": self.redirect_url, "mode": mode}

    def disconnect_zerodha(self, mode):
        mode = self.validate_zerodha_mode(mode)
        client = self.zerodha_clients_by_mode.get(mode)
        if not client:
            return {"disconnected": False, "message": f"{self.auth_label(mode)} is not connected."}

        if mode in {"PAPER", "LIVE"}:
            try:
                self.executor.stop()
            except Exception:
                pass
        try:
            client.stop_ticker()
        except Exception:
            pass

        self.zerodha_clients_by_mode[mode] = None
        self.zerodha_auth_profiles[mode] = None
        self.zerodha_auth_login_times[mode] = ""
        self.account_margins[mode] = self.paper_balance_snapshot() if mode == "PAPER" else {"available": None, "updated_at": "", "error": ""}
        self.pending_auth.pop(mode, None)
        if self.current_mode == mode:
            self.executor.zerodha = None
        self.current_token_map = {}
        self.tick_buffer = {"NIFTY": [], "CE": [], "PE": []}
        self.tick_rate_windows = {"NIFTY": deque(), "CE": deque(), "PE": deque()}
        self.tick_rates = {"NIFTY": 0, "CE": 0, "PE": 0}
        self.live_log_active_rows = []
        self.live_order_history_rows = []
        self.live_trade_snapshot = {}
        self.live_health_snapshot = {}
        self.set_status(f"{self.auth_label(mode)} disconnected")
        return {"disconnected": True, "mode": mode}

    def finish_login(self, request_token, mode=""):
        mode = str(mode or "").upper()
        mode = ZERODHA_MODE_ALIASES.get(mode, mode)
        if mode not in ZERODHA_MODES:
            if len(self.pending_auth) == 1:
                mode = next(iter(self.pending_auth))
            else:
                mode = self.current_mode if self.current_mode in self.pending_auth else "PAPER"
        pending = self.pending_auth.get(mode)
        if not pending:
            raise ValueError("No pending Zerodha login found. Start login from the web app first.")
        client = pending["client"]
        access_token = client.generate_session(request_token)
        self.auth_store.save_access_token(access_token)
        profile = client.profile()
        self.zerodha_clients_by_mode[mode] = client
        self.zerodha_auth_profiles[mode] = profile or {}
        self.zerodha_auth_login_times[mode] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.pending_auth.pop(mode, None)
        self.sync_zerodha_client_for_mode(mode)
        self.refresh_margin(mode, raise_errors=False)
        self.set_status(f"{self.auth_label(mode)} connected")
        return profile

    def refresh_margin(self, mode, raise_errors=True):
        mode = self.validate_zerodha_mode(mode or "LIVE")
        if mode == "PAPER":
            snapshot = self.paper_balance_snapshot()
            self.account_margins["PAPER"] = snapshot
            return snapshot
        client = self.zerodha_clients_by_mode.get(mode)
        if not client:
            if raise_errors:
                raise ValueError(f"Connect {self.auth_label(mode)} first.")
            return self.account_margins.get(mode, {})
        try:
            margin = client.available_margin()
            fetched_at = datetime.now()
            profile = self.zerodha_auth_profiles.get(mode) or {}
            snapshot = {
                "available": float(margin) if margin is not None else None,
                "updated_at": fetched_at.strftime("%Y-%m-%d %H:%M:%S"),
                "error": "" if margin is not None else "Margin unavailable",
            }
            if mode == "LIVE":
                save_real_account_snapshot({
                    "fetched_at": snapshot["updated_at"],
                    "available_margin": snapshot["available"],
                    "used_margin": None,
                    "broker_user_id": profile.get("user_id") or profile.get("client_id") or "",
                    "source": "Zerodha",
                    "valid_until": (fetched_at + timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"),
                })
        except Exception as exc:
            classification = classify_runtime_error(exc, context="margin")
            snapshot = {
                "available": None,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(exc),
                "error_class": classification["class"],
                "error_category": classification["category"],
            }
            if raise_errors:
                self.account_margins[mode] = snapshot
                raise
        self.account_margins[mode] = snapshot
        return snapshot

    def run_backtest_job(self, payload):
        payload = self.normalise_backtest_payload(payload)
        settings_values = payload.get("settings") or load_settings_profiles()["backtest"]
        settings = settings_from_values(settings_values)
        data_source = self.backtest_data_source(payload)
        try:
            if data_source == "zerodha":
                self.set_status("Fetching Zerodha candles for backtest...")
                nifty, options, source_metadata = self.load_zerodha_backtest_data(payload, settings)
                settings = {
                    **settings,
                    "data_source": source_metadata["data_source_label"],
                    "broker_connected": "Yes (Virtual/Paper Zerodha)",
                    "chart_interval": source_metadata["interval"],
                    "start_date": source_metadata["from"],
                    "end_date": source_metadata["to"],
                }
            else:
                nifty, options, source_metadata = self.load_manual_backtest_data(payload)
                settings = {
                    **settings,
                    "data_source": "uploaded/server file",
                    "broker_connected": "No",
                }
            backtest_folder = result_folder("backtest")
            output_path = os.path.join(backtest_folder, f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
            self.set_status("Running backtest...")
            balance, trades = run_backtest(nifty, options, settings, output_path)
            result = {
                "balance": balance,
                "trade_count": len(trades),
                "output_path": output_path,
                "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data_source": source_metadata.get("data_source_label", "Manual Upload"),
                "source_metadata": source_metadata,
            }
            with self.lock:
                self.last_backtest = result
            self.set_status(f"Backtest complete: {len(trades)} trades")
            return result
        except Exception as exc:
            self.set_status(f"Backtest failed: {exc}")
            raise

    def run_risk_settings_optimizer_job(self, payload):
        self.start_optimizer_progress("risk_settings", "Risk Settings Optimizer", "Starting risk settings optimizer...")
        payload = self.normalise_backtest_payload(payload)
        settings_values = payload.get("settings") or load_settings_profiles()["backtest"]
        settings = settings_from_values(settings_values)
        data_source = self.backtest_data_source(payload)
        try:
            if data_source == "zerodha":
                self.update_optimizer_progress("risk_settings", {"stage": "Data", "message": "Fetching Zerodha candles...", "percent": 0})
                self.set_status("Fetching Zerodha candles for risk settings optimizer...")
                nifty, options, source_metadata = self.load_zerodha_backtest_data(payload, settings)
                settings = {
                    **settings,
                    "data_source": source_metadata["data_source_label"],
                    "broker_connected": "Yes (Virtual/Paper Zerodha)",
                    "chart_interval": source_metadata["interval"],
                    "start_date": source_metadata["from"],
                    "end_date": source_metadata["to"],
                }
            else:
                self.update_optimizer_progress("risk_settings", {"stage": "Data", "message": "Loading uploaded/server candles...", "percent": 0})
                nifty, options, source_metadata = self.load_manual_backtest_data(payload)
                settings = {
                    **settings,
                    "data_source": "uploaded/server file",
                    "broker_connected": "No",
                }
            optimizer_folder = result_folder("backtest_risk_setting_optimizer")
            self.set_status("Running backtest risk settings optimizer...")
            result = run_risk_settings_optimizer(
                nifty,
                options,
                settings,
                optimizer_folder,
                source_metadata=source_metadata,
                optimizer_config=payload.get("optimizer") or {},
                progress_callback=self.optimizer_progress_callback("risk_settings"),
                stop_requested=self.optimizer_stop_requested_callback("risk_settings"),
            )
            with self.lock:
                self.last_backtest = result
            self.finish_optimizer_progress("risk_settings", f"Risk settings optimizer complete: {result['runs']} runs")
            return result
        except OptimizerStopped as exc:
            message = str(exc)
            self.stop_optimizer_progress("risk_settings", message)
            return {
                "stopped": True,
                "message": message,
                "progress": self.optimizer_progress.get("risk_settings"),
            }
        except Exception as exc:
            self.fail_optimizer_progress("risk_settings", f"Risk settings optimizer failed: {exc}")
            raise

    def run_trading_tab_optimizer_job(self, payload):
        self.start_optimizer_progress("trading_tab", "Trading Tab Optimizer", "Starting Trading tab optimizer...")
        payload = self.normalise_backtest_payload(payload)
        settings_values = payload.get("settings") or load_settings_profiles()["backtest"]
        settings = settings_from_values(settings_values)
        data_source = self.backtest_data_source(payload)
        try:
            if data_source == "zerodha":
                self.update_optimizer_progress("trading_tab", {"stage": "Data", "message": "Fetching Zerodha candles...", "percent": 0})
                self.set_status("Fetching Zerodha candles for Trading tab optimizer...")
                nifty, options, source_metadata = self.load_zerodha_backtest_data(payload, settings)
                settings = {
                    **settings,
                    "data_source": source_metadata["data_source_label"],
                    "broker_connected": "Yes (Virtual/Paper Zerodha)",
                    "chart_interval": source_metadata["interval"],
                    "start_date": source_metadata["from"],
                    "end_date": source_metadata["to"],
                }
            else:
                self.update_optimizer_progress("trading_tab", {"stage": "Data", "message": "Loading uploaded/server candles...", "percent": 0})
                nifty, options, source_metadata = self.load_manual_backtest_data(payload)
                settings = {
                    **settings,
                    "data_source": "uploaded/server file",
                    "broker_connected": "No",
                }
            optimizer_folder = result_folder("backtest_trading_tab_optimizer")
            self.set_status("Running backtest Trading tab optimizer...")
            result = run_trading_tab_optimizer(
                nifty,
                options,
                settings,
                optimizer_folder,
                source_metadata=source_metadata,
                optimizer_config=payload.get("optimizer") or {},
                progress_callback=self.optimizer_progress_callback("trading_tab"),
                stop_requested=self.optimizer_stop_requested_callback("trading_tab"),
            )
            with self.lock:
                self.last_backtest = result
            self.finish_optimizer_progress("trading_tab", f"Trading tab optimizer complete: {result['runs']} runs")
            return result
        except OptimizerStopped as exc:
            message = str(exc)
            self.stop_optimizer_progress("trading_tab", message)
            return {
                "stopped": True,
                "message": message,
                "progress": self.optimizer_progress.get("trading_tab"),
            }
        except Exception as exc:
            self.fail_optimizer_progress("trading_tab", f"Trading tab optimizer failed: {exc}")
            raise

    def backtest_data_source(self, payload):
        source = str(payload.get("data_source") or payload.get("source") or "manual").strip().lower()
        source = source.replace("-", "_").replace(" ", "_")
        if source in {"zerodha", "zerodha_api", "zerodha_historical", "api"}:
            return "zerodha"
        return "manual"

    def load_manual_backtest_data(self, payload):
        nifty_path = str(payload.get("nifty_path") or "").strip()
        options_payload = payload.get("options") or []
        if not nifty_path:
            raise ValueError("NIFTY CSV path is required.")
        if len(options_payload) < 2:
            raise ValueError("At least CALL and PUT option CSV paths are required.")
        nifty = load_csv_dataframe(nifty_path)
        options = []
        for index, item in enumerate(options_payload[:2]):
            path = str(item.get("path") or "").strip()
            if not path:
                raise ValueError(f"Option {index + 1} CSV path is required.")
            symbol = str(item.get("symbol") or "").strip() or os.path.splitext(os.path.basename(path))[0]
            parsed = parse_option_metadata_from_text(os.path.basename(path))
            options.append(load_csv_dataframe(
                path,
                symbol,
                option_data=True,
                strike=str(item.get("strike") or "").strip(),
                expiry=str(item.get("expiry") or "").strip(),
                option_type=str(item.get("option_type") or parsed.get("option_type") or "").strip(),
            ))
        return nifty, options, {
            "data_source": "manual",
            "data_source_label": "Manual Upload/Server File",
            "nifty_path": nifty_path,
            "contracts": [
                {
                    "option_type": option.attrs.get("option_type", ""),
                    "tradingsymbol": option.attrs.get("tradingsymbol", ""),
                    "strike": option.attrs.get("strike", ""),
                    "expiry": option.attrs.get("expiry", ""),
                    "path": str(item.get("path") or "").strip(),
                }
                for option, item in zip(options, options_payload[:2])
            ],
        }

    def load_zerodha_backtest_data(self, payload, settings):
        client = self.zerodha_clients_by_mode.get("PAPER")
        if not client:
            raise ValueError("Connect Virtual/Paper Zerodha first.")
        options_payload = payload.get("options") or []
        if len(options_payload) < 2:
            raise ValueError("Call and Put strike/expiry are required for Zerodha historical backtest data.")
        call = options_payload[0]
        put = options_payload[1]
        interval = normalise_interval(
            payload.get("history_interval")
            or payload.get("interval")
            or settings.get("chart_interval")
        )
        nifty, options, metadata = fetch_zerodha_backtest_data(
            client,
            payload.get("trade_date") or payload.get("backtest_date") or payload.get("date"),
            interval,
            settings,
            call.get("strike"),
            call.get("expiry"),
            put.get("strike"),
            put.get("expiry"),
        )
        metadata["connected_mode"] = "PAPER"
        return nifty, options, metadata

    def run_live_backtest_optimizer_job(self, payload):
        mode = self.validate_zerodha_mode(payload.get("mode") or "PAPER")
        if mode != "PAPER":
            raise ValueError("NIFTY optimizer must use the Virtual/Paper Zerodha connection.")
        self.sync_zerodha_client_for_mode(mode)
        client = self.executor.zerodha
        if not client:
            raise ValueError("Connect Virtual/Paper Zerodha first.")
        settings_values = payload.get("settings") or load_settings_profiles()["backtest"]
        settings = settings_from_values(settings_values)
        interval = normalise_interval(payload.get("history_interval") or settings.get("chart_interval"))
        settings["chart_interval"] = interval
        nifty_token = parse_instrument_token(payload.get("nifty_token"), "NIFTY token")
        range_months = payload.get("date_range_months") or payload.get("range_months")
        if range_months:
            start_date, end_date = date_range_from_months(range_months)
        else:
            start_date = str(payload.get("start_date") or "").strip()
            end_date = str(payload.get("end_date") or "").strip()
        if not start_date or not end_date:
            raise ValueError("Choose a date range: last 1, 2, 3, or 6 months.")

        self.set_status("Running NIFTY RSI optimizer...")
        output_folder = result_folder("backtest_live")
        result = run_live_backtest_optimizer(
            client,
            nifty_token,
            None,
            start_date,
            end_date,
            interval,
            settings,
            output_folder,
        )
        with self.lock:
            self.last_backtest = result
        self.set_status(f"NIFTY optimizer complete: {result['runs']} runs, {result['days_used']} days")
        return result

    def normalise_backtest_payload(self, payload):
        payload = dict(payload or {})
        if isinstance(payload.get("settings"), str):
            try:
                payload["settings"] = json.loads(payload["settings"])
            except json.JSONDecodeError:
                payload["settings"] = {}
        if isinstance(payload.get("options"), str):
            try:
                payload["options"] = json.loads(payload["options"])
            except json.JSONDecodeError:
                payload["options"] = []
        if payload.get("options"):
            return payload
        payload["nifty_path"] = payload.get("nifty_file") or payload.get("nifty_path") or ""
        payload["options"] = [
            {
                "path": payload.get("call_file") or payload.get("call_path") or "",
                "symbol": payload.get("call_symbol") or "NIFTY_CE",
                "strike": payload.get("call_strike") or "",
                "expiry": payload.get("call_expiry") or "",
                "option_type": "CE",
            },
            {
                "path": payload.get("put_file") or payload.get("put_path") or "",
                "symbol": payload.get("put_symbol") or "NIFTY_PE",
                "strike": payload.get("put_strike") or "",
                "expiry": payload.get("put_expiry") or "",
                "option_type": "PE",
            },
        ]
        return payload

    def fetch_nifty_token(self, mode):
        mode = self.validate_zerodha_mode(mode)
        self.sync_zerodha_client_for_mode(mode)
        if not self.executor.zerodha:
            raise ValueError(f"Connect {self.auth_label(mode)} first.")
        return {"token": self.executor.zerodha.get_nifty50_token()}

    def fetch_option_contract(self, mode, payload):
        mode = self.validate_zerodha_mode(mode)
        self.sync_zerodha_client_for_mode(mode)
        if not self.executor.zerodha:
            raise ValueError(f"Connect {self.auth_label(mode)} first.")
        contract = self.executor.zerodha.find_option_contract(
            option_type=payload.get("option_type"),
            strike=payload.get("strike"),
            expiry=payload.get("expiry"),
            name="NIFTY",
        )
        return {
            "tradingsymbol": contract["tradingsymbol"],
            "instrument_token": contract["instrument_token"],
            "token": contract["instrument_token"],
            "expiry": str(contract.get("expiry", ""))[:10],
            "strike": contract.get("strike", payload.get("strike", "")),
            "option_type": contract.get("instrument_type", payload.get("option_type", "")),
        }

    def option_contracts_from_payload(self, options):
        contracts = []
        for index, item in enumerate(options or []):
            name = "Call" if index == 0 else "Put"
            symbol = str(item.get("tradingsymbol") or item.get("symbol") or "").strip()
            raw_token = item.get("token") or item.get("instrument_token")
            if not symbol or raw_token in ("", None):
                raise ValueError(f"Enter tradingsymbol and token for {name.lower()} option.")
            contracts.append({
                "tradingsymbol": symbol,
                "token": parse_instrument_token(raw_token, f"{name} token"),
                "strike": str(item.get("strike") or "").strip(),
                "expiry": str(item.get("expiry") or "").strip(),
                "option_type": str(item.get("option_type") or "").strip().upper(),
            })
        if len(contracts) < 2:
            raise ValueError("CALL and PUT contracts are required.")
        return contracts[:2]

    def token_map_from_payload(self, payload):
        token_map = {parse_instrument_token(payload.get("nifty_token"), "NIFTY token"): "NIFTY"}
        for index, contract in enumerate(self.option_contracts_from_payload(payload.get("options"))):
            token_map[int(contract["token"])] = f"OPTION_{index}"
        return token_map

    def start_market_feed(self, mode, payload):
        mode = str(mode or "PAPER").upper()
        if mode not in {"PAPER", "LIVE"}:
            raise ValueError("Market feed can be started only from Paper or Live trading.")
        self.sync_zerodha_client_for_mode(mode)
        token_map = self.token_map_from_payload(payload)
        self.current_token_map = token_map
        self.executor.start_market_feed(
            list(token_map.keys()),
            on_ticks=self.on_ticks,
            on_connect=lambda _response: self.set_status("Feed connected"),
            on_close=lambda code, reason: self.set_status(f"Feed closed ({code}) {reason}"),
        )
        self.set_status("Feed connecting...")
        return {"tokens": list(token_map.keys()), "status": "connecting"}

    def start_live(self, mode, payload):
        mode = str(mode or "PAPER").upper()
        if mode not in {"PAPER", "LIVE"}:
            raise ValueError("Trading can be started only from Paper or Live trading.")
        prepared = self.prepare_live_start(mode, payload)
        with self.lock:
            self.update_session_summary(
                {
                    "mode": mode,
                    "session_status": "trading started",
                    "account_balance": prepared["settings"].get("balance"),
                    "session_start_balance": prepared["settings"].get("balance"),
                    "session_pnl": 0,
                    "session_trade_count": 0,
                },
                mode=mode,
            )
        thread = threading.Thread(target=self._run_live_worker, args=(mode, payload, prepared), daemon=True)
        thread.start()
        self.set_status(f"{mode.title()} live worker starting...")
        return {"started": True, "message": f"{mode} start checks passed; worker starting."}

    def prepare_live_start(self, mode, payload):
        self.sync_zerodha_client_for_mode(mode)
        if not self.executor.zerodha:
            raise ValueError(f"Connect {self.auth_label(mode)} first.")
        settings_profile = "paper" if mode == "PAPER" else "real"
        profiles = load_settings_profiles()
        settings_values = payload.get("settings") or profiles[settings_profile]
        if mode == "PAPER":
            settings_values = {**settings_values, "balance": profiles["paper"].get("balance", settings_values.get("balance"))}
        settings = settings_from_values(settings_values)
        if mode == "LIVE":
            self.require_real_live_start_safety()
        history_days = int(payload.get("history_days") or 5)
        interval = normalise_interval(payload.get("history_interval") or settings.get("chart_interval"))
        contracts = self.option_contracts_from_payload(payload.get("options"))
        nifty_token = str(payload.get("nifty_token") or "").strip()
        if not nifty_token:
            raise ValueError("NIFTY token is required.")
        token_map = {parse_instrument_token(nifty_token, "NIFTY token"): "NIFTY"}
        for index, contract in enumerate(contracts):
            token_map[int(contract["token"])] = f"OPTION_{index}"
        return {
            "profiles": profiles,
            "settings": settings,
            "history_days": history_days,
            "interval": interval,
            "contracts": contracts,
            "nifty_token": nifty_token,
            "token_map": token_map,
        }

    def _run_live_worker(self, mode, payload, prepared=None):
        try:
            prepared = prepared or self.prepare_live_start(mode, payload)
            settings = prepared["settings"]
            with self.lock:
                self.live_log_active_rows = []
                self.live_order_history_rows = []
                self.live_trade_snapshot = {}
                self.live_health_snapshot = {}
                self.update_session_summary(
                    {
                        "mode": mode,
                        "session_status": "trading started",
                        "session_id": "",
                        "account_balance": settings["balance"],
                        "session_start_balance": settings["balance"],
                        "session_pnl": 0,
                        "session_trade_count": 0,
                    },
                    mode=mode,
                )
                if mode == "PAPER":
                    self.account_margins["PAPER"] = self.paper_balance_snapshot()
            history_days = prepared["history_days"]
            interval = prepared["interval"]
            contracts = prepared["contracts"]
            nifty_token = prepared["nifty_token"]
            token_map = prepared["token_map"]
            self.current_token_map = token_map
            self.set_status("Fetching historical candles...")
            nifty, options = self.executor.fetch_live_history(
                nifty_token,
                contracts,
                days=history_days,
                interval=interval,
                settings=settings,
            )
            if mode == "PAPER":
                save_path = timestamped_file("paper_trading", result_folder("paper_trading"))
                session = self.executor.start_live_paper_trading(
                    nifty,
                    options,
                    token_map,
                    settings,
                    save_path,
                    on_trade=self.on_trade,
                    on_order_update=self.on_order_update,
                    on_alert=self.on_alert,
                    on_ticks=self.on_ticks,
                    on_connect=lambda _response: self.set_status("Live paper feed connected"),
                    on_close=lambda code, reason: self.set_status(f"Feed closed ({code}) {reason}"),
                )
                with self.lock:
                    health = session.health_snapshot()
                    health["session_status"] = "trading started"
                    self.update_session_summary(health, mode="PAPER")
                self.set_status("Live paper feed connecting...")
                return
            save_path = timestamped_file("real_money_trading", result_folder("real_money_trading"))
            session = self.executor.start_live_real_trading(
                nifty,
                options,
                token_map,
                settings,
                save_path,
                on_trade=self.on_trade,
                on_order_update=self.on_order_update,
                on_alert=self.on_alert,
                on_ticks=self.on_ticks,
                on_connect=lambda _response: self.set_status("Real trading feed connected"),
                on_close=lambda code, reason: self.set_status(f"Feed closed ({code}) {reason}"),
            )
            with self.lock:
                health = session.health_snapshot()
                health["session_status"] = "trading started"
                self.update_session_summary(health, mode="LIVE")
            self.set_status("Real trading feed connecting...")
        except Exception as exc:
            with self.lock:
                self.session_summary["session_status"] = "Idle"
                self.session_summary["session_id"] = ""
                self.session_summary["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.set_status(f"Live start failed: {exc}")

    def require_real_live_start_safety(self):
        failures = self.real_live_start_safety_failures()
        if failures:
            raise ValueError("Real-money live start blocked: " + "; ".join(failures))
        margin = self.refresh_margin("LIVE", raise_errors=True)
        available = margin.get("available")
        error = str(margin.get("error") or "").strip()
        if error:
            raise ValueError(f"Real-money live start blocked: fresh margin check failed: {error}")
        try:
            available = float(available)
        except (TypeError, ValueError):
            raise ValueError("Real-money live start blocked: fresh margin is unavailable")
        if available <= 0:
            raise ValueError("Real-money live start blocked: fresh margin must be greater than zero")
        return True

    def real_live_start_safety_failures(self):
        failures = []
        network = self.network_health.get("LIVE") or {}
        recovery = self.recovery_status.get("LIVE") or {}
        if not self._recent_check(network.get("checked_at")):
            failures.append("run a fresh LIVE network health check")
        elif str(network.get("status", "")).strip().upper() not in LIVE_START_NETWORK_PASS_STATUSES:
            failures.append(f"LIVE network health is {network.get('status') or 'not connected'}")
        if any(str(step.get("status", "")).strip().upper() == "FAILED" for step in network.get("steps") or []):
            failures.append("LIVE network health has failed checks")

        if not self._recent_check(recovery.get("checked_at")):
            failures.append("run a fresh LIVE recovery check")
        else:
            severity = str(recovery.get("severity", "")).strip().upper()
            status = str(recovery.get("status", "")).strip().upper()
            if severity != "GOOD" or status != "SAFE TO TRADE":
                failures.append(f"LIVE recovery status is {recovery.get('status') or 'not safe'}")
        return failures

    def _recent_check(self, checked_at, max_age_seconds=LIVE_START_SAFETY_MAX_AGE_SECONDS):
        if not checked_at:
            return False
        try:
            checked = datetime.strptime(str(checked_at), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
        return (datetime.now() - checked).total_seconds() <= max_age_seconds

    def on_ticks(self, ticks):
        with self.lock:
            now = time.time()
            for tick in ticks or []:
                token = tick.get("instrument_token", "")
                ltp = tick.get("last_price", "")
                volume = tick.get("volume_traded", "")
                bucket, name = self.tick_bucket(token)
                rate = self.update_tick_rate(bucket, now)
                line = {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "name": name,
                    "token": token,
                    "ltp": ltp,
                    "volume": volume,
                    "ticks_per_second": rate,
                }
                rows = self.tick_buffer.setdefault(bucket, [])
                rows.append(line)
                self.tick_buffer[bucket] = rows[-300:]

    def update_tick_rate(self, bucket, timestamp):
        window = self.tick_rate_windows.setdefault(bucket, deque())
        window.append(timestamp)
        while window and timestamp - window[0] > 1:
            window.popleft()
        rate = len(window)
        self.tick_rates[bucket] = rate
        return rate

    def tick_bucket(self, token):
        try:
            token = int(token)
        except (TypeError, ValueError):
            return "NIFTY", "UNKNOWN"
        name = self.current_token_map.get(token, "")
        if name == "NIFTY":
            return "NIFTY", "NIFTY"
        if name == "OPTION_0":
            return "CE", "CE"
        if name == "OPTION_1":
            return "PE", "PE"
        return "NIFTY", name or "UNKNOWN"

    def on_trade(self, trade, balance):
        with self.lock:
            self.trades.append({"balance": balance, "trade": trade, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            self.trades = self.trades[-300:]
            session = self.executor.live_paper_session
            if session and session.mode == "PAPER":
                self.save_paper_balance(balance)
                self.update_session_summary(session.health_snapshot(), mode="PAPER")
            else:
                session = self.executor.live_real_session
                if session:
                    self.update_session_summary(session.health_snapshot(), mode="LIVE")
        self.set_status(f"Trade update received. Balance {float(balance):.2f}")

    def on_alert(self, alert):
        with self.lock:
            self.alerts.append(alert)
            self.alerts = self.alerts[-100:]
        message = str((alert or {}).get("message") or "Alert received")
        self.set_status(message)

    def on_order_update(self, payload):
        if not payload:
            return
        with self.lock:
            health = payload.get("health")
            if health is not None:
                self.live_health_snapshot = health
                self.update_session_summary(health)
            active_orders = payload.get("active_orders")
            if active_orders is not None:
                self.live_log_active_rows = list(active_orders)
            live_trade = payload.get("live_trade")
            if live_trade is not None:
                self.live_trade_snapshot = dict(live_trade)
            event = payload.get("order_event")
            if event:
                self.live_order_history_rows.append(event)
                self.live_order_history_rows = self.live_order_history_rows[-500:]

    def stop(self):
        self.executor.stop()
        with self.lock:
            self.session_summary["session_status"] = "Idle"
            self.session_summary["session_id"] = ""
            self.session_summary["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.set_status("Stopped live session and feed")
        return {"stopped": True}

    def kill_switch(self):
        reason = self.executor.activate_kill_switch("Manual kill switch from web UI")
        if not reason:
            raise ValueError("Start a live or paper session before activating the kill switch.")
        self.set_status(reason)
        return {"reason": reason}

    def square_off(self):
        trade = self.executor.square_off_open_position()
        if trade is None:
            return {"message": "No open position to square off."}
        self.set_status("Open position squared off")
        return {"trade": trade}

    def replay_latest(self):
        return {"path": latest_replay_database(RESULT_FOLDER)}

    def replay_load(self, payload):
        path = str(payload.get("db_file") or payload.get("path") or "").strip()
        if not path:
            path = latest_replay_database(RESULT_FOLDER)
        if not path or not os.path.exists(path):
            raise ValueError("Select an existing SQLite session database.")
        report = build_session_replay(path, session_id=str(payload.get("session_id") or "").strip())
        filter_name = str(payload.get("filter") or "All")
        key = REPLAY_FILTERS.get(filter_name, "timeline")
        items = report.get("timeline", []) if key == "timeline" else report.get("highlights", {}).get(key, [])
        rows = [replay_table_row(item) for item in items]
        result = {
            "path": path,
            "filter": filter_name,
            "summary": report.get("summary", {}),
            "highlights": {name: len(report.get("highlights", {}).get(key, [])) for name, key in REPLAY_FILTERS.items() if key != "timeline"},
            "rows": rows[:1000],
            "items": items[:1000],
            "text": "\n".join(format_replay_report(report, include_payload=False)),
        }
        with self.lock:
            self.last_replay = result
        self.set_status(f"Replay loaded: {result['summary'].get('total_items', 0)} rows")
        return result

    def parity_replay(self, payload):
        path = str(payload.get("db_file") or payload.get("path") or "").strip()
        if not path:
            path = latest_replay_database(RESULT_FOLDER)
        if not path or not os.path.exists(path):
            raise ValueError("Select an existing SQLite session database.")

        nifty_path = str(payload.get("nifty_file") or payload.get("nifty_path") or "").strip()
        ce_path = str(payload.get("ce_file") or payload.get("ce_path") or "").strip()
        pe_path = str(payload.get("pe_file") or payload.get("pe_path") or "").strip()

        settings_values = payload.get("settings") or load_settings_profiles()["backtest"]
        settings = settings_from_values(settings_values)
        nifty = ce = pe = None
        if nifty_path or ce_path or pe_path:
            if not nifty_path or not ce_path or not pe_path:
                raise ValueError("Supply all NIFTY, CE, and PE candle files, or leave all candle files blank to use session DB candles.")
            nifty = load_csv_dataframe(nifty_path, instrument="NIFTY")
            ce = load_csv_dataframe(ce_path, instrument=os.path.basename(ce_path), option_data=True, option_type="CE")
            pe = load_csv_dataframe(pe_path, instrument=os.path.basename(pe_path), option_data=True, option_type="PE")
        report = build_parity_report(
            path,
            nifty,
            [ce, pe] if ce is not None and pe is not None else None,
            settings,
            session_id=str(payload.get("session_id") or "").strip(),
            price_tolerance=float(payload.get("price_tolerance", 0.01) or 0.01),
        )
        self.set_status(
            f"Parity replay complete: {report['summary'].get('mismatches', 0)} mismatches"
        )
        return report

    def market_cue_fetch(self):
        result = self.market_cue.fetch()
        self.set_status("Market cue data fetched")
        return result

    def market_cue_upload_fii_dii(self, payload):
        path = str(payload.get("csv_file") or payload.get("file") or payload.get("fii_dii_file") or "").strip()
        if not path:
            raise ValueError("Upload NSE FII/DII CSV file first.")
        result = self.market_cue.upload_fii_dii(path, scope_hint=payload.get("scope_hint", ""))
        self.set_status(f"NSE FII/DII CSV parsed: {result.get('status')}")
        return result

    def market_cue_analyze(self, payload):
        result = self.market_cue.analyze(payload)
        scoring = result.get("scoring", {})
        self.set_status(f"Market cue analyzed: {scoring.get('bias')} ({scoring.get('confidence')}%)")
        return result

    def market_cue_save(self, payload):
        result = self.market_cue.save(payload)
        report = self.market_cue.report(result["report_id"])
        result["output_path"] = self.write_market_cue_report_file(report)
        self.set_status(f"Market cue report saved: #{result.get('report_id')}")
        return result

    def write_market_cue_report_file(self, report):
        folder = result_folder("market_cue")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(folder, f"market_cue_report_{stamp}.txt")
        report_text = str(report.get("report_text") or "")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(report_text)
            handle.write("\n")
        return path

    def market_cue_history(self):
        return {"reports": self.market_cue.history()}

    def market_cue_report(self, report_id):
        return self.market_cue.report(report_id)

    def market_cue_latest_bias(self):
        return self.market_cue.latest_bias()


class TradeBotRequestHandler(BaseHTTPRequestHandler):
    app_state = None

    def do_GET(self):
        try:
            self.route_get()
        except Exception as exc:
            if _is_client_disconnect(exc):
                return
            try:
                self.send_json({"error": str(exc)}, status=500)
            except Exception as send_exc:
                if _is_client_disconnect(send_exc):
                    return
                raise

    def do_POST(self):
        try:
            self.route_post()
        except Exception as exc:
            if _is_client_disconnect(exc):
                return
            try:
                self.send_json({"error": str(exc)}, status=400)
            except Exception as send_exc:
                if _is_client_disconnect(send_exc):
                    return
                raise

    def route_get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self.send_static_file("index.html")
        if path.startswith("/static/"):
            return self.send_static_file(path[len("/static/"):])
        if self.app_state.options_auto_routes.can_handle_get(path):
            return self.app_state.options_auto_routes.handle_get(self, path, parsed)
        if self.app_state.intraday_routes.can_handle_get(path):
            return self.app_state.intraday_routes.handle_get(self, path, parsed)
        if path == "/api/status":
            return self.send_json(self.app_state.status_payload())
        if path == "/api/candles":
            params = parse_qs(parsed.query)
            name = (params.get("name") or ["NIFTY"])[0]
            limit = (params.get("limit") or ["200"])[0]
            return self.send_json(self.app_state.candle_snapshot(name, limit))
        if path == "/api/settings":
            return self.send_json({
                "profiles": load_settings_profiles(),
                "labels": SETTING_LABELS,
                "defaults": DEFAULT_SETTINGS,
            })
        if path == "/api/replay/latest":
            return self.send_json(self.app_state.replay_latest())
        if path == "/api/market-cue/history":
            return self.send_json(self.app_state.market_cue_history())
        if path == "/api/market-cue/latest-bias":
            return self.send_json(self.app_state.market_cue_latest_bias())
        if path.startswith("/api/market-cue/report/"):
            report_id = path.rsplit("/", 1)[-1]
            return self.send_json(self.app_state.market_cue_report(report_id))
        if path == WEB_REDIRECT_PATH:
            params = parse_qs(parsed.query)
            request_token = (params.get("request_token") or [""])[0]
            status = (params.get("status") or [""])[0]
            mode = (params.get("mode") or [""])[0]
            if status != "success" or not request_token:
                return self.send_html("Zerodha login failed. Return to TradeBot web app.", status=400)
            profile = self.app_state.finish_login(request_token, mode)
            name = profile.get("user_name") or profile.get("user_shortname") or "Zerodha"
            return self.send_html(
                f"<h1>Zerodha connected</h1><p>Connected to {name}. You can return to the TradeBot web app.</p>"
            )
        return self.send_json({"error": "Not found"}, status=404)

    def route_post(self):
        parsed = urlparse(self.path)
        path = parsed.path
        payload = self.read_payload()
        if self.app_state.options_auto_routes.can_handle_post(path):
            return self.app_state.options_auto_routes.handle_post(self, path, payload)
        if self.app_state.intraday_routes.can_handle_post(path):
            return self.app_state.intraday_routes.handle_post(self, path, payload)
        if path == "/api/settings/apply-backtest-live":
            return self.send_json({
                "profiles": apply_backtest_settings_to_live(payload.get("settings") or payload),
            })
        if path == "/api/settings/apply-latest-optimized":
            return self.send_json({"error": "NIFTY optimizer results are report-only and are not applied to paper or real settings."}, status=404)
        if path.startswith("/api/settings/"):
            profile = path.rsplit("/", 1)[-1]
            values = save_settings_profile(profile, payload)
            if profile == "paper":
                self.app_state.account_margins["PAPER"] = self.app_state.paper_balance_snapshot()
                if not self.app_state.executor.live_paper_session:
                    self.app_state.session_summary = self.app_state.empty_session_summary("PAPER")
            return self.send_json({"profile": profile, "values": values})
        if path == "/api/backtest/run":
            return self.send_json(self.app_state.run_backtest_job(payload))
        if path == "/api/backtest/risk-optimize":
            return self.send_json(self.app_state.run_risk_settings_optimizer_job(payload))
        if path == "/api/backtest/trading-optimize":
            return self.send_json(self.app_state.run_trading_tab_optimizer_job(payload))
        if path == "/api/backtest/optimizer-stop":
            return self.send_json(self.app_state.request_optimizer_stop(payload))
        if path == "/api/backtest/zerodha-optimize":
            return self.send_json(self.app_state.run_live_backtest_optimizer_job(payload))
        if path == "/api/market-cue/fetch":
            return self.send_json(self.app_state.market_cue_fetch())
        if path == "/api/market-cue/upload-fii-dii":
            return self.send_json(self.app_state.market_cue_upload_fii_dii(payload))
        if path == "/api/market-cue/analyze":
            return self.send_json(self.app_state.market_cue_analyze(payload))
        if path == "/api/market-cue/save":
            return self.send_json(self.app_state.market_cue_save(payload))
        if path == "/api/zerodha/login":
            return self.send_json(self.app_state.start_login(payload))
        if path == "/api/zerodha/disconnect":
            mode = str(payload.get("mode") or "PAPER").upper()
            return self.send_json(self.app_state.disconnect_zerodha(mode))
        if path == "/api/zerodha/margin":
            mode = str(payload.get("mode") or "LIVE").upper()
            return self.send_json(self.app_state.refresh_margin(mode))
        if path == "/api/network/health":
            mode = str(payload.get("mode") or "PAPER").upper()
            return self.send_json(self.app_state.run_network_health_check(mode))
        if path == "/api/recovery/status":
            mode = str(payload.get("mode") or "PAPER").upper()
            return self.send_json(self.app_state.run_recovery_check(mode))
        if path == "/api/live/fetch-nifty":
            mode = str(payload.get("mode") or "PAPER").upper()
            return self.send_json(self.app_state.fetch_nifty_token(mode))
        if path == "/api/live/fetch-option":
            mode = str(payload.get("mode") or "PAPER").upper()
            return self.send_json(self.app_state.fetch_option_contract(mode, payload))
        if path == "/api/live/start-feed":
            mode = str(payload.get("mode") or "PAPER").upper()
            return self.send_json(self.app_state.start_market_feed(mode, payload))
        if path == "/api/live/start":
            mode = str(payload.get("mode") or "PAPER").upper()
            return self.send_json(self.app_state.start_live(mode, payload))
        if path == "/api/live/stop":
            return self.send_json(self.app_state.stop())
        if path == "/api/live/kill-switch":
            return self.send_json(self.app_state.kill_switch())
        if path == "/api/live/square-off":
            return self.send_json(self.app_state.square_off())
        if path == "/api/replay/load":
            return self.send_json(self.app_state.replay_load(payload))
        if path == "/api/parity/replay":
            return self.send_json(self.app_state.parity_replay(payload))
        return self.send_json({"error": "Not found"}, status=404)

    def read_payload(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            return self.read_multipart_payload(body, content_type)
        if "application/json" in content_type:
            return json.loads(body.decode("utf-8") or "{}")
        stripped = body.strip()
        if stripped.startswith(b"{"):
            return json.loads(stripped.decode("utf-8"))
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        return {key: values[-1] if len(values) == 1 else values for key, values in parsed.items()}

    def read_multipart_payload(self, body, content_type):
        payload = {}
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        message = BytesParser(policy=email_default_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            key = part.get_param("name", header="content-disposition")
            if not key:
                continue
            filename = part.get_filename()
            value = part.get_payload(decode=True) or b""
            if filename:
                if value:
                    payload[key] = self.save_uploaded_bytes(filename, value)
                continue
            charset = part.get_content_charset() or "utf-8"
            payload[key] = value.decode(charset, errors="replace")
        return payload

    def save_uploaded_bytes(self, filename, value):
        original = os.path.basename(filename or "upload")
        safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in original)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(UPLOAD_DIR, f"{stamp}_{safe_name}")
        with open(path, "wb") as handle:
            handle.write(value)
        return path

    def send_json(self, payload, status=200):
        try:
            body = json.dumps(payload, default=json_default).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            if _is_client_disconnect(exc):
                return
            raise

    def send_html(self, body, status=200):
        page = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>TradeBot Zerodha</title>"
            "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:40px;background:#f7f9fc;color:#172033}"
            "main{max-width:680px;background:white;border:1px solid #d8dee9;padding:28px;border-radius:8px}</style>"
            "</head><body><main>"
            f"{body}<p><a href='/'>Open TradeBot web app</a></p>"
            "</main></body></html>"
        )
        encoded = page.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            if _is_client_disconnect(exc):
                return
            raise

    def send_static_file(self, relative_path):
        relative_path = unquote(relative_path).replace("\\", "/").lstrip("/")
        path = os.path.abspath(os.path.join(STATIC_DIR, relative_path))
        if not path.startswith(os.path.abspath(STATIC_DIR)) or not os.path.isfile(path):
            return self.send_json({"error": "Not found"}, status=404)
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as handle:
            body = handle.read()
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            if _is_client_disconnect(exc):
                return
            raise

    def log_message(self, _format, *_args):
        return


def _is_client_disconnect(exc: BaseException) -> bool:
    return (
        isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError))
        or getattr(exc, "winerror", None) in {10053, 10054, 109}
        or getattr(exc, "errno", None) in {32, 54, 104}
    )


def create_server(host=WEB_HOST, port=WEB_PORT):
    app = WebTradeBotApp(host=host, port=port)
    TradeBotRequestHandler.app_state = app
    server = ThreadingHTTPServer((host, int(port)), TradeBotRequestHandler)
    return server, app


def main():
    parser = argparse.ArgumentParser(description="TradeBot web application")
    parser.add_argument("--host", default=WEB_HOST)
    parser.add_argument("--port", default=WEB_PORT, type=int)
    args = parser.parse_args()
    if int(args.port) != WEB_PORT:
        print(f"TradeBot uses fixed port {WEB_PORT}; ignoring requested port {args.port}.")
        args.port = WEB_PORT
    server, app = create_server(args.host, args.port)
    print(f"TradeBot web app: {app.local_url}")
    print(f"Zerodha redirect URL: {app.redirect_url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
