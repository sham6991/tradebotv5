from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Callable


OWNER_NONE = "NONE"
OWNER_MAIN_APP = "MAIN_APP"
OWNER_OPTIONS_AUTO = "OPTIONS_AUTO"
OWNER_INTRADAY = "INTRADAY"
ALLOWED_OWNERS = {OWNER_NONE, OWNER_MAIN_APP, OWNER_OPTIONS_AUTO, OWNER_INTRADAY}


class WebSocketOwnerController:
    """Small in-process lock for the app module allowed to own Kite websocket data."""

    def __init__(
        self,
        state_path: str | None = None,
        active_ticker_provider: Callable[[str], bool] | None = None,
    ) -> None:
        self.state_path = state_path or ""
        self.active_ticker_provider = active_ticker_provider or (lambda _owner: False)
        self._lock = threading.RLock()
        self.preferred_owner = OWNER_NONE
        self.active_owner = OWNER_NONE
        self.active_mode = ""
        self.active_since = ""
        self.active_ticker_name = ""
        self.active_token_count = 0
        self.active_tokens_sample: list[int] = []
        self.owner_status = "NONE"
        self.blockers: list[str] = []
        self.warnings: list[str] = []
        self.last_error = ""
        self.last_switch_reason = ""
        self.last_updated_at = _now()
        self._load_safe_state()
        self.force_release_if_no_active_ticker("Startup owner audit")

    def get_state(self, zerodha_connected: bool | None = None) -> dict[str, Any]:
        with self._lock:
            connected = bool(zerodha_connected)
            if zerodha_connected is None:
                connected = False
            preferred = self.preferred_owner
            blockers = list(self.blockers)
            if preferred != OWNER_NONE and not connected:
                blockers = list(dict.fromkeys(blockers + ["Zerodha login is required before websocket can start. Your preferred owner has been saved."]))
            state = {
                "preferred_owner": preferred,
                "active_owner": self.active_owner,
                "active_mode": self.active_mode,
                "active_since": self.active_since,
                "active_ticker_name": self.active_ticker_name,
                "active_token_count": self.active_token_count,
                "active_tokens_sample": list(self.active_tokens_sample),
                "owner_status": self.owner_status,
                "zerodha_login_required": preferred != OWNER_NONE and not connected,
                "can_activate_preferred": self.can_activate(preferred, self.active_mode, connected)["allowed"],
                "can_start_main_app": self.can_start_owner(OWNER_MAIN_APP, self.active_mode, connected)["allowed"],
                "can_start_options_auto": self.can_start_owner(OWNER_OPTIONS_AUTO, self.active_mode, connected)["allowed"],
                "can_start_intraday": self.can_start_owner(OWNER_INTRADAY, self.active_mode, connected)["allowed"],
                "blockers": blockers,
                "warnings": list(self.warnings),
                "last_error": self.last_error,
                "last_switch_reason": self.last_switch_reason,
                "last_updated_at": self.last_updated_at,
            }
            state["next_action"] = self._next_action(state)
            return state

    def set_preferred_owner(self, owner: str) -> dict[str, Any]:
        owner = _normalize_owner(owner)
        with self._lock:
            self.preferred_owner = owner
            self.owner_status = "SELECTED" if owner != OWNER_NONE and self.active_owner == OWNER_NONE else self.owner_status
            self.last_switch_reason = f"Preferred websocket owner set to {owner}."
            self._touch()
            self._persist_safe_state()
            return self.get_state()

    def can_activate(self, owner: str, mode: str = "", zerodha_connected: bool = False) -> dict[str, Any]:
        return self.can_start_owner(owner, mode, zerodha_connected)

    def can_start_owner(self, owner: str, mode: str = "", zerodha_connected: bool = False) -> dict[str, Any]:
        owner = _normalize_owner(owner)
        mode = _normalize_mode(mode)
        with self._lock:
            if owner == OWNER_NONE:
                return {"allowed": False, "blockers": ["Select a websocket owner before activating."]}
            if not zerodha_connected:
                return {
                    "allowed": False,
                    "blockers": ["Zerodha login is required before websocket can start. Your preferred owner has been saved."],
                    "owner_status": "WAITING_FOR_LOGIN",
                }
            if self.active_owner not in {OWNER_NONE, owner}:
                return {
                    "allowed": False,
                    "blockers": [self.build_blocker_for(owner)],
                    "owner_status": self.owner_status,
                }
            return {"allowed": True, "blockers": [], "owner_status": self.owner_status or "SELECTED", "mode": mode}

    def acquire_owner(
        self,
        owner: str,
        mode: str = "",
        ticker_name: str = "",
        tokens: list[int] | tuple[int, ...] | None = None,
        reason: str = "",
        zerodha_connected: bool = True,
    ) -> dict[str, Any]:
        owner = _normalize_owner(owner)
        tokens = [int(token) for token in list(tokens or []) if _is_positive_int(token)]
        with self._lock:
            allowed = self.can_start_owner(owner, mode, zerodha_connected)
            if not allowed.get("allowed"):
                self.blockers = list(allowed.get("blockers") or [])
                self.owner_status = allowed.get("owner_status") or "BLOCKED"
                self.last_error = self.blockers[0] if self.blockers else ""
                self._touch()
                return {**self.get_state(zerodha_connected), "acquired": False, "allowed": False}
            same_owner = self.active_owner == owner
            self.active_owner = owner
            self.active_mode = _normalize_mode(mode)
            self.active_since = self.active_since if same_owner and self.active_since else _now()
            self.active_ticker_name = str(ticker_name or self.active_ticker_name or _default_ticker_name(owner, self.active_mode))
            self.active_token_count = len(tokens) if tokens else self.active_token_count
            self.active_tokens_sample = tokens[:12] if tokens else self.active_tokens_sample
            self.owner_status = "RECONNECTING" if same_owner and reason and "reconnect" in reason.lower() else "ACTIVE"
            self.blockers = []
            self.last_error = ""
            self.last_switch_reason = reason or f"{owner} acquired websocket owner lock."
            self._touch()
            self._persist_safe_state()
            return {**self.get_state(zerodha_connected), "acquired": True, "allowed": True}

    def release_owner(self, owner: str, reason: str = "") -> dict[str, Any]:
        owner = _normalize_owner(owner)
        with self._lock:
            if self.active_owner != OWNER_NONE and owner != self.active_owner:
                self.last_error = f"{owner} cannot release websocket owner lock held by {self.active_owner}."
                self.blockers = [self.last_error]
                self._touch()
                return {**self.get_state(True), "released": False, "allowed": False}
            old_owner = self.active_owner
            self.active_owner = OWNER_NONE
            self.active_mode = ""
            self.active_since = ""
            self.active_ticker_name = ""
            self.active_token_count = 0
            self.active_tokens_sample = []
            self.owner_status = "STOPPED" if old_owner != OWNER_NONE else "NONE"
            self.blockers = []
            self.last_error = ""
            self.last_switch_reason = reason or f"{old_owner} released websocket owner lock."
            self._touch()
            self._persist_safe_state()
            return {**self.get_state(True), "released": True, "allowed": True}

    def force_release_if_no_active_ticker(self, reason: str = "") -> dict[str, Any]:
        with self._lock:
            if self.active_owner == OWNER_NONE:
                return self.get_state()
            try:
                active = bool(self.active_ticker_provider(self.active_owner))
            except Exception:
                active = False
            if active:
                return self.get_state(True)
            stale = self.active_owner
            self.warnings = list(dict.fromkeys(list(self.warnings) + ["Previous websocket owner state was stale and has been cleared."]))
            self.active_owner = OWNER_NONE
            self.active_mode = ""
            self.active_since = ""
            self.active_ticker_name = ""
            self.active_token_count = 0
            self.active_tokens_sample = []
            self.owner_status = "STOPPED"
            self.last_switch_reason = reason or f"Cleared stale websocket owner {stale}."
            self._touch()
            self._persist_safe_state()
            return self.get_state()

    def mark_owner_status(self, owner: str, status: str, reason: str = "") -> dict[str, Any]:
        owner = _normalize_owner(owner)
        with self._lock:
            if owner != OWNER_NONE and self.active_owner not in {OWNER_NONE, owner}:
                self.last_error = self.build_blocker_for(owner)
                self.blockers = [self.last_error]
            else:
                self.owner_status = str(status or self.owner_status or "NONE").upper()
                self.last_switch_reason = reason or self.last_switch_reason
                if self.owner_status == "ERROR":
                    self.last_error = reason
            self._touch()
            self._persist_safe_state()
            return self.get_state(True)

    def build_blocker_for(self, owner: str) -> str:
        owner = _normalize_owner(owner)
        if self.active_owner == OWNER_NONE or self.active_owner == owner:
            return ""
        return f"{_owner_label(owner)} cannot start websocket because {_owner_label(self.active_owner)} currently owns the Zerodha feed."

    def _load_safe_state(self) -> None:
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle) or {}
        except Exception as exc:
            self.warnings.append(f"Websocket owner state could not be loaded: {exc}")
            return
        self.preferred_owner = _normalize_owner(payload.get("preferred_owner"))
        self.active_owner = _normalize_owner(payload.get("last_active_owner"))
        self.active_mode = _normalize_mode(payload.get("last_mode"))
        self.owner_status = str(payload.get("last_status") or "NONE").upper()
        self.last_updated_at = str(payload.get("last_updated_at") or self.last_updated_at)

    def _persist_safe_state(self) -> None:
        if not self.state_path:
            return
        payload = {
            "preferred_owner": self.preferred_owner,
            "last_active_owner": self.active_owner,
            "last_mode": self.active_mode,
            "last_status": self.owner_status,
            "last_updated_at": self.last_updated_at,
        }
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp_path = self.state_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp_path, self.state_path)
        except Exception as exc:
            self.last_error = f"Websocket owner state could not be saved: {exc}"

    def _touch(self) -> None:
        self.last_updated_at = _now()

    def _next_action(self, state: dict[str, Any]) -> str:
        if state.get("blockers"):
            return str(state["blockers"][0])
        if state.get("zerodha_login_required"):
            return "Login to Zerodha to activate the preferred websocket owner."
        if state.get("active_owner") == OWNER_NONE:
            return "Select and activate a websocket owner."
        return f"{_owner_label(state.get('active_owner'))} owns the websocket feed."


