from __future__ import annotations

import os
from typing import Any

from .database import MarketCueDatabase
from .global_data import fetch_global_cues
from .kite_data import fetch_kite_index_data
from .models import empty_fii_dii
from .nse_fii_dii import parse_manual_entry, parse_nse_fii_dii_csv
from .report_generator import generate_report
from .scoring import score_market_cues
from .utils import safe_float
from .validator import validate_market_data


_DEFAULT_SERVICE: "MarketCueService | None" = None


class MarketCueService:
    def __init__(self, kite_client_provider=None, db: MarketCueDatabase | None = None):
        self.kite_client_provider = kite_client_provider or (lambda: None)
        self.db = db or MarketCueDatabase()
        self.last_raw_data: dict[str, Any] | None = None
        self.last_analysis: dict[str, Any] | None = None

    def fetch(self) -> dict[str, Any]:
        kite_client = self.kite_client_provider()
        flow = empty_fii_dii("manual_upload", "FAILED")
        flow["warnings"].append("Upload NSE FII/DII CSV to include institutional flow.")
        raw = {
            "indian_market": fetch_kite_index_data(kite_client),
            "global_market": fetch_global_cues(self.db),
            "institutional_flow": flow,
        }
        raw["source_status"] = self.source_status(raw)
        self.last_raw_data = raw
        return raw

    def upload_fii_dii(self, file_path: str, scope_hint: str = "") -> dict[str, Any]:
        file_name = os.path.basename(file_path or "")
        if not file_name.lower().endswith(".csv"):
            parsed = {"status": "FAILED", "warnings": ["Only CSV upload is supported in Version 1."], "source_file_name": file_name}
            self.db.save_uploaded_file(file_name, parsed, parsed["warnings"][0])
            return parsed
        if os.path.getsize(file_path) > 2_000_000:
            parsed = {"status": "FAILED", "warnings": ["CSV file is too large."], "source_file_name": file_name}
            self.db.save_uploaded_file(file_name, parsed, parsed["warnings"][0])
            return parsed
        try:
            with open(file_path, "r", encoding="utf-8-sig", errors="replace") as handle:
                csv_text = handle.read()
            parsed = parse_nse_fii_dii_csv(csv_text, fetch_mode="manual_upload", file_name=file_name)
            hinted_scope = _scope_from_hint(scope_hint)
            if hinted_scope:
                parsed["scope"] = hinted_scope
            self.db.save_uploaded_file(file_name, parsed)
            return parsed
        except Exception as exc:
            parsed = {"status": "FAILED", "warnings": [str(exc)], "source_file_name": file_name}
            self.db.save_uploaded_file(file_name, parsed, str(exc))
            return parsed

    def analyze(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        raw = payload.get("raw_data") or self.last_raw_data or self.fetch()
        raw = self.apply_payload_updates(raw, payload)
        overrides = self.collect_manual_overrides(payload, raw)
        raw = self.apply_manual_overrides(raw, overrides)
        validation = validate_market_data(raw, overrides)
        scoring = score_market_cues(raw, validation)
        report = generate_report(raw, validation, scoring)
        result = {
            "raw_data": raw,
            "validated_data": validation,
            "scoring": scoring,
            "report": report,
            "report_text": report["report_text"],
            "manual_overrides": overrides,
            "source_logs": self.source_logs(raw),
            "source_status": self.source_status(raw),
        }
        self.last_raw_data = raw
        self.last_analysis = result
        return result

    def save(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        if payload.get("analysis"):
            analysis = payload["analysis"] or {}
            result = self.analyze({
                "raw_data": analysis.get("raw_data"),
                "manual_overrides": analysis.get("manual_overrides", []),
            })
        elif self.last_analysis:
            result = self.last_analysis
        else:
            raise ValueError("Analyze market cues before saving a report.")
        report_id = self.db.save_report(result)
        result["report_id"] = report_id
        return {"report_id": report_id, "saved": True, "summary": self.bias_json(result)}

    def history(self) -> list[dict[str, Any]]:
        return self.db.list_reports()

    def report(self, report_id: int) -> dict[str, Any]:
        report = self.db.get_report(report_id)
        if not report:
            raise ValueError("Market cue report not found.")
        return report

    def latest_bias(self) -> dict[str, Any]:
        if self.last_analysis:
            return self.bias_json(self.last_analysis)
        latest = self.db.latest_report()
        if latest:
            scoring = latest.get("scoring") or {}
            raw = latest.get("raw_data") or {}
            return {
                "bias": latest.get("bias"),
                "score": latest.get("final_score"),
                "confidence": latest.get("confidence"),
                "risk_level": latest.get("risk_level"),
                "data_reliability": latest.get("data_reliability"),
                "nifty_plan": scoring.get("nifty_zones", {}),
                "banknifty_plan": scoring.get("banknifty_zones", {}),
                "institutional_flow": raw.get("institutional_flow", {}),
                "source_status": self.source_status(raw),
            }
        return {
            "status": "NO_REPORT",
            "bias": None,
            "score": None,
            "confidence": None,
            "risk_level": None,
            "data_reliability": "Unavailable",
            "nifty_plan": {},
            "banknifty_plan": {},
            "institutional_flow": {},
            "source_status": {
                "zerodha": "NOT_CHECKED",
                "yfinance": "NOT_CHECKED",
                "nse_fii_dii": "NOT_CHECKED",
                "warnings": ["No market cue report has been generated yet."],
            },
        }

    def bias_json(self, result: dict[str, Any]) -> dict[str, Any]:
        scoring = result.get("scoring") or {}
        raw = result.get("raw_data") or {}
        flow = raw.get("institutional_flow") or {}
        return {
            "bias": scoring.get("bias"),
            "score": scoring.get("final_score"),
            "confidence": scoring.get("confidence"),
            "risk_level": scoring.get("risk_level"),
            "data_reliability": (result.get("validated_data") or {}).get("data_reliability"),
            "nifty_plan": scoring.get("nifty_zones", {}),
            "banknifty_plan": scoring.get("banknifty_zones", {}),
            "institutional_flow": {
                "fii_net": flow.get("fii_net"),
                "dii_net": flow.get("dii_net"),
                "data_date": flow.get("data_date"),
                "source": flow.get("source"),
                "fetch_mode": flow.get("fetch_mode"),
                "scope": flow.get("scope"),
                "units": flow.get("units"),
            },
            "source_status": result.get("source_status") or self.source_status(raw),
        }

    def apply_payload_updates(self, raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        updated = {
            "indian_market": dict(raw.get("indian_market") or {}),
            "global_market": dict(raw.get("global_market") or {}),
            "institutional_flow": dict(raw.get("institutional_flow") or {}),
        }
        if payload.get("institutional_flow"):
            updated["institutional_flow"] = dict(payload["institutional_flow"])
        manual_flow = payload.get("manual_fii_dii") or {}
        if manual_flow:
            updated["institutional_flow"] = parse_manual_entry(
                manual_flow.get("fii_net"),
                manual_flow.get("dii_net"),
                manual_flow.get("data_date"),
                manual_flow.get("reason", ""),
            )
        return updated

    def collect_manual_overrides(self, payload: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
        overrides: list[dict[str, Any]] = []
        for item in payload.get("manual_overrides") or []:
            field = str(item.get("field_name") or "").strip()
            if not field:
                continue
            matched, original_value = self.lookup_override_field(raw, field)
            overrides.append({
                "field_name": field,
                "original_value": original_value,
                "override_value": item.get("override_value"),
                "reason": str(item.get("reason") or "").strip(),
                "applied": matched,
            })
        return overrides

    def apply_manual_overrides(self, raw: dict[str, Any], overrides: list[dict[str, Any]]) -> dict[str, Any]:
        for override in overrides:
            if override.get("applied") is False:
                continue
            field = override["field_name"]
            value = override.get("override_value")
            if field in {"fii_net", "dii_net", "data_date"}:
                raw.setdefault("institutional_flow", {})[field] = safe_float(value) if field.endswith("_net") else value
                raw["institutional_flow"]["fetch_mode"] = "manual_override"
                continue
            cue_name, cue_field = _split_override_field(field)
            for bucket in ("indian_market", "global_market"):
                for name, row in (raw.get(bucket) or {}).items():
                    target_field = cue_field or field
                    if (not cue_name or cue_name.lower() in {str(name).lower(), str(row.get("name", "")).lower(), str(row.get("symbol", "")).lower()}) and target_field in row:
                        row[target_field] = safe_float(value) if target_field in {"value", "previous_close", "percent_change"} else value
                        row["status"] = "OK"
                        row["manual_override"] = True
        return raw

    def lookup_field(self, raw: dict[str, Any], field: str) -> Any:
        _matched, value = self.lookup_override_field(raw, field)
        return value

    def lookup_override_field(self, raw: dict[str, Any], field: str) -> tuple[bool, Any]:
        if field in (raw.get("institutional_flow") or {}):
            return True, raw["institutional_flow"].get(field)
        cue_name, cue_field = _split_override_field(field)
        for bucket in ("indian_market", "global_market"):
            for name, row in (raw.get(bucket) or {}).items():
                target_field = cue_field or field
                if (not cue_name or cue_name.lower() in {str(name).lower(), str(row.get("name", "")).lower(), str(row.get("symbol", "")).lower()}) and target_field in row:
                    return True, row.get(target_field)
        return False, None

    def source_logs(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        logs: list[dict[str, Any]] = []
        for bucket in ("indian_market", "global_market"):
            logs.extend((raw.get(bucket) or {}).values())
        flow = raw.get("institutional_flow") or {}
        logs.append({
            "source": flow.get("source"),
            "symbol": "FII/DII",
            "status": flow.get("status"),
            "value": flow.get("fii_net"),
            "percent_change": None,
            "timestamp": flow.get("data_date"),
            "warning": "; ".join(flow.get("warnings") or []),
        })
        return logs

    def source_status(self, raw: dict[str, Any]) -> dict[str, Any]:
        indian = raw.get("indian_market") or {}
        global_data = raw.get("global_market") or {}
        flow = raw.get("institutional_flow") or {}
        return {
            "zerodha": _rollup_status(indian.values()),
            "yfinance": _rollup_status(global_data.values()),
            "nse_fii_dii": flow.get("status", "FAILED"),
            "warnings": [row.get("warning") for row in list(indian.values()) + list(global_data.values()) if row.get("warning")] + list(flow.get("warnings") or []),
        }


def _rollup_status(rows) -> str:
    statuses = [str((row or {}).get("status") or "FAILED").upper() for row in rows]
    if statuses and all(status == "OK" for status in statuses):
        return "OK"
    if any(status in {"OK", "PARTIAL", "STALE"} for status in statuses):
        return "PARTIAL"
    return "FAILED"


def _split_override_field(field: str) -> tuple[str, str]:
    if "." not in str(field):
        return "", str(field).strip()
    name, attr = str(field).rsplit(".", 1)
    return name.strip(), attr.strip()


def _scope_from_hint(scope_hint: str) -> str:
    text = str(scope_hint or "").strip().lower()
    if "combined" in text or "bse" in text or "msei" in text:
        return "NSE+BSE+MSEI"
    if "nse" in text:
        return "NSE only"
    return ""


def get_market_cue_bias() -> dict[str, Any]:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = MarketCueService()
    return _DEFAULT_SERVICE.latest_bias()
