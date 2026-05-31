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
