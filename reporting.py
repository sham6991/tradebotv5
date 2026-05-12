import os
import queue
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime

import pandas as pd


@contextmanager
def sqlite_connection(path):
    conn = sqlite3.connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def timestamped_file(prefix, folder):
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(folder, f"{prefix}_{stamp}.xlsx")


TIME_COLUMNS = {
    "Datetime",
    "Signal Time",
    "Entry Time",
    "Exit Time",
    "entry_time",
    "exit_time",
    "started_at",
    "ended_at",
    "date",
}


def format_datetime_value(value):
    if value == "" or value is None:
        return ""
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return str(value)
    try:
        if getattr(parsed, "tzinfo", None) is not None:
            parsed = parsed.tz_convert(None)
    except (AttributeError, TypeError):
        pass
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def format_time_columns(df):
    df = df.copy()
    for col in TIME_COLUMNS:
        if col in df.columns:
            df[col] = df[col].apply(format_datetime_value)
    return df


def write_excel(path, rows_or_df):
    df = rows_or_df if isinstance(rows_or_df, pd.DataFrame) else pd.DataFrame(rows_or_df)
    df = format_time_columns(df)
    df.to_excel(path, index=False)


def append_rows_excel(path, rows):
    if not rows:
        return
    new_df = pd.DataFrame(rows)
    if os.path.exists(path):
        old_df = pd.read_excel(path)
        new_df = pd.concat([old_df, new_df], ignore_index=True)
    write_excel(path, new_df)


