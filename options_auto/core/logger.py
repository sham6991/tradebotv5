from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.core.clock import iso_now


@dataclass
class OptionsAutoLogger:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def log(self, level: str, message: str, **context: Any) -> dict[str, Any]:
        row = {
            "timestamp": iso_now(),
            "level": str(level).upper(),
            "message": message,
            "context": context,
        }
        self.rows.append(row)
        return row

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.rows[-int(limit):]

