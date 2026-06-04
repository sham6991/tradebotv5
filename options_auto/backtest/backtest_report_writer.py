from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any


class BacktestReportWriter:
    def __init__(self, base_result_folder: str):
        self.base_result_folder = base_result_folder

    def write(self, session_id: str, payload: dict[str, Any]) -> dict[str, str]:
        day = datetime.now().strftime("%Y-%m-%d")
        folder = os.path.join(self.base_result_folder, "options_auto", "backtests", day, session_id)
        os.makedirs(folder, exist_ok=True)
        json_path = os.path.join(folder, "audit.json")
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
        decisions_path = os.path.join(folder, "decisions.csv")
        decisions = payload.get("decisions") or []
        with open(decisions_path, "w", newline="", encoding="utf-8") as handle:
            if decisions:
                writer = csv.DictWriter(handle, fieldnames=sorted({key for row in decisions for key in row}))
                writer.writeheader()
                writer.writerows(decisions)
            else:
                handle.write("decision\n")
        return {"folder": folder, "audit_json": json_path, "decisions_csv": decisions_path}

