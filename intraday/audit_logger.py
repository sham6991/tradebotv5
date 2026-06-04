from __future__ import annotations

from datetime import datetime


class AuditLogger:
    def __init__(self, database, session_id: str):
        self.database = database
        self.session_id = session_id

    def log(self, level: str, module: str, event: str, details: dict | None = None) -> None:
        self.database.save_audit({
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "module": module,
            "event": event,
            "details": details or {},
        })
