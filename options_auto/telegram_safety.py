from __future__ import annotations

import time
from typing import Any


SAFE_COMMANDS = {"status", "pnl", "active_trades", "stop_new_entries", "safe_mode", "refresh_balance", "engine_status"}
CONFIRM_COMMANDS = {"emergency_exit"}
DISALLOWED_COMMANDS = {"start_real_auto_trade", "increase_quantity", "disable_sl", "disable_governor", "change_max_loss"}


class TelegramSafety:
    def __init__(self) -> None:
        self.command_log: list[dict[str, Any]] = []
        self.last_command_times: dict[str, float] = {}
        self.recent_fingerprints: dict[str, float] = {}

    def validate(
        self,
        command: str,
        user_id: str,
        settings: dict[str, Any] | None = None,
        confirmed: bool = False,
        command_id: str = "",
        now_epoch: float | None = None,
        position_snapshot: Any = None,
    ) -> dict[str, Any]:
        settings = dict(settings or {})
        now_epoch = time.time() if now_epoch is None else float(now_epoch)
        command = str(command or "").strip().lower()
        user_id = str(user_id or "")
        allowed_users = {str(item) for item in settings.get("telegram_allowed_user_ids") or []}
        blockers = []
        if allowed_users and str(user_id) not in allowed_users:
            blockers.append("Telegram user is not whitelisted.")
        cooldown = int(settings.get("telegram_command_cooldown_seconds") or 0)
        last_time = self.last_command_times.get(user_id)
        if cooldown > 0 and last_time is not None and now_epoch - last_time < cooldown:
            blockers.append("Telegram command cooldown is active.")
        duplicate_window = int(settings.get("telegram_duplicate_window_seconds") or 0)
        fingerprint = command_id or f"{user_id}:{command}:{confirmed}"
        previous = self.recent_fingerprints.get(fingerprint)
        if duplicate_window > 0 and previous is not None and now_epoch - previous < duplicate_window:
            blockers.append("Duplicate Telegram command was ignored.")
        if command in DISALLOWED_COMMANDS:
            blockers.append("Telegram command is disallowed by safety policy.")
        elif command in CONFIRM_COMMANDS and not confirmed:
            blockers.append("Dangerous Telegram command requires confirmation.")
        elif command in CONFIRM_COMMANDS and settings.get("require_telegram_position_preview", True) and position_snapshot is None:
            blockers.append("Dangerous Telegram command requires live position preview.")
        elif command not in SAFE_COMMANDS and command not in CONFIRM_COMMANDS:
            blockers.append("Unknown Telegram command.")
        result = {
            "allowed": not blockers,
            "command": command,
            "source": "TELEGRAM",
            "blockers": blockers,
            "logged": True,
            "position_preview_included": position_snapshot is not None,
        }
        self.command_log.append({
            "timestamp_epoch": now_epoch,
            "user_id": user_id,
            "command": command,
            "allowed": result["allowed"],
            "blockers": blockers,
            "source": "TELEGRAM",
        })
        self.last_command_times[user_id] = now_epoch
        self.recent_fingerprints[fingerprint] = now_epoch
        result["command_log"] = self.command_log[-100:]
        return result
