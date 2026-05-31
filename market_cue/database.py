from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from .utils import DB_PATH, ensure_market_cue_dir, from_json, iso_now, parse_datetime, to_json


SCHEMA = """
CREATE TABLE IF NOT EXISTS market_cue_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    raw_data_json TEXT,
    validated_data_json TEXT,
    scoring_breakdown_json TEXT,
    final_score REAL,
    bias TEXT,
    confidence INTEGER,
    risk_level TEXT,
    data_reliability TEXT,
    nifty_ltp REAL,
    nifty_previous_close REAL,
    banknifty_ltp REAL,
    banknifty_previous_close REAL,
    fii_value REAL,
    dii_value REAL,
    fii_dii_data_date TEXT,
    fii_dii_source TEXT,
    fii_dii_fetch_mode TEXT,
    fii_dii_scope TEXT,
    report_text TEXT
);

CREATE TABLE IF NOT EXISTS market_cue_source_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    source_name TEXT,
    symbol TEXT,
    status TEXT,
    value REAL,
    percent_change REAL,
    timestamp TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS market_cue_manual_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    field_name TEXT,
    original_value TEXT,
    override_value TEXT,
    reason TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS market_cue_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    value REAL,
    percent_change REAL,
    timestamp TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    is_stale INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS market_cue_uploaded_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    file_name TEXT,
    file_type TEXT,
    source_type TEXT,
    parsed_status TEXT,
    parsed_json TEXT,
    error_message TEXT,
    uploaded_at TEXT
);
"""

MAX_REPORT_HISTORY = 60


