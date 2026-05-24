import json
import os
import threading
from datetime import datetime

import pandas as pd

from trailing_stop import trailing_settings


class SessionPersistenceMixin:
    def _save_open_position(self):
        if not self.position_state_path or not self.open_position:
            return
        position = dict(self.open_position)
        signal = dict(position.get("signal", {}))
        signal.pop("option", None)
        position["signal"] = signal
        with open(self.position_state_path, "w", encoding="utf-8") as handle:
            json.dump(position, handle, default=str, indent=2)
        if self.store:
            self.store.save_state(f"{self.mode.lower()}_open_position", position)

    def _load_open_position(self):
        def hydrate(position):
            option_index = int(position["option_index"])
            position["signal"]["option"] = self.options[option_index]
            position.setdefault("initial_target_price", position.get("target", ""))
            position.setdefault("initial_stoploss_price", position.get("stoploss", ""))
            position.setdefault("current_sl_price", position.get("stoploss", ""))
            config = trailing_settings(self.settings)
            position.setdefault("trailing_sl_enabled", config["enabled"])
            position.setdefault("trailing_start_points", config["start_points"])
            position.setdefault("trailing_step_points", config["step_points"])
            position.setdefault("trailing_lock_points", config["lock_points"])
            position.setdefault("last_trailing_level", 0)
            position.setdefault("trailing_modification_count", 0)
            position.setdefault("trailing_modifications", [])
            return position

        if not self.position_state_path or not os.path.exists(self.position_state_path):
            position = self.store.load_state(f"{self.mode.lower()}_open_position") if self.store else None
            if not position:
                return
            try:
                self.open_position = hydrate(position)
            except Exception:
                self.open_position = None
            return
        try:
            with open(self.position_state_path, "r", encoding="utf-8") as handle:
                position = json.load(handle)
            self.open_position = hydrate(position)
        except Exception:
            self.open_position = None

    def _clear_open_position(self):
        if self.position_state_path and os.path.exists(self.position_state_path):
            os.remove(self.position_state_path)
        if self.store:
            self.store.clear_state(f"{self.mode.lower()}_open_position")

    def _save_pending_entry(self):
        if not self.pending_entry:
            return
        pending = dict(self.pending_entry)
        signal = dict(pending.get("signal", {}))
        signal.pop("option", None)
        pending["signal"] = signal
        pending.pop("timer", None)
        if self.pending_state_path:
            with open(self.pending_state_path, "w", encoding="utf-8") as handle:
                json.dump(pending, handle, default=str, indent=2)
        if self.store:
            self.store.save_state(f"{self.mode.lower()}_pending_entry", pending)

    def _load_pending_entry(self):
        data = None
        if self.pending_state_path and os.path.exists(self.pending_state_path):
            try:
                with open(self.pending_state_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                data = None
        if data is None and self.store:
            data = self.store.load_state(f"{self.mode.lower()}_pending_entry")
        if not data:
            return
        try:
            option_index = int(data["option_index"])
            data["signal"]["option"] = self.options[option_index]
            data["placed_at"] = pd.to_datetime(data["placed_at"], errors="coerce").to_pydatetime()
            self.pending_entry = data
            elapsed = (datetime.now() - data["placed_at"]).total_seconds()
            remaining = max(0, self.pending_entry_timeout_seconds - elapsed)
            timer = threading.Timer(remaining, self._expire_pending_entry_order)
            timer.daemon = True
            self.pending_entry["timer"] = timer
            timer.start()
        except Exception as exc:
            self.pending_entry = None
            self._log_event("ERROR", "Could not restore pending entry", {"error": str(exc)})

    def _clear_pending_entry(self):
        if self.pending_state_path and os.path.exists(self.pending_state_path):
            os.remove(self.pending_state_path)
        if self.store:
            self.store.clear_state(f"{self.mode.lower()}_pending_entry")

    def _save_kill_switch_state(self):
        state = {
            "active": self.risk_guard.kill_switch_active,
            "reason": self.risk_guard.kill_switch_reason,
            "blocked_reason": self.risk_guard.blocked_reason,
            "session_id": self.session_id,
        }
        if self.kill_switch_state_path:
            with open(self.kill_switch_state_path, "w", encoding="utf-8") as handle:
                json.dump(state, handle, default=str, indent=2)
        if self.store:
            self.store.save_state(f"{self.session_id}_kill_switch", state)

    def _load_kill_switch_state(self):
        data = None
        if self.kill_switch_state_path and os.path.exists(self.kill_switch_state_path):
            try:
                with open(self.kill_switch_state_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                data = None
        if data is None and self.store:
            data = self.store.load_state(f"{self.session_id}_kill_switch")
        if not data:
            return
        self.risk_guard.restore_kill_switch(
            active=data.get("active", False),
            reason=data.get("reason", ""),
        )
        self._sync_risk_state_from_guard()
