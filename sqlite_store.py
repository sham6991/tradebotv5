import json
import os
import queue
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

import pandas as pd

from reporting import ensure_risk_engine_schema, format_datetime_value


class TradingStore:
    def __init__(self, path, mode="", settings=None):
        self.path = path
        self.mode = mode
        self.settings = settings or {}
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        self._move_legacy_trade_audit_table()
        ensure_risk_engine_schema(self.path)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    trade_no INTEGER,
                    data TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    order_id TEXT,
                    side TEXT,
                    status TEXT,
                    data TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    session_id TEXT,
                    session_trade_no INTEGER,
                    timestamp TEXT,
                    instrument TEXT,
                    option_type TEXT,
                    action TEXT,
                    order_type TEXT,
                    quantity INTEGER,
                    ordered_quantity INTEGER,
                    filled_quantity INTEGER,
                    pending_quantity INTEGER,
                    cancelled_quantity INTEGER,
                    is_partial_fill INTEGER,
                    order_status TEXT,
                    entry_price REAL,
                    buy_score REAL,
                    exit_price REAL,
                    exit_reason TEXT,
                    target_price REAL,
                    stop_loss_price REAL,
                    ltp_at_order_placement REAL,
                    zerodha_order_id TEXT,
                    parent_order_id TEXT,
                    related_trade_id TEXT,
                    error_reason TEXT,
                    data TEXT
                )
                """
            )
            self._ensure_order_history_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                )
                """
            )

    def _move_legacy_trade_audit_table(self):
        if not os.path.exists(self.path):
            return

        with self._connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "trades" not in tables:
                return

            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(trades)").fetchall()
            }
            if "trade_id" in columns:
                return

            target = "trade_audit"
            if target in tables:
                target = f"trade_audit_legacy_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            conn.execute(f"ALTER TABLE trades RENAME TO {target}")

    def _ensure_order_history_columns(self, conn):
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(order_history)").fetchall()
        }
        additions = {
            "ordered_quantity": "INTEGER",
            "filled_quantity": "INTEGER",
            "pending_quantity": "INTEGER",
            "cancelled_quantity": "INTEGER",
            "is_partial_fill": "INTEGER",
        }
        for column, definition in additions.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE order_history ADD COLUMN {column} {definition}")

    def log_event(self, level, message, payload=None):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events(created_at, level, message, payload) VALUES (?, ?, ?, ?)",
                (self._now(), level, message, self._json(payload)),
            )

    def log_trade(self, trade):
        normalized = self._normalize_trade_for_risk_engine(trade)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO trade_audit(created_at, trade_no, data) VALUES (?, ?, ?)",
                (self._now(), trade.get("Trade No"), self._json(trade)),
            )
            pd.DataFrame([normalized]).to_sql("trades", conn, if_exists="append", index=False)
        self.update_session(
            normalized["mode"],
            self.settings.get("session_id", ""),
            final_balance=trade.get("Total PnL", self.settings.get("balance", 0)),
            total_trades=trade.get("Trade No", 0),
        )

    def start_session(self, mode, session_id, initial_balance, notes=""):
        if not session_id:
            return
        table_name = self._session_table(mode)
        row = {
            "session_id": session_id,
            "started_at": self._now(),
            "ended_at": None,
            "strategy_name": self.settings.get("strategy_name", "tradebotV3_livepaper"),
            "strategy_version": self.settings.get("strategy_version", "1.0"),
            "initial_balance": initial_balance,
            "final_balance": initial_balance,
            "net_pnl": 0,
            "total_trades": 0,
            "notes": notes,
        }
        with self._connect() as conn:
            existing = conn.execute(
                f"SELECT id FROM {table_name} WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing:
                return
            pd.DataFrame([row]).to_sql(table_name, conn, if_exists="append", index=False)

    def update_session(self, mode, session_id, final_balance=None, total_trades=None, ended=False):
        if not session_id:
            return
        table_name = self._session_table(mode)
        initial = float(self.settings.get("balance", 0) or 0)
        final = float(final_balance if final_balance is not None else initial)
        ended_at = self._now() if ended else None
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE {table_name}
                SET final_balance = ?,
                    net_pnl = ?,
                    total_trades = ?,
                    ended_at = COALESCE(?, ended_at)
                WHERE session_id = ?
                """,
                (
                    final,
                    final - initial,
                    int(total_trades or 0),
                    ended_at,
                    session_id,
                ),
            )

    def log_order(self, order_id, side, status, data=None):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO orders(created_at, order_id, side, status, data) VALUES (?, ?, ?, ?, ?)",
                (self._now(), str(order_id or ""), side, status, self._json(data)),
            )

    def log_order_history(self, event):
        row = {
            "session_id": self.settings.get("session_id", ""),
            "session_trade_no": self._int_or_none(event.get("Session Trade No")),
            "timestamp": format_datetime_value(event.get("Timestamp")),
            "instrument": event.get("Instrument / Symbol", ""),
            "option_type": event.get("Option Type", ""),
            "action": event.get("Action", ""),
            "order_type": event.get("Order Type", ""),
            "quantity": self._int_or_none(event.get("Quantity")),
            "ordered_quantity": self._int_or_none(event.get("Ordered Quantity")),
            "filled_quantity": self._int_or_none(event.get("Filled Quantity")),
            "pending_quantity": self._int_or_none(event.get("Pending Quantity")),
            "cancelled_quantity": self._int_or_none(event.get("Cancelled Quantity")),
            "is_partial_fill": self._bool_int(event.get("Is Partial Fill")),
            "order_status": event.get("Order Status", ""),
            "entry_price": self._float_or_none(event.get("Entry Price")),
            "buy_score": self._float_or_none(event.get("Buy Score")),
            "exit_price": self._float_or_none(event.get("Exit Price")),
            "exit_reason": event.get("Exit Reason", ""),
            "target_price": self._float_or_none(event.get("Target Price")),
            "stop_loss_price": self._float_or_none(event.get("Stop Loss Price")),
            "ltp_at_order_placement": self._float_or_none(event.get("LTP at Order Placement")),
            "zerodha_order_id": event.get("Zerodha Order ID", ""),
            "parent_order_id": event.get("Parent Order ID", ""),
            "related_trade_id": event.get("Related Trade ID", ""),
            "error_reason": event.get("Error / Rejection Reason", ""),
            "data": self._json(event),
        }
        with self._connect() as conn:
            columns = ", ".join(["created_at", *row.keys()])
            placeholders = ", ".join(["?"] * (len(row) + 1))
            conn.execute(
                f"INSERT INTO order_history({columns}) VALUES ({placeholders})",
                [self._now(), *row.values()],
            )

    def save_state(self, key, data):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO state(key, updated_at, data) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET updated_at = excluded.updated_at, data = excluded.data
                """,
                (key, self._now(), self._json(data)),
            )

    def load_state(self, key):
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def clear_state(self, key):
        with self._connect() as conn:
            conn.execute("DELETE FROM state WHERE key = ?", (key,))

    def _json(self, value):
        return json.dumps(value or {}, default=str)

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _session_table(self, mode):
        return "live_sessions" if str(mode).upper() == "LIVE" else "paper_sessions"

    def _normalize_trade_for_risk_engine(self, trade):
        mode = self.mode or trade.get("Mode") or "PAPER"
        entry = self._float_or_none(trade.get("Entry"))
        exit_price = self._float_or_none(trade.get("Exit"))
        quantity = int(self._float_or_none(trade.get("Quantity")) or 0)
        pnl_amount = self._float_or_none(trade.get("Final PnL"))
        if pnl_amount is None:
            pnl_amount = self._float_or_none(trade.get("PnL")) or 0
        pnl_points = None
        if entry is not None and exit_price is not None:
            pnl_points = exit_price - entry
        pnl_percent = None
        if entry not in (None, 0) and pnl_points is not None:
            pnl_percent = (pnl_points / entry) * 100

        return {
            "trade_id": trade.get("trade_id") or f"{mode}_{trade.get('Trade No', 0)}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            "mode": mode,
            "strategy_name": self.settings.get("strategy_name", "tradebotV3_livepaper"),
            "strategy_version": self.settings.get("strategy_version", "1.0"),
            "entry_time": format_datetime_value(trade.get("Entry Time")),
            "exit_time": format_datetime_value(trade.get("Exit Time")),
            "instrument": trade.get("Instrument", ""),
            "option_symbol": trade.get("Instrument", ""),
            "option_type": trade.get("Type", ""),
            "strike": trade.get("Strike"),
            "expiry": trade.get("Expiry"),
            "entry_price": entry,
            "exit_price": exit_price,
            "quantity": quantity,
            "lot_size": int(self._float_or_none(trade.get("Contract Lot Size")) or 0),
            "pnl_points": pnl_points,
            "pnl_amount": pnl_amount,
            "pnl_percent": pnl_percent,
            "charges": self._float_or_none(trade.get("Charges")) or 0.0,
            "net_pnl": self._float_or_none(trade.get("Total PnL")) or 0,
            "exit_reason": trade.get("Reason", ""),
            "trade_duration_minutes": trade.get("Duration"),
            "market_regime_at_entry": trade.get("Market Regime", ""),
            "market_regime_at_exit": trade.get("Market Regime", ""),
            "risk_profile_id": None,
        }

    def _float_or_none(self, value):
        if value in ("", None):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _int_or_none(self, value):
        number = self._float_or_none(value)
        return int(number) if number is not None else None

    def _bool_int(self, value):
        if isinstance(value, bool):
            return 1 if value else 0
        text = str(value or "").strip().lower()
        if text in ("1", "true", "yes", "y"):
            return 1
        if text in ("0", "false", "no", "n", ""):
            return 0
        return None


