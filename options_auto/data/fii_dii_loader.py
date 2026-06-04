from __future__ import annotations

import csv
import io
from typing import Any


def parse_fii_dii_csv_text(text: str, file_name: str = "fii_dii.csv") -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(text or "")))
    if not rows:
        return {"status": "FAILED", "file_name": file_name, "error": "CSV is empty."}
    normalized = [[str(cell or "").strip() for cell in row] for row in rows]
    joined_rows = [" ".join(row).lower() for row in normalized]
    result = {
        "status": "OK",
        "file_name": file_name,
        "fii_net": None,
        "dii_net": None,
        "cash_activity": None,
        "derivatives_activity": None,
        "warnings": [],
    }
    for row, joined in zip(normalized, joined_rows):
        numeric_values = [_number(cell) for cell in row]
        numeric_values = [value for value in numeric_values if value is not None]
        if not numeric_values:
            continue
        net_value = numeric_values[-1]
        if "fii" in joined or "fpi" in joined:
            result["fii_net"] = net_value
        elif "dii" in joined:
            result["dii_net"] = net_value
        elif "cash" in joined:
            result["cash_activity"] = net_value
        elif "derivative" in joined:
            result["derivatives_activity"] = net_value
    if result["fii_net"] is None:
        result["warnings"].append("FII/FPI net value not found.")
    if result["dii_net"] is None:
        result["warnings"].append("DII net value not found.")
    if result["warnings"]:
        result["status"] = "PARTIAL"
    return result


def _number(value: str) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None

