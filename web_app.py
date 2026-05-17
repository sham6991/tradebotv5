import argparse
import json
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

from backtest import run_backtest
from engine import parse_option_metadata_from_text
from event_replay import build_session_replay, format_replay_report
from execution_v2 import Executor
from indicators import clean_and_add_indicators
from position_reconciler import PositionReconciler
from reporting import timestamped_file
from strategy import ensure_option_formula_columns
from ui_replay import REPLAY_FILTERS, latest_replay_database, replay_table_row
from ui_shared import DEFAULT_SETTINGS, SETTING_LABELS, SETTINGS_PROFILE_PATH
from zerodha_auth import DEFAULT_REDIRECT_URL, ZerodhaAuthStore
from zerodha_client import ZerodhaClient


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_FOLDER = os.path.join(BASE_DIR, "results")
STATIC_DIR = os.path.join(BASE_DIR, "web_static")
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")
WEB_HOST = "127.0.0.1"
WEB_PORT = 8006
WEB_REDIRECT_PATH = "/zerodha/callback"


def json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def normalise_interval(value):
    text = str(value or "").strip().lower()
    return {
        "1 min": "minute",
        "1minute": "minute",
        "minute": "minute",
        "2 min": "2minute",
        "2minute": "2minute",
        "3 min": "3minute",
        "3minute": "3minute",
        "5 min": "5minute",
        "5minute": "5minute",
    }.get(text, "3minute")


def normalise_order_product(value):
    text = str(value or "NRML").strip().upper()
    return "MIS" if text in ("MIS", "INTRADAY") else "NRML"


def setting_value(values, key):
    value = (values or {}).get(key, DEFAULT_SETTINGS.get(key, ""))
    if value is None or str(value).strip() == "":
        return DEFAULT_SETTINGS.get(key, "")
    return value


def normalized_settings_profile(values):
    values = values or {}
    return {
        **values,
        **{key: setting_value(values, key) for key in DEFAULT_SETTINGS},
    }


def settings_from_values(values):
    values = {key: setting_value(values, key) for key in DEFAULT_SETTINGS}
    return {
        "balance": float(values["balance"]),
        "lot_size": int(values["lot_size"]),
        "max_trades": int(values["max_trades"]),
        "profit_points": float(values["profit_points"]),
        "safety_points": float(values["safety_points"]),
        "entry_offset": float(values["entry_offset"]),
        "time_exit": int(values["time_exit"]),
        "cooldown": int(values["cooldown"]),
        "chart_interval": normalise_interval(values["chart_interval"]),
        "bullish_threshold": float(values["bullish_threshold"]),
        "bearish_threshold": float(values["bearish_threshold"]),
        "rsi_bull": float(values["rsi_bull"]),
        "rsi_bear": float(values["rsi_bear"]),
        "rsi_reversal_bullish": float(values.get("rsi_reversal_bullish", DEFAULT_SETTINGS["rsi_reversal_bullish"])),
        "rsi_reversal_bearish": float(values.get("rsi_reversal_bearish", DEFAULT_SETTINGS["rsi_reversal_bearish"])),
        "watch_buy_score": float(values.get("watch_buy_score", DEFAULT_SETTINGS["watch_buy_score"])),
        "min_buy_score": float(values["min_buy_score"]),
        "strong_buy_score": float(values.get("strong_buy_score", DEFAULT_SETTINGS["strong_buy_score"])),
        "min_volume_ratio": float(values.get("min_volume_ratio", DEFAULT_SETTINGS["min_volume_ratio"])),
        "min_option_volume": float(values.get("min_option_volume", DEFAULT_SETTINGS["min_option_volume"])),
        "aggression_score_cap": float(values.get("aggression_score_cap", DEFAULT_SETTINGS["aggression_score_cap"])),
        "compression_range_ratio": float(values.get("compression_range_ratio", DEFAULT_SETTINGS["compression_range_ratio"])),
        "expansion_range_ratio": float(values.get("expansion_range_ratio", DEFAULT_SETTINGS["expansion_range_ratio"])),
        "max_chase_range_ratio": float(values.get("max_chase_range_ratio", DEFAULT_SETTINGS["max_chase_range_ratio"])),
        "failed_breakout_penalty": float(values.get("failed_breakout_penalty", DEFAULT_SETTINGS["failed_breakout_penalty"])),
        "early_breakout_min_score": float(values.get("early_breakout_min_score", DEFAULT_SETTINGS["early_breakout_min_score"])),
        "max_daily_loss": float(values["max_daily_loss"]),
        "max_daily_profit": float(values["max_daily_profit"]),
        "max_consecutive_losses": int(values["max_consecutive_losses"]),
        "square_off_time": str(values["square_off_time"]).strip(),
        "order_product": normalise_order_product(values.get("order_product", "NRML")),
    }


