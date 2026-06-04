from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any


class InstrumentsCache:
    def __init__(self, cache_path: str):
        self.cache_path = cache_path

    def load(self, trading_day: date | None = None) -> list[dict[str, Any]]:
        trading_day = trading_day or date.today()
        if not os.path.exists(self.cache_path):
            return []
        with open(self.cache_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("trading_day") != trading_day.isoformat():
            return []
        return list(payload.get("instruments") or [])

    def save(self, instruments: list[dict[str, Any]], trading_day: date | None = None) -> dict[str, Any]:
        trading_day = trading_day or date.today()
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        payload = {
            "trading_day": trading_day.isoformat(),
            "cached_at": datetime.now().isoformat(timespec="seconds"),
            "instruments": list(instruments or []),
        }
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return payload

