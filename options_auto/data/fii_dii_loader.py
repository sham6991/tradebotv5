from __future__ import annotations

import csv
import io
from typing import Any

from options_auto.core.clock import iso_now


def parse_fii_dii_csv_text(text: str, file_name: str = "fii_dii.csv") -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(text or "")))
    if not rows:
        return _with_score({"status": "FAILED", "file_name": file_name, "error": "CSV is empty.", "warnings": ["CSV is empty."]})
    normalized = [[str(cell or "").strip() for cell in row] for row in rows]
    joined_rows = [" ".join(row).lower() for row in normalized]
    result = {
        "status": "OK",
        "file_name": file_name,
        "fii_net": None,
        "dii_net": None,
        "cash_activity": None,
        "derivatives_activity": None,
        "total_turnover": None,
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
        if "turnover" in joined and numeric_values:
            result["total_turnover"] = numeric_values[-1]
    if result["fii_net"] is None:
        result["warnings"].append("FII/FPI net value not found.")
    if result["dii_net"] is None:
        result["warnings"].append("DII net value not found.")
    if result["fii_net"] is None and result["dii_net"] is None:
        result["status"] = "NEUTRAL_MISSING_VALUES"
        result["warnings"].append("FII and DII values are missing; score treated as neutral.")
        return _with_score(result)
    if result["warnings"]:
        result["status"] = "PARTIAL"
    return _with_score(result)


def score_fii_dii(fii_net: Any = None, dii_net: Any = None, total_turnover: Any = None) -> dict[str, Any]:
    fii = _coerce_number(fii_net)
    dii = _coerce_number(dii_net)
    turnover = _coerce_number(total_turnover)
    values = [value for value in (fii, dii) if value is not None]
    if not values:
        return {"combined_net": 0.0, "fii_dii_pct": None, "fii_dii_score": 0.0}
    combined = sum(values)
    if turnover and turnover > 0:
        fii_dii_pct = combined / turnover * 100.0
        return {
            "combined_net": round(combined, 2),
            "fii_dii_pct": round(fii_dii_pct, 4),
            "fii_dii_score": _clamp(fii_dii_pct * 10.0),
        }
    return {"combined_net": round(combined, 2), "fii_dii_pct": None, "fii_dii_score": _threshold_score(combined)}


def fii_dii_status_from_upload(parsed: dict[str, Any], phase: str = "PREMARKET") -> dict[str, Any]:
    parsed = dict(parsed or {})
    score = score_fii_dii(parsed.get("fii_net"), parsed.get("dii_net"), parsed.get("total_turnover"))
    return {
        "status": parsed.get("status") or "FAILED",
        "file_name": parsed.get("file_name") or "",
        "fii_net": parsed.get("fii_net"),
        "dii_net": parsed.get("dii_net"),
        "cash_activity": parsed.get("cash_activity"),
        "derivatives_activity": parsed.get("derivatives_activity"),
        "total_turnover": parsed.get("total_turnover"),
        "combined_net": score["combined_net"],
        "fii_dii_pct": score["fii_dii_pct"],
        "fii_dii_score": score["fii_dii_score"],
        "score": score["fii_dii_score"],
        "warnings": list(parsed.get("warnings") or []),
        "uploaded_at": parsed.get("uploaded_at") or iso_now(),
        "used_for_phase": str(phase or "PREMARKET").upper(),
    }


def _number(value: str) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _with_score(result: dict[str, Any]) -> dict[str, Any]:
    score = score_fii_dii(result.get("fii_net"), result.get("dii_net"), result.get("total_turnover"))
    result.update(score)
    result["score"] = score["fii_dii_score"]
    result["fii_dii_score"] = score["fii_dii_score"]
    return result


def _coerce_number(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _threshold_score(combined: float) -> float:
    if combined >= 3000:
        return 100.0
    if combined >= 1500:
        return 60.0
    if combined >= 500:
        return 30.0
    if combined > -500:
        return 0.0
    if combined > -1500:
        return -30.0
    if combined > -3000:
        return -60.0
    return -100.0


def _clamp(value: float, low: float = -100.0, high: float = 100.0) -> float:
    return round(max(low, min(high, float(value))), 2)