class BufferedExcelWriter:
    def __init__(self, flush_interval=1.0, max_batch_rows=100, append_func=None):
        self.flush_interval = max(0.05, float(flush_interval or 1.0))
        self.max_batch_rows = max(1, int(max_batch_rows or 100))
        self.append_func = append_func or append_rows_excel
        self.queue = queue.Queue()
        self.thread = None
        self.lock = threading.Lock()
        self.closed = False
        self.errors = []
        self.enqueued_rows = 0
        self.flushed_rows = 0

    def append(self, path, rows):
        if not path or not rows:
            return False
        with self.lock:
            if self.closed:
                self.append_func(path, rows)
                return True
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(
                    target=self._run,
                    name="tradebot_buffered_excel_writer",
                    daemon=True,
                )
                self.thread.start()
            self.enqueued_rows += len(rows)
        self.queue.put(("rows", path, list(rows), None))
        return True

    def flush(self, timeout=10):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                return True
        done = threading.Event()
        self.queue.put(("flush", "", [], done))
        return done.wait(timeout)

    def close(self, timeout=10):
        with self.lock:
            if self.closed:
                return True
            self.closed = True
            thread = self.thread
        if thread is None or not thread.is_alive():
            return True
        done = threading.Event()
        self.queue.put(("close", "", [], done))
        completed = done.wait(timeout)
        thread.join(timeout=1)
        return completed

    def _run(self):
        pending = {}
        pending_rows = 0
        last_flush = time.monotonic()
        while True:
            timeout = max(0.05, self.flush_interval - (time.monotonic() - last_flush))
            try:
                kind, path, rows, done = self.queue.get(timeout=timeout)
            except queue.Empty:
                pending_rows = self._flush_pending(pending)
                last_flush = time.monotonic()
                continue

            if kind == "rows":
                pending.setdefault(path, []).extend(rows)
                pending_rows += len(rows)
                if pending_rows >= self.max_batch_rows:
                    pending_rows = self._flush_pending(pending)
                    last_flush = time.monotonic()
                continue

            if kind == "flush":
                pending_rows = self._flush_pending(pending)
                last_flush = time.monotonic()
                if done:
                    done.set()
                continue

            if kind == "close":
                pending_rows = self._flush_pending(pending)
                if done:
                    done.set()
                break

    def _flush_pending(self, pending):
        if not pending:
            return 0
        for path, rows in list(pending.items()):
            if not rows:
                continue
            try:
                self.append_func(path, rows)
                self.flushed_rows += len(rows)
            except Exception as exc:
                self.errors.append({
                    "path": path,
                    "rows": len(rows),
                    "error": str(exc),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
        pending.clear()
        return 0


def ensure_risk_engine_schema(path):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with sqlite_connection(path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT,
                mode TEXT,
                strategy_name TEXT,
                strategy_version TEXT,
                entry_time TEXT,
                exit_time TEXT,
                instrument TEXT,
                option_symbol TEXT,
                option_type TEXT,
                strike REAL,
                expiry TEXT,
                entry_price REAL,
                exit_price REAL,
                quantity INTEGER,
                lot_size INTEGER,
                pnl_points REAL,
                pnl_amount REAL,
                pnl_percent REAL,
                charges REAL,
                net_pnl REAL,
                exit_reason TEXT,
                trade_duration_minutes INTEGER,
                market_regime_at_entry TEXT,
                market_regime_at_exit TEXT,
                risk_profile_id INTEGER
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                started_at TEXT,
                ended_at TEXT,
                strategy_name TEXT,
                strategy_version TEXT,
                settings_hash TEXT,
                settings_version TEXT,
                settings_schema_version INTEGER,
                data_start_date TEXT,
                data_end_date TEXT,
                initial_capital REAL,
                final_capital REAL,
                total_trades INTEGER,
                net_pnl REAL,
                max_drawdown REAL,
                notes TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                started_at TEXT,
                ended_at TEXT,
                strategy_name TEXT,
                strategy_version TEXT,
                settings_hash TEXT,
                settings_version TEXT,
                settings_schema_version INTEGER,
                initial_balance REAL,
                final_balance REAL,
                net_pnl REAL,
                total_trades INTEGER,
                notes TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS live_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                started_at TEXT,
                ended_at TEXT,
                strategy_name TEXT,
                strategy_version TEXT,
                settings_hash TEXT,
                settings_version TEXT,
                settings_schema_version INTEGER,
                initial_balance REAL,
                final_balance REAL,
                net_pnl REAL,
                total_trades INTEGER,
                notes TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                mode TEXT,
                strategy_name TEXT,
                strategy_version TEXT,
                settings_hash TEXT,
                settings_version TEXT,
                settings_schema_version INTEGER,
                total_trades INTEGER,
                winning_trades INTEGER,
                losing_trades INTEGER,
                gross_profit REAL,
                gross_loss REAL,
                net_pnl REAL,
                max_drawdown REAL,
                profit_factor REAL,
                accuracy REAL,
                average_win REAL,
                average_loss REAL,
                risk_reward_ratio REAL,
                consecutive_losses INTEGER,
                daily_target_hit INTEGER,
                daily_loss_hit INTEGER,
                market_regime_majority TEXT
            )
            """
        )
        for table_name in ("backtest_runs", "paper_sessions", "live_sessions", "daily_performance"):
            _ensure_sqlite_columns(cursor, table_name, {
                "settings_hash": "TEXT",
                "settings_version": "TEXT",
                "settings_schema_version": "INTEGER",
            })


def validate_risk_engine_sqlite(path):
    """Verify the SQLite file contains required risk engine tables and columns."""
    required_schema = {
        "trades": [
            "trade_id", "mode", "strategy_name", "strategy_version",
            "entry_time", "exit_time", "instrument", "option_symbol",
            "option_type", "strike", "expiry", "entry_price",
            "exit_price", "quantity", "lot_size", "pnl_points",
            "pnl_amount", "pnl_percent", "charges", "net_pnl",
            "exit_reason", "trade_duration_minutes",
            "market_regime_at_entry", "market_regime_at_exit",
            "risk_profile_id"
        ],
        "backtest_runs": [
            "run_id", "started_at", "ended_at", "strategy_name",
            "strategy_version", "settings_hash", "settings_version",
            "settings_schema_version", "data_start_date", "data_end_date",
            "initial_capital", "final_capital", "total_trades",
            "net_pnl", "max_drawdown", "notes"
        ],
        "paper_sessions": [
            "session_id", "started_at", "ended_at", "strategy_name",
            "strategy_version", "settings_hash", "settings_version",
            "settings_schema_version", "initial_balance",
            "final_balance", "net_pnl", "total_trades", "notes"
        ],
        "live_sessions": [
            "session_id", "started_at", "ended_at", "strategy_name",
            "strategy_version", "settings_hash", "settings_version",
            "settings_schema_version", "initial_balance",
            "final_balance", "net_pnl", "total_trades", "notes"
        ]
    }

    if not os.path.exists(path):
        return False

    with sqlite_connection(path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        existing_tables = {row[0] for row in cursor.fetchall()}

        for table_name, columns in required_schema.items():
            if table_name not in existing_tables:
                return False

            cursor.execute(f"PRAGMA table_info({table_name})")
            existing_columns = {row[1] for row in cursor.fetchall()}
            if not set(columns).issubset(existing_columns):
                return False

    return True


def _ensure_sqlite_columns(cursor, table_name, columns):
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing = {row[1] for row in cursor.fetchall()}
    for column, definition in columns.items():
        if column not in existing:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {definition}")


def write_sqlite(path, rows_or_df, table_name="trades"):
    if rows_or_df is None:
        return
    ensure_risk_engine_schema(path)
    df = rows_or_df if isinstance(rows_or_df, pd.DataFrame) else pd.DataFrame(rows_or_df)
    if df.empty:
        return
    df = format_time_columns(df)
    with sqlite_connection(path) as conn:
        df.to_sql(table_name, conn, if_exists="append", index=False)