def _normalize_owner(owner: Any) -> str:
    value = str(owner or OWNER_NONE).strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "MAIN": OWNER_MAIN_APP,
        "MAINAPP": OWNER_MAIN_APP,
        "MAIN_APP": OWNER_MAIN_APP,
        "OPTIONS": OWNER_OPTIONS_AUTO,
        "OPTION_AUTO": OWNER_OPTIONS_AUTO,
        "OPTIONS_AUTO": OWNER_OPTIONS_AUTO,
        "INTRADAY": OWNER_INTRADAY,
        "NONE": OWNER_NONE,
        "": OWNER_NONE,
    }
    return aliases.get(value, value if value in ALLOWED_OWNERS else OWNER_NONE)


def _normalize_mode(mode: Any) -> str:
    value = str(mode or "").strip().upper()
    if value == "REAL":
        return "LIVE"
    return value if value in {"PAPER", "LIVE", "SHADOW"} else value


def _default_ticker_name(owner: str, mode: str) -> str:
    if owner == OWNER_MAIN_APP:
        return "default"
    if owner == OWNER_OPTIONS_AUTO:
        return f"options_auto_{str(mode or 'paper').lower()}"
    if owner == OWNER_INTRADAY:
        return f"intraday_{str(mode or 'paper').lower()}"
    return ""


def _owner_label(owner: Any) -> str:
    owner = _normalize_owner(owner)
    return {
        OWNER_MAIN_APP: "Main App",
        OWNER_OPTIONS_AUTO: "Options Auto",
        OWNER_INTRADAY: "Intraday",
        OWNER_NONE: "No owner",
    }.get(owner, str(owner or "Unknown"))


def _is_positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except Exception:
        return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