class MarketCueDatabase:
    def __init__(self, path: str = DB_PATH):
        ensure_market_cue_dir()
        self.path = path
        self.init_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def cache_value(self, source_name: str, symbol: str, row: dict[str, Any], is_stale: bool = False) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO market_cue_cache
                    (source_name, symbol, value, percent_change, timestamp, raw_json, created_at, is_stale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_name,
                    symbol,
                    row.get("value"),
                    row.get("percent_change"),
                    row.get("timestamp"),
                    to_json(row),
                    iso_now(),
                    1 if is_stale else 0,
                ),
            )

    def latest_cache(self, source_name: str, symbol: str, max_age_hours: int = 36) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM market_cue_cache
                WHERE source_name = ? AND symbol = ?
                ORDER BY id DESC LIMIT 1
                """,
                (source_name, symbol),
            ).fetchone()
        if not row:
            return None
        created_at = parse_datetime(row["created_at"])
        if created_at and datetime.now() - created_at > timedelta(hours=max_age_hours):
            return None
        data = from_json(row["raw_json"], {}) or {}
        data["stale"] = True
        data["status"] = "STALE"
        if created_at:
            age_minutes = max(0, int((datetime.now() - created_at).total_seconds() // 60))
            data["cache_age_minutes"] = age_minutes
        data["warning"] = "Fresh fetch failed; stale cached value is shown."
        return data

    def save_uploaded_file(self, file_name: str, parsed: dict[str, Any], error: str = "", report_id: int | None = None) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO market_cue_uploaded_files
                    (report_id, file_name, file_type, source_type, parsed_status, parsed_json, error_message, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    file_name,
                    "csv",
                    "NSE FII/DII",
                    parsed.get("status", "FAILED"),
                    to_json(parsed),
                    error,
                    iso_now(),
                ),
            )
            return int(cursor.lastrowid)

    def save_report(self, result: dict[str, Any]) -> int:
        raw = result.get("raw_data", {})
        validated = result.get("validated_data", {})
        scoring = result.get("scoring", {})
        fii = raw.get("institutional_flow") or {}
        indian = raw.get("indian_market") or {}
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO market_cue_reports
                    (created_at, raw_data_json, validated_data_json, scoring_breakdown_json, final_score,
                     bias, confidence, risk_level, data_reliability, nifty_ltp, nifty_previous_close,
                     banknifty_ltp, banknifty_previous_close, fii_value, dii_value, fii_dii_data_date,
                     fii_dii_source, fii_dii_fetch_mode, fii_dii_scope, report_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    iso_now(),
                    to_json(raw),
                    to_json(validated),
                    to_json(scoring),
                    scoring.get("final_score"),
                    scoring.get("bias"),
                    scoring.get("confidence"),
                    scoring.get("risk_level"),
                    validated.get("data_reliability"),
                    (indian.get("NIFTY 50") or {}).get("value"),
                    (indian.get("NIFTY 50") or {}).get("previous_close"),
                    (indian.get("BANK NIFTY") or {}).get("value"),
                    (indian.get("BANK NIFTY") or {}).get("previous_close"),
                    fii.get("fii_net"),
                    fii.get("dii_net"),
                    fii.get("data_date"),
                    fii.get("source"),
                    fii.get("fetch_mode"),
                    fii.get("scope"),
                    result.get("report_text", ""),
                ),
            )
            report_id = int(cursor.lastrowid)
            for source in result.get("source_logs", []):
                conn.execute(
                    """
                    INSERT INTO market_cue_source_logs
                        (report_id, source_name, symbol, status, value, percent_change, timestamp, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        source.get("source"),
                        source.get("symbol") or source.get("name"),
                        source.get("status"),
                        source.get("value"),
                        source.get("percent_change"),
                        source.get("timestamp"),
                        source.get("warning", ""),
                    ),
                )
            for override in result.get("manual_overrides", []):
                conn.execute(
                    """
                    INSERT INTO market_cue_manual_overrides
                        (report_id, field_name, original_value, override_value, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        override.get("field_name"),
                        str(override.get("original_value", "")),
                        str(override.get("override_value", "")),
                        override.get("reason", ""),
                        iso_now(),
                    ),
                )
            self.prune_report_history(conn, MAX_REPORT_HISTORY)
            return report_id

    def prune_report_history(self, conn: sqlite3.Connection, limit: int = MAX_REPORT_HISTORY) -> None:
        rows = conn.execute(
            """
            SELECT id
            FROM market_cue_reports
            ORDER BY id DESC
            LIMIT -1 OFFSET ?
            """,
            (int(limit),),
        ).fetchall()
        old_ids = [int(row["id"]) for row in rows]
        if not old_ids:
            return
        placeholders = ",".join("?" for _ in old_ids)
        conn.execute(f"DELETE FROM market_cue_source_logs WHERE report_id IN ({placeholders})", old_ids)
        conn.execute(f"DELETE FROM market_cue_manual_overrides WHERE report_id IN ({placeholders})", old_ids)
        conn.execute(f"UPDATE market_cue_uploaded_files SET report_id = NULL WHERE report_id IN ({placeholders})", old_ids)
        conn.execute(f"DELETE FROM market_cue_reports WHERE id IN ({placeholders})", old_ids)

    def list_reports(self, limit: int = MAX_REPORT_HISTORY) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, bias, final_score, confidence, risk_level, data_reliability,
                       nifty_ltp, banknifty_ltp, fii_value, dii_value
                FROM market_cue_reports
                ORDER BY id DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_uploaded_files(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, report_id, file_name, source_type, parsed_status, parsed_json, error_message, uploaded_at
                FROM market_cue_uploaded_files
                ORDER BY id DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            parsed = from_json(item.pop("parsed_json", None), {}) or {}
            item.update({
                "fii_value": parsed.get("fii_net"),
                "dii_value": parsed.get("dii_net"),
                "data_date": parsed.get("data_date"),
                "scope": parsed.get("scope"),
                "fetch_mode": parsed.get("fetch_mode"),
                "warnings": "; ".join(parsed.get("warnings") or []),
            })
            result.append(item)
        return result

    def get_report(self, report_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM market_cue_reports WHERE id = ?", (int(report_id),)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["raw_data"] = from_json(data.get("raw_data_json"), {})
        data["validated_data"] = from_json(data.get("validated_data_json"), {})
        data["scoring"] = from_json(data.get("scoring_breakdown_json"), {})
        return data

    def latest_report(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM market_cue_reports ORDER BY id DESC LIMIT 1").fetchone()
        return self.get_report(row["id"]) if row else None