def load_settings_profiles():
    try:
        with open(SETTINGS_PROFILE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    real_profile = data.get("real", {})
    if not real_profile.get("zerodha_margin_fetched"):
        real_profile = {**real_profile, "balance": "0"}
    return {
        "backtest": normalized_settings_profile(data.get("backtest", {})),
        "paper": normalized_settings_profile(data.get("paper", {})),
        "real": normalized_settings_profile(real_profile),
    }


def save_settings_profile(profile, values):
    if profile not in {"backtest", "paper", "real"}:
        raise ValueError("Unknown settings profile.")
    os.makedirs(os.path.dirname(SETTINGS_PROFILE_PATH), exist_ok=True)
    profiles = load_settings_profiles()
    profiles[profile] = normalized_settings_profile(values)
    with open(SETTINGS_PROFILE_PATH, "w", encoding="utf-8") as handle:
        json.dump(profiles, handle, indent=2)
    return profiles[profile]


def apply_backtest_settings_to_live(values=None):
    profiles = load_settings_profiles()
    source = normalized_settings_profile(values or profiles["backtest"])
    real_existing = profiles.get("real", {})
    real_preserved = {
        key: real_existing[key]
        for key in ("balance", "zerodha_margin_fetched")
        if key in real_existing
    }

    profiles["backtest"] = source
    profiles["paper"] = normalized_settings_profile(source)
    profiles["real"] = normalized_settings_profile({**source, **real_preserved})

    os.makedirs(os.path.dirname(SETTINGS_PROFILE_PATH), exist_ok=True)
    with open(SETTINGS_PROFILE_PATH, "w", encoding="utf-8") as handle:
        json.dump(profiles, handle, indent=2)
    return {
        "backtest": profiles["backtest"],
        "paper": profiles["paper"],
        "real": profiles["real"],
    }


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
        self.zerodha_clients_by_mode = {"PAPER": None, "LIVE": None}
        self.zerodha_auth_profiles = {"PAPER": None, "LIVE": None}
        self.zerodha_auth_login_times = {"PAPER": "", "LIVE": ""}
        self.pending_auth = {}
        self.account_margins = {
            "PAPER": {"available": None, "updated_at": "", "error": ""},
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

    def auth_label(self, mode):
        return "Real Money Zerodha" if mode == "LIVE" else "Paper Trading Data"

    def sync_zerodha_client_for_mode(self, mode):
        self.current_mode = mode
        self.executor.zerodha = self.zerodha_clients_by_mode.get(mode)
        return self.executor.zerodha

    def connection_blocked(self, mode):
        other = "PAPER" if mode == "LIVE" else "LIVE"
        return bool(self.zerodha_clients_by_mode.get(other))

    def connection_status(self, mode):
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
                "network_health": self.network_health,
                "recovery_status": self.recovery_status,
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
                self.measure_network_step("Zerodha Order Book", client.orders),
            ])
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
            return {
                "name": name,
                "status": "Failed",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "error": str(exc),
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
        if saved_position and not state["open_position"]:
            state["open_position"] = saved_position
            findings.append(self.recovery_finding("WARN", "SAVED_OPEN_POSITION", "Saved open position exists but no active session is loaded."))
        if saved_pending and not state["pending_entry"]:
            state["pending_entry"] = saved_pending
            findings.append(self.recovery_finding("WARN", "SAVED_PENDING_ENTRY", "Saved pending entry exists but no active session is loaded."))

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
            ("Open Position", os.path.join(RESULT_FOLDER, f"{prefix}_open_position.json")),
            ("Pending Entry", os.path.join(RESULT_FOLDER, f"{prefix}_pending_entry.json")),
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
            return glob.glob(os.path.join(RESULT_FOLDER, pattern))
        except OSError:
            return []

    def read_recovery_json(self, mode, kind):
        path = os.path.join(RESULT_FOLDER, f"{mode.lower()}_{kind}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

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
                findings.append(self.recovery_finding("WARN", "ORDER_STATUS_CHECK_FAILED", f"{label} status check failed: {exc}"))
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
                rows.append(self.recovery_check_row(label, "Error", str(exc)))
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

    def recovery_check_row(self, name, status, detail):
        return {"name": name, "status": status, "detail": detail}

    def recovery_finding(self, level, code, message, raw=None):
        return {
            "level": str(level or "WARN").upper(),
            "code": str(code or ""),
            "message": str(message or ""),
            "order_id": str((raw or {}).get("order_id", "")) if isinstance(raw, dict) else "",
            "status": str((raw or {}).get("status", "")) if isinstance(raw, dict) else "",
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
        mode = str(payload.get("mode") or "PAPER").upper()
        if mode not in {"PAPER", "LIVE"}:
            raise ValueError("Mode must be PAPER or LIVE.")
        if self.connection_blocked(mode):
            raise ValueError(f"{self.auth_label('PAPER' if mode == 'LIVE' else 'LIVE')} is already connected.")
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
        mode = str(mode or "").upper()
        if mode not in {"PAPER", "LIVE"}:
            raise ValueError("Mode must be PAPER or LIVE.")
        client = self.zerodha_clients_by_mode.get(mode)
        if not client:
            return {"disconnected": False, "message": f"{self.auth_label(mode)} is not connected."}

        self.executor.stop()
        try:
            client.stop_ticker()
        except Exception:
            pass

        self.zerodha_clients_by_mode[mode] = None
        self.zerodha_auth_profiles[mode] = None
        self.zerodha_auth_login_times[mode] = ""
        self.account_margins[mode] = {"available": None, "updated_at": "", "error": ""}
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
        if mode not in {"PAPER", "LIVE"}:
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
        mode = str(mode or "LIVE").upper()
        client = self.zerodha_clients_by_mode.get(mode)
        if not client:
            if raise_errors:
                raise ValueError(f"Connect {self.auth_label(mode)} first.")
            return self.account_margins.get(mode, {})
        try:
            margin = client.available_margin()
            snapshot = {
                "available": float(margin) if margin is not None else None,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": "" if margin is not None else "Margin unavailable",
            }
        except Exception as exc:
            snapshot = {
                "available": None,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(exc),
            }
            if raise_errors:
                self.account_margins[mode] = snapshot
                raise
        self.account_margins[mode] = snapshot
        return snapshot

    def run_backtest_job(self, payload):
        payload = self.normalise_backtest_payload(payload)
        nifty_path = str(payload.get("nifty_path") or "").strip()
        options_payload = payload.get("options") or []
        if not nifty_path:
            raise ValueError("NIFTY CSV path is required.")
        if len(options_payload) < 2:
            raise ValueError("At least CALL and PUT option CSV paths are required.")
        settings_values = payload.get("settings") or load_settings_profiles()["backtest"]
        settings = settings_from_values(settings_values)
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
        os.makedirs(RESULT_FOLDER, exist_ok=True)
        output_path = os.path.join(RESULT_FOLDER, f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        self.set_status("Running backtest...")
        balance, trades = run_backtest(nifty, options, settings, output_path)
        result = {
            "balance": balance,
            "trade_count": len(trades),
            "output_path": output_path,
            "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self.lock:
            self.last_backtest = result
        self.set_status(f"Backtest complete: {len(trades)} trades")
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
        self.sync_zerodha_client_for_mode(mode)
        if not self.executor.zerodha:
            raise ValueError(f"Connect {self.auth_label(mode)} first.")
        return {"token": self.executor.zerodha.get_nifty50_token()}

    def fetch_option_contract(self, mode, payload):
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
            "expiry": str(contract.get("expiry", ""))[:10],
            "strike": contract.get("strike", payload.get("strike", "")),
            "option_type": contract.get("instrument_type", payload.get("option_type", "")),
        }

    def option_contracts_from_payload(self, options):
        contracts = []
        for index, item in enumerate(options or []):
            symbol = str(item.get("tradingsymbol") or item.get("symbol") or "").strip()
            token = str(item.get("token") or item.get("instrument_token") or "").strip()
            if not symbol or not token:
                raise ValueError(f"Enter tradingsymbol and token for option {index + 1}.")
            contracts.append({
                "tradingsymbol": symbol,
                "token": int(token),
                "strike": str(item.get("strike") or "").strip(),
                "expiry": str(item.get("expiry") or "").strip(),
                "option_type": str(item.get("option_type") or "").strip().upper(),
            })
        if len(contracts) < 2:
            raise ValueError("CALL and PUT contracts are required.")
        return contracts[:2]

    def token_map_from_payload(self, payload):
        token_map = {int(payload.get("nifty_token")): "NIFTY"}
        for index, contract in enumerate(self.option_contracts_from_payload(payload.get("options"))):
            token_map[int(contract["token"])] = f"OPTION_{index}"
        return token_map

    def start_market_feed(self, mode, payload):
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
        thread = threading.Thread(target=self._run_live_worker, args=(mode, payload), daemon=True)
        thread.start()
        self.set_status(f"{mode.title()} live worker starting...")
        return {"started": True}

    def _run_live_worker(self, mode, payload):
        try:
            self.sync_zerodha_client_for_mode(mode)
            if not self.executor.zerodha:
                raise ValueError(f"Connect {self.auth_label(mode)} first.")
            settings_profile = "paper" if mode == "PAPER" else "real"
            settings_values = payload.get("settings") or load_settings_profiles()[settings_profile]
            settings = settings_from_values(settings_values)
            history_days = int(payload.get("history_days") or 5)
            interval = normalise_interval(payload.get("history_interval") or settings.get("chart_interval"))
            contracts = self.option_contracts_from_payload(payload.get("options"))
            nifty_token = str(payload.get("nifty_token") or "").strip()
            if not nifty_token:
                raise ValueError("NIFTY token is required.")
            token_map = self.token_map_from_payload(payload)
            self.current_token_map = token_map
            self.set_status("Fetching historical candles...")
            nifty, options = self.executor.fetch_live_history(nifty_token, contracts, days=history_days, interval=interval)
            os.makedirs(RESULT_FOLDER, exist_ok=True)
            if mode == "PAPER":
                save_path = timestamped_file("paper_trading", RESULT_FOLDER)
                self.executor.start_live_paper_trading(
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
                self.set_status("Live paper feed connecting...")
                return
            save_path = timestamped_file("real_trading", RESULT_FOLDER)
            self.executor.start_live_real_trading(
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
            self.set_status("Real trading feed connecting...")
        except Exception as exc:
            self.set_status(f"Live start failed: {exc}")

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


class TradeBotRequestHandler(BaseHTTPRequestHandler):
    app_state = None

    def do_GET(self):
        try:
            self.route_get()
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def do_POST(self):
        try:
            self.route_post()
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)

    def route_get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self.send_static_file("index.html")
        if path.startswith("/static/"):
            return self.send_static_file(path[len("/static/"):])
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
        if path == "/api/settings/apply-backtest-live":
            return self.send_json({
                "profiles": apply_backtest_settings_to_live(payload.get("settings") or payload),
            })
        if path.startswith("/api/settings/"):
            profile = path.rsplit("/", 1)[-1]
            return self.send_json({"profile": profile, "values": save_settings_profile(profile, payload)})
        if path == "/api/backtest/run":
            return self.send_json(self.app_state.run_backtest_job(payload))
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
        body = json.dumps(payload, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_static_file(self, relative_path):
        relative_path = unquote(relative_path).replace("\\", "/").lstrip("/")
        path = os.path.abspath(os.path.join(STATIC_DIR, relative_path))
        if not path.startswith(os.path.abspath(STATIC_DIR)) or not os.path.isfile(path):
            return self.send_json({"error": "Not found"}, status=404)
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


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