class AsyncTradingStore:
    def __init__(self, store, max_queue_size=10000):
        self.store = store
        self.path = store.path
        self.mode = store.mode
        self.settings = store.settings
        self.queue = queue.Queue(maxsize=max(1, int(max_queue_size or 10000)))
        self.thread = None
        self.lock = threading.Lock()
        self.closed = False
        self.enqueued_writes = 0
        self.completed_writes = 0
        self.dropped_writes = 0
        self.errors = []

    def start_session(self, *args, **kwargs):
        return self.store.start_session(*args, **kwargs)

    def log_event(self, *args, **kwargs):
        return self._enqueue("log_event", args, kwargs)

    def log_trade(self, *args, **kwargs):
        return self._enqueue("log_trade", args, kwargs)

    def update_session(self, *args, **kwargs):
        return self._enqueue("update_session", args, kwargs)

    def log_order(self, *args, **kwargs):
        return self._enqueue("log_order", args, kwargs)

    def log_order_history(self, *args, **kwargs):
        return self._enqueue("log_order_history", args, kwargs)

    def save_state(self, *args, **kwargs):
        return self.store.save_state(*args, **kwargs)

    def load_state(self, *args, **kwargs):
        return self.store.load_state(*args, **kwargs)

    def clear_state(self, *args, **kwargs):
        return self.store.clear_state(*args, **kwargs)

    def close(self, timeout=10):
        with self.lock:
            if self.closed:
                return True
            self.closed = True
            thread = self.thread
        if thread is None or not thread.is_alive():
            return True
        done = threading.Event()
        self.queue.put(("close", "", (), {}, done))
        completed = done.wait(timeout)
        thread.join(timeout=1)
        return completed

    def health(self):
        return {
            "async": True,
            "queue_size": self.queue.qsize(),
            "enqueued_writes": self.enqueued_writes,
            "completed_writes": self.completed_writes,
            "dropped_writes": self.dropped_writes,
            "errors": list(self.errors[-5:]),
        }

    def _enqueue(self, method_name, args, kwargs):
        with self.lock:
            if self.closed:
                return getattr(self.store, method_name)(*args, **kwargs)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(
                    target=self._run,
                    name="tradebot_async_sqlite_store",
                    daemon=True,
                )
                self.thread.start()
        try:
            self.queue.put_nowait(("call", method_name, args, kwargs, None))
            self.enqueued_writes += 1
            return True
        except queue.Full:
            self.dropped_writes += 1
            self.errors.append({
                "method": method_name,
                "error": "async sqlite queue full",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            return False

    def _run(self):
        while True:
            kind, method_name, args, kwargs, done = self.queue.get()
            if kind == "close":
                if done:
                    done.set()
                break
            try:
                getattr(self.store, method_name)(*args, **kwargs)
                self.completed_writes += 1
            except Exception as exc:
                self.errors.append({
                    "method": method_name,
                    "error": str(exc),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
