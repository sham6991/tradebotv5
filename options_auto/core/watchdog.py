from __future__ import annotations

from typing import Any


class WatchdogService:
    def evaluate(self, status: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        blockers = []
        warnings = []
        latency_log = dict(status.get("latency_log") or {})
        if status.get("ui_alive") is False:
            blockers.append("UI heartbeat missing.")
        if status.get("data_feed_alive") is False:
            blockers.append("Data feed heartbeat missing.")
        if status.get("kite_connected") is False and status.get("mode") == "REAL":
            blockers.append("Real mode Kite connection is down.")
        if status.get("order_monitor_alive") is False and status.get("mode") == "REAL":
            blockers.append("Real order monitor heartbeat missing.")
        if status.get("oco_monitor_alive") is False and status.get("active_position"):
            blockers.append("OCO monitor heartbeat missing while a position is active.")
        if status.get("position_protected") is False and status.get("active_position"):
            blockers.append("Active position is not protected.")
        stale = float(status.get("last_update_age_seconds") or 0)
        if stale > float(settings.get("quote_stale_seconds") or 3):
            blockers.append("Last update is stale.")
        memory_pct = float(status.get("memory_pct") or 0)
        if memory_pct >= 85:
            warnings.append("Memory pressure is high; slow analytics should pause.")
        cpu_pct = float(status.get("cpu_pct") or 0)
        if cpu_pct >= 90:
            warnings.append("CPU pressure is high; slow analytics should pause.")
        for name, value in latency_log.items():
            try:
                latency = float(value)
            except (TypeError, ValueError):
                continue
            if latency > float(settings.get("max_latency_warning_ms") or 1500):
                warnings.append(f"{name} latency is high.")
        if status.get("locked"):
            blockers.append("Engine is locked.")
            mode = "LOCKED"
        elif blockers:
            mode = "CRITICAL" if any("protected" in item.lower() or "oco" in item.lower() for item in blockers) else "DEGRADED"
        else:
            mode = "NORMAL"
        slow_tasks_paused = memory_pct >= 85 or cpu_pct >= 90 or bool(blockers)
        score = max(0.0, 100.0 - len(blockers) * 25.0 - len(warnings) * 8.0)
        return {
            "mode": mode,
            "new_entries_allowed": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "bot_health_score": score,
            "session_health_score": score,
            "daily_readiness_score": max(0.0, score - (10.0 if slow_tasks_paused else 0.0)),
            "slow_tasks_paused": slow_tasks_paused,
            "order_protection_must_continue": True,
            "latency_log": latency_log,
        }
