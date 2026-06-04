from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any


class IntradayDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.ensure_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS intraday_sessions (
                    session_id TEXT PRIMARY KEY,
                    mode TEXT,
                    broker TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    selected_symbols TEXT,
                    locked_settings_json TEXT,
                    starting_balance REAL,
                    ending_balance REAL,
                    realized_pnl REAL,
                    unrealized_pnl REAL,
                    max_profit REAL,
                    max_loss REAL,
                    status TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    symbol TEXT,
                    exchange TEXT,
                    validation_status TEXT,
                    company_name TEXT,
                    sector TEXT,
                    instrument_token TEXT,
                    tick_size TEXT,
                    lot_size TEXT,
                    segment TEXT,
                    mis_allowed INTEGER,
                    data_available INTEGER,
                    suggestions_json TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_market_cues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    phase TEXT,
                    cue_state TEXT,
                    market_regime TEXT,
                    nifty_trend TEXT,
                    sector_trend TEXT,
                    global_cue TEXT,
                    fii_dii_used INTEGER,
                    source_breakdown_json TEXT,
                    algo_adjustment TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_stock_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    symbol TEXT,
                    ltp REAL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    candle_interval TEXT,
                    candles_available INTEGER,
                    last_candle_time TEXT,
                    data_source TEXT,
                    ema20 REAL,
                    ema50 REAL,
                    rsi REAL,
                    vwap REAL,
                    poc REAL,
                    vah REAL,
                    val REAL,
                    relative_volume REAL,
                    spread REAL,
                    bid_qty REAL,
                    ask_qty REAL,
                    depth_imbalance REAL,
                    liquidity_score REAL,
                    trap_score REAL,
                    news_score REAL,
                    options_bias_score REAL,
                    final_long_score REAL,
                    final_short_score REAL,
                    selected_side TEXT,
                    reason_json TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    symbol TEXT,
                    side TEXT,
                    setup_name TEXT,
                    score REAL,
                    score_breakdown_json TEXT,
                    entry_price REAL,
                    stoploss REAL,
                    target REAL,
                    risk_reward REAL,
                    approved_by_user INTEGER,
                    rejected_reason TEXT,
                    final_decision TEXT,
                    final_quantity INTEGER,
                    risk_based_quantity INTEGER,
                    margin_based_quantity INTEGER,
                    allowed_capital REAL,
                    estimated_leverage REAL,
                    estimated_required_margin REAL,
                    actual_required_margin REAL,
                    margin_validation_status TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_margin_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    mode TEXT,
                    symbol TEXT,
                    exchange TEXT,
                    side TEXT,
                    entry_price REAL,
                    stoploss_price REAL,
                    available_funds REAL,
                    allowed_capital REAL,
                    max_loss_allowed REAL,
                    risk_per_share REAL,
                    estimated_leverage REAL,
                    estimated_trade_value REAL,
                    estimated_required_margin REAL,
                    actual_required_margin REAL,
                    trade_value REAL,
                    risk_based_quantity INTEGER,
                    margin_based_quantity INTEGER,
                    final_quantity INTEGER,
                    margin_validation_status TEXT,
                    rejection_reason TEXT,
                    raw_margin_response_json TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    broker_order_id TEXT,
                    local_order_id TEXT,
                    parent_trade_id TEXT,
                    mode TEXT,
                    symbol TEXT,
                    side TEXT,
                    transaction_type TEXT,
                    order_type TEXT,
                    product TEXT,
                    quantity INTEGER,
                    price REAL,
                    trigger_price REAL,
                    status TEXT,
                    status_message TEXT,
                    broker_response_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_order_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    local_order_id TEXT,
                    broker_order_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    role TEXT,
                    event TEXT,
                    status TEXT,
                    status_message TEXT,
                    broker_response_json TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    quantity INTEGER,
                    entry_time TEXT,
                    entry_price REAL,
                    exit_time TEXT,
                    exit_price REAL,
                    stoploss REAL,
                    target REAL,
                    pnl_gross REAL,
                    charges REAL,
                    pnl_net REAL,
                    exit_reason TEXT,
                    setup_name TEXT,
                    score REAL,
                    result TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    symbol TEXT,
                    headline TEXT,
                    source TEXT,
                    url TEXT,
                    sentiment TEXT,
                    impact TEXT,
                    relevance REAL,
                    summary TEXT,
                    raw_json TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_trade_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    symbol TEXT,
                    health_score REAL,
                    recommendation TEXT,
                    dynamic_sl REAL,
                    target REAL,
                    opposite_signal_score REAL,
                    invalidation_status TEXT,
                    details_json TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_trade_management_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    trade_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    action TEXT,
                    health_score REAL,
                    r_multiple REAL,
                    old_stoploss REAL,
                    new_stoploss REAL,
                    old_target REAL,
                    new_target REAL,
                    exit_price REAL,
                    partial_quantity INTEGER,
                    broker_order_id TEXT,
                    status TEXT,
                    reason TEXT,
                    details_json TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_post_trade_learning (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    symbol TEXT,
                    setup_name TEXT,
                    result TEXT,
                    lesson TEXT,
                    details_json TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    module TEXT,
                    severity TEXT,
                    message TEXT,
                    details_json TEXT
                );
                CREATE TABLE IF NOT EXISTS intraday_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    level TEXT,
                    module TEXT,
                    event TEXT,
                    details_json TEXT
                );
                """
            )
            self._ensure_columns(
                conn,
                "intraday_signals",
                {
                    "final_quantity": "INTEGER",
                    "risk_based_quantity": "INTEGER",
                    "margin_based_quantity": "INTEGER",
                    "allowed_capital": "REAL",
                    "estimated_leverage": "REAL",
                    "estimated_required_margin": "REAL",
                    "actual_required_margin": "REAL",
                    "margin_validation_status": "TEXT",
                },
            )
            self._ensure_columns(
                conn,
                "intraday_stock_snapshots",
                {
                    "candle_interval": "TEXT",
                    "candles_available": "INTEGER",
                    "last_candle_time": "TEXT",
                    "data_source": "TEXT",
                },
            )
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_intraday_symbols_session ON intraday_symbols(session_id);
                CREATE INDEX IF NOT EXISTS idx_intraday_market_cues_session ON intraday_market_cues(session_id);
                CREATE INDEX IF NOT EXISTS idx_intraday_snapshots_session_symbol ON intraday_stock_snapshots(session_id, symbol);
                CREATE INDEX IF NOT EXISTS idx_intraday_signals_session ON intraday_signals(session_id);
                CREATE INDEX IF NOT EXISTS idx_intraday_orders_session ON intraday_orders(session_id);
                CREATE INDEX IF NOT EXISTS idx_intraday_order_events_session ON intraday_order_events(session_id);
                CREATE INDEX IF NOT EXISTS idx_intraday_trades_session ON intraday_trades(session_id);
                CREATE INDEX IF NOT EXISTS idx_intraday_news_session_symbol ON intraday_news(session_id, symbol);
                CREATE INDEX IF NOT EXISTS idx_intraday_trade_management_session ON intraday_trade_management_events(session_id);
                """
            )

    def _ensure_columns(self, conn, table: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, declaration in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def create_session(self, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO intraday_sessions
                (session_id, mode, broker, start_time, end_time, selected_symbols, locked_settings_json,
                 starting_balance, ending_balance, realized_pnl, unrealized_pnl, max_profit, max_loss, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["session_id"],
                    payload["mode"],
                    payload["broker"],
                    payload["start_time"],
                    payload.get("end_time", ""),
                    json.dumps(payload.get("selected_symbols") or []),
                    json.dumps(payload.get("locked_settings") or {}),
                    payload.get("starting_balance", 0),
                    payload.get("ending_balance", 0),
                    payload.get("realized_pnl", 0),
                    payload.get("unrealized_pnl", 0),
                    payload.get("max_profit", 0),
                    payload.get("max_loss", 0),
                    payload.get("status", "RUNNING"),
                ),
            )

    def update_session(self, session_id: str, updates: dict[str, Any]) -> None:
        if not updates:
            return
        allowed = {
            "end_time", "ending_balance", "realized_pnl", "unrealized_pnl",
            "max_profit", "max_loss", "status",
        }
        columns = [key for key in updates if key in allowed]
        if not columns:
            return
        values = [updates[key] for key in columns] + [session_id]
        assignment = ", ".join(f"{key}=?" for key in columns)
        with self.connect() as conn:
            conn.execute(f"UPDATE intraday_sessions SET {assignment} WHERE session_id=?", values)

    def save_snapshot(self, session_id: str, snapshot: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_stock_snapshots
                (session_id, timestamp, symbol, ltp, open, high, low, close, volume,
                 candle_interval, candles_available, last_candle_time, data_source, ema20, ema50, rsi,
                 vwap, poc, vah, val, relative_volume, spread, bid_qty, ask_qty, depth_imbalance,
                 liquidity_score, trap_score, news_score, options_bias_score, final_long_score,
                 final_short_score, selected_side, reason_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    snapshot.get("timestamp"),
                    snapshot.get("symbol"),
                    snapshot.get("ltp"),
                    snapshot.get("open"),
                    snapshot.get("high"),
                    snapshot.get("low"),
                    snapshot.get("close"),
                    snapshot.get("volume"),
                    snapshot.get("candle_interval"),
                    snapshot.get("candles_available"),
                    snapshot.get("last_candle_time"),
                    snapshot.get("data_source"),
                    snapshot.get("ema20"),
                    snapshot.get("ema50"),
                    snapshot.get("rsi"),
                    snapshot.get("vwap"),
                    snapshot.get("poc"),
                    snapshot.get("vah"),
                    snapshot.get("val"),
                    snapshot.get("relative_volume"),
                    snapshot.get("spread"),
                    snapshot.get("bid_qty"),
                    snapshot.get("ask_qty"),
                    snapshot.get("depth_imbalance"),
                    snapshot.get("liquidity_score"),
                    snapshot.get("trap_score"),
                    snapshot.get("news_score"),
                    snapshot.get("options_bias_score"),
                    snapshot.get("final_long_score"),
                    snapshot.get("final_short_score"),
                    snapshot.get("selected_side"),
                    json.dumps(snapshot.get("reason") or {}),
                ),
            )

    def save_symbol(self, session_id: str, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_symbols
                (session_id, symbol, exchange, validation_status, company_name, sector, instrument_token,
                 tick_size, lot_size, segment, mis_allowed, data_available, suggestions_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    row.get("symbol", ""),
                    row.get("exchange", ""),
                    row.get("validation_status", ""),
                    row.get("company_name", ""),
                    row.get("sector", ""),
                    str(row.get("instrument_token", "")),
                    str(row.get("tick_size", "")),
                    str(row.get("lot_size", "")),
                    row.get("segment", ""),
                    int(row.get("mis_allowed", 0) or 0),
                    int(row.get("data_available", 0) or 0),
                    json.dumps(row.get("suggestions") or []),
                ),
            )

    def save_market_cue(self, session_id: str, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_market_cues
                (session_id, timestamp, phase, cue_state, market_regime, nifty_trend, sector_trend,
                 global_cue, fii_dii_used, source_breakdown_json, algo_adjustment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    row.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                    row.get("phase", ""),
                    row.get("cue_state", ""),
                    row.get("market_regime", ""),
                    row.get("nifty_trend", ""),
                    row.get("sector_trend", ""),
                    row.get("global_cue", ""),
                    int(row.get("fii_dii_used", 0) or 0),
                    json.dumps(row.get("source_breakdown") or {}),
                    row.get("algo_adjustment", ""),
                ),
            )

    def save_signal(self, signal: dict[str, Any]) -> None:
        margin = signal.get("margin") or {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_signals
                (session_id, timestamp, symbol, side, setup_name, score, score_breakdown_json,
                 entry_price, stoploss, target, risk_reward, approved_by_user, rejected_reason, final_decision,
                 final_quantity, risk_based_quantity, margin_based_quantity, allowed_capital, estimated_leverage,
                 estimated_required_margin, actual_required_margin, margin_validation_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.get("session_id"),
                    datetime.now().isoformat(timespec="seconds"),
                    signal.get("symbol"),
                    signal.get("side"),
                    signal.get("setup_name"),
                    signal.get("score"),
                    json.dumps(signal.get("score_breakdown") or {}),
                    signal.get("entry_price"),
                    signal.get("stoploss"),
                    signal.get("target"),
                    signal.get("risk_reward"),
                    1 if signal.get("approved_by_user") else 0,
                    signal.get("rejected_reason", "") or margin.get("rejection_reason", ""),
                    signal.get("final_decision"),
                    margin.get("final_quantity"),
                    margin.get("risk_based_quantity"),
                    margin.get("margin_based_quantity"),
                    margin.get("allowed_margin_capital") or margin.get("allowed_capital"),
                    margin.get("estimated_leverage"),
                    margin.get("estimated_required_margin"),
                    margin.get("actual_required_margin"),
                    margin.get("margin_validation_status"),
                ),
            )

    def save_margin_check(self, session_id: str, settings, signal, margin: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_margin_checks
                (session_id, timestamp, mode, symbol, exchange, side, entry_price, stoploss_price,
                 available_funds, allowed_capital, max_loss_allowed, risk_per_share, estimated_leverage,
                 estimated_trade_value, estimated_required_margin, actual_required_margin, trade_value,
                 risk_based_quantity, margin_based_quantity, final_quantity, margin_validation_status,
                 rejection_reason, raw_margin_response_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    datetime.now().isoformat(timespec="seconds"),
                    getattr(settings, "mode", ""),
                    getattr(signal, "symbol", ""),
                    getattr(signal, "exchange", ""),
                    getattr(signal, "side", ""),
                    margin.get("entry_price"),
                    margin.get("stoploss_price"),
                    margin.get("available_funds"),
                    margin.get("allowed_margin_capital") or margin.get("allowed_capital"),
                    margin.get("max_loss_allowed"),
                    margin.get("risk_per_share"),
                    margin.get("estimated_leverage"),
                    margin.get("estimated_trade_value"),
                    margin.get("estimated_required_margin"),
                    margin.get("actual_required_margin"),
                    margin.get("trade_value"),
                    margin.get("risk_based_quantity"),
                    margin.get("margin_based_quantity"),
                    margin.get("final_quantity"),
                    margin.get("margin_validation_status"),
                    margin.get("rejection_reason", ""),
                    json.dumps(margin.get("raw_margin_response"), default=str),
                ),
            )

    def save_order(self, order: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_orders
                (session_id, broker_order_id, local_order_id, parent_trade_id, mode, symbol, side,
                 transaction_type, order_type, product, quantity, price, trigger_price, status,
                 status_message, broker_response_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.get("session_id"),
                    order.get("broker_order_id", ""),
                    order.get("local_order_id", ""),
                    order.get("parent_trade_id", ""),
                    order.get("mode", ""),
                    order.get("symbol", ""),
                    order.get("side", ""),
                    order.get("transaction_type", ""),
                    order.get("order_type", ""),
                    order.get("product", ""),
                    order.get("quantity", 0),
                    order.get("price"),
                    order.get("trigger_price"),
                    order.get("status", ""),
                    order.get("status_message", ""),
                    json.dumps(order.get("broker_response") or {}),
                    order.get("created_at") or datetime.now().isoformat(timespec="seconds"),
                    order.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def save_order_event(self, session_id: str, order: dict[str, Any], event: str, message: str = "", broker_response: dict | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_order_events
                (session_id, timestamp, local_order_id, broker_order_id, symbol, side, role,
                 event, status, status_message, broker_response_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    datetime.now().isoformat(timespec="seconds"),
                    order.get("local_order_id", ""),
                    order.get("broker_order_id", ""),
                    order.get("symbol", ""),
                    order.get("side", ""),
                    order.get("role", ""),
                    event,
                    order.get("status", event),
                    message or order.get("status_message", ""),
                    json.dumps(broker_response or order.get("broker_response") or {}),
                ),
            )

    def update_order_status(self, local_order_id: str, status: str, status_message: str, broker_response: dict | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE intraday_orders
                SET status=?, status_message=?, broker_response_json=?, updated_at=?
                WHERE local_order_id=?
                """,
                (
                    status,
                    status_message,
                    json.dumps(broker_response or {}),
                    datetime.now().isoformat(timespec="seconds"),
                    local_order_id,
                ),
            )

    def update_order_prices(
        self,
        local_order_id: str,
        *,
        price: float | None = None,
        trigger_price: float | None = None,
        quantity: int | None = None,
        status_message: str = "",
        broker_response: dict | None = None,
    ) -> None:
        updates = {"updated_at": datetime.now().isoformat(timespec="seconds")}
        if price is not None:
            updates["price"] = price
        if trigger_price is not None:
            updates["trigger_price"] = trigger_price
        if quantity is not None:
            updates["quantity"] = int(quantity)
        if status_message:
            updates["status_message"] = status_message
        if broker_response is not None:
            updates["broker_response_json"] = json.dumps(broker_response)
        columns = list(updates)
        values = [updates[key] for key in columns] + [local_order_id]
        with self.connect() as conn:
            conn.execute(
                f"UPDATE intraday_orders SET {', '.join(f'{key}=?' for key in columns)} WHERE local_order_id=?",
                values,
            )

    def save_trade(self, trade: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_trades
                (session_id, symbol, side, quantity, entry_time, entry_price, exit_time, exit_price,
                 stoploss, target, pnl_gross, charges, pnl_net, exit_reason, setup_name, score, result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.get("session_id"),
                    trade.get("symbol"),
                    trade.get("side"),
                    trade.get("quantity", 0),
                    trade.get("entry_time", ""),
                    trade.get("entry_price", 0),
                    trade.get("exit_time", ""),
                    trade.get("exit_price", 0),
                    trade.get("stoploss", 0),
                    trade.get("target", 0),
                    trade.get("pnl_gross", 0),
                    trade.get("charges", 0),
                    trade.get("pnl_net", 0),
                    trade.get("exit_reason", ""),
                    trade.get("setup_name", ""),
                    trade.get("score", 0),
                    trade.get("result", ""),
                ),
            )

    def save_trade_health(self, session_id: str, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_trade_health
                (session_id, timestamp, symbol, health_score, recommendation, dynamic_sl,
                 target, opposite_signal_score, invalidation_status, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    row.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                    row.get("symbol", ""),
                    row.get("health_score", 0),
                    row.get("recommendation", ""),
                    row.get("dynamic_sl"),
                    row.get("target"),
                    row.get("opposite_signal_score", 0),
                    row.get("invalidation_status", ""),
                    json.dumps(row.get("details") or {}, default=str),
                ),
            )

    def save_trade_management_event(self, session_id: str, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_trade_management_events
                (session_id, timestamp, trade_id, symbol, side, action, health_score, r_multiple,
                 old_stoploss, new_stoploss, old_target, new_target, exit_price, partial_quantity,
                 broker_order_id, status, reason, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    row.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                    row.get("trade_id", ""),
                    row.get("symbol", ""),
                    row.get("side", ""),
                    row.get("action", ""),
                    row.get("health_score", 0),
                    row.get("r_multiple", 0),
                    row.get("old_stoploss"),
                    row.get("new_stoploss"),
                    row.get("old_target"),
                    row.get("new_target"),
                    row.get("exit_price"),
                    row.get("partial_quantity", 0),
                    row.get("broker_order_id", ""),
                    row.get("status", ""),
                    row.get("reason", ""),
                    json.dumps(row.get("details") or {}, default=str),
                ),
            )

    def save_news(self, session_id: str, item: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_news
                (session_id, timestamp, symbol, headline, source, url, sentiment, impact, relevance, summary, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    item.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                    item.get("symbol", ""),
                    item.get("headline", ""),
                    item.get("source", ""),
                    item.get("url", ""),
                    item.get("sentiment", ""),
                    item.get("impact", ""),
                    item.get("relevance", 0),
                    item.get("summary", ""),
                    json.dumps(item.get("raw") or {}),
                ),
            )

    def save_error(self, session_id: str, module: str, severity: str, message: str, details: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_errors
                (session_id, timestamp, module, severity, message, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    datetime.now().isoformat(timespec="seconds"),
                    module,
                    severity,
                    message,
                    json.dumps(details or {}),
                ),
            )

    def save_audit(self, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO intraday_audit_log
                (session_id, timestamp, level, module, event, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("session_id"),
                    row.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                    row.get("level", "INFO"),
                    row.get("module", "intraday"),
                    row.get("event", ""),
                    json.dumps(row.get("details") or {}),
                ),
            )

    def table_rows(self, table: str, session_id: str | None = None) -> list[dict[str, Any]]:
        allowed = {
            "intraday_sessions",
            "intraday_symbols",
            "intraday_market_cues",
            "intraday_stock_snapshots",
            "intraday_signals",
            "intraday_margin_checks",
            "intraday_orders",
            "intraday_order_events",
            "intraday_trades",
            "intraday_news",
            "intraday_trade_health",
            "intraday_trade_management_events",
            "intraday_post_trade_learning",
            "intraday_errors",
            "intraday_audit_log",
        }
        if table not in allowed:
            raise ValueError("Unsupported intraday table.")
        with self.connect() as conn:
            if session_id and table != "intraday_sessions":
                rows = conn.execute(f"SELECT * FROM {table} WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
            elif session_id:
                rows = conn.execute(f"SELECT * FROM {table} WHERE session_id=?", (session_id,)).fetchall()
            else:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            return [dict(row) for row in rows]
