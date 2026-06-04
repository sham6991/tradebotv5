from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from market_cue.nse_fii_dii import parse_nse_fii_dii_csv


def parse_intraday_fii_dii_csv_file(file_path: str, scope_hint: str = "") -> dict[str, Any]:
    file_name = os.path.basename(file_path or "")
    if not file_name.lower().endswith(".csv"):
        return _failed(file_name, "Only FII/DII CSV upload is supported.")
    if not os.path.isfile(file_path):
        return _failed(file_name, "FII/DII CSV file was not found.")
    if os.path.getsize(file_path) > 2_000_000:
        return _failed(file_name, "FII/DII CSV file is too large.")
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as handle:
        return parse_intraday_fii_dii_csv_text(handle.read(), file_name=file_name, scope_hint=scope_hint)


def parse_intraday_fii_dii_csv_text(csv_text: str, file_name: str = "fii_dii.csv", scope_hint: str = "") -> dict[str, Any]:
    parsed = parse_nse_fii_dii_csv(
        csv_text,
        source_name="User uploaded NSE FII/DII CSV",
        fetch_mode="manual_upload_required",
        file_name=file_name,
    )
    if scope_hint:
        parsed["scope"] = str(scope_hint).strip()
    parsed["uploaded_at"] = datetime.now().isoformat(timespec="seconds")
    parsed["required_for_intraday_live_start"] = True
    parsed["valid_for_session_start"] = parsed.get("status") == "OK"
    if not parsed["valid_for_session_start"]:
        parsed.setdefault("warnings", []).append("Upload must contain FII/FPI net, DII net, and data date before PAPER or REAL session start.")
    return parsed


def upload_status(parsed: dict[str, Any] | None) -> dict[str, Any]:
    if not parsed:
        return {
            "uploaded": False,
            "valid": False,
            "status": "MISSING",
            "message": "Upload NSE FII/DII CSV before starting PAPER or REAL intraday session.",
        }
    return {
        "uploaded": True,
        "valid": bool(parsed.get("valid_for_session_start")),
        "status": parsed.get("status", "UNKNOWN"),
        "message": "FII/DII CSV ready." if parsed.get("valid_for_session_start") else "; ".join(parsed.get("warnings") or []),
        "data_date": parsed.get("data_date"),
        "fii_net": parsed.get("fii_net"),
        "dii_net": parsed.get("dii_net"),
        "source_file_name": parsed.get("source_file_name"),
        "uploaded_at": parsed.get("uploaded_at"),
        "scope": parsed.get("scope"),
        "warnings": list(parsed.get("warnings") or []),
    }


def _failed(file_name: str, warning: str) -> dict[str, Any]:
    return {
        "source": "User uploaded NSE FII/DII CSV",
        "fetch_mode": "manual_upload_required",
        "status": "FAILED",
        "source_file_name": file_name,
        "warnings": [warning],
        "valid_for_session_start": False,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "required_for_intraday_live_start": True,
    }
