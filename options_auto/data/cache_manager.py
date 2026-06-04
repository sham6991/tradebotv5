from __future__ import annotations

import json
import os
import time
from typing import Any


class JsonCacheManager:
    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def path_for(self, key: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(key))
        return os.path.join(self.folder, f"{safe}.json")

    def get(self, key: str, max_age_seconds: float | None = None) -> dict[str, Any] | None:
        path = self.path_for(key)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if max_age_seconds is not None:
            age = time.time() - float(payload.get("_cached_epoch") or 0)
            if age > float(max_age_seconds):
                return None
        return payload.get("value")

    def set(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        payload = {"_cached_epoch": time.time(), "value": value}
        with open(self.path_for(key), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
        return value

