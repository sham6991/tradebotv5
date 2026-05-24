import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any

import pandas as pd

from engine import TradingEngine
from reporting import format_datetime_value
from backtest_runtime import BacktestTradingCore


ENTRY_STATUS_PRIORITY = {
    "COMPLETE": 5,
    "FILLED": 5,
    "PAPER": 5,
    "OPEN": 3,
    "PENDING": 2,
    "VALIDATION_PENDING": 2,
    "PUT_ORDER_REQ_RECEIVED": 2,
    "REJECTED": 1,
    "CANCELLED": 1,
}


def build_parity_report(db_path, nifty, options, settings, session_id="", price_tolerance=0.01):
    settings = dict(settings or {})
    if nifty is None or options is None:
        stored_nifty, stored_options = load_candle_frames(db_path, session_id=session_id, settings=settings)
        nifty = stored_nifty if nifty is None else nifty
        options = stored_options if options is None else options
    expected = build_backtest_decision_replay(nifty, options, settings)
    actual_entries = load_actual_entry_rows(db_path, session_id=session_id)
    comparisons = compare_entries(expected["entries"], actual_entries, price_tolerance=price_tolerance)
    skip_counts = {}
    for row in expected["skips"]:
        reason = row.get("skip_reason") or "no_trade"
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    mismatches = [row for row in comparisons if row.get("match") is False]
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": db_path,
        "session_id": session_id,
        "price_tolerance": price_tolerance,
        "summary": {
            "expected_entries": len(expected["entries"]),
            "actual_entries": len(actual_entries),
            "mismatches": len(mismatches),
            "expected_skips": len(expected["skips"]),
            "skip_reason_counts": dict(sorted(skip_counts.items())),
            "status": "MATCH" if not mismatches and len(expected["entries"]) == len(actual_entries) else "MISMATCH",
        },
        "mismatches": mismatches,
        "comparisons": comparisons,
        "expected_entries": expected["entries"],
        "actual_entries": actual_entries,
        "expected_skips": expected["skips"],
    }


def load_candle_frames(db_path, session_id="", settings=None):
    if not db_path or not os.path.exists(db_path):
        raise ValueError("SQLite session database is required to load parity candles.")
    with closing(sqlite3.connect(db_path)) as conn:
        if not _table_exists(conn, "candles"):
            raise ValueError("Session database does not contain completed candles. Supply NIFTY/CE/PE candle files.")
        rows = conn.execute(
            """
            SELECT stream_name, instrument, tradingsymbol, option_type, data
            FROM candles
            WHERE (? = '' OR session_id = ?)
            ORDER BY candle_time, id
            """,
            (session_id, session_id),
        ).fetchall()
    if not rows:
        raise ValueError("No completed candles found in the selected session database.")

    grouped = {}
    metadata = {}
    for stream_name, instrument, tradingsymbol, option_type, data in rows:
        stream = str(stream_name or "")
        grouped.setdefault(stream, []).append(_json(data))
        metadata[stream] = {
            "instrument": instrument or ("NIFTY" if stream == "NIFTY" else ""),
            "tradingsymbol": tradingsymbol or instrument or "",
            "option_type": str(option_type or "").upper(),
        }

    if "NIFTY" not in grouped:
        raise ValueError("Session database does not contain NIFTY candles.")
    nifty = _prepare_frame(pd.DataFrame(grouped["NIFTY"]))
    nifty.attrs.update(metadata.get("NIFTY", {"instrument": "NIFTY", "tradingsymbol": "NIFTY"}))

    option_frames = []
    for stream in sorted(
        [key for key in grouped if str(key).startswith("OPTION_")],
        key=lambda value: int(str(value).split("_")[1]) if str(value).split("_")[-1].isdigit() else 999,
    ):
        frame = _prepare_frame(pd.DataFrame(grouped[stream]))
        frame = _ensure_option_formula_frame(frame, settings or {})
        frame.attrs.update(metadata.get(stream, {}))
        option_frames.append(frame)

    if not option_frames:
        raise ValueError("Session database does not contain option candles.")
    return nifty, option_frames


def build_backtest_decision_replay(nifty, options, settings):
    nifty = _prepare_frame(nifty)
    options = [_prepare_frame(option) for option in (options or [])]
    engine = TradingEngine(settings.get("cooldown", 0))
    core = BacktestTradingCore(engine)
    core.balance = float(settings.get("balance", 0) or 0)
    core.lot_size = int(settings.get("lot_size", 1) or 1)
    core.max_trades = int(settings.get("max_trades", 999999) or 999999)

    entries = []
    skips = []
    start_index = 6 if len(nifty) > 6 else 0
    for index in range(start_index, max(start_index, len(nifty) - 1)):
        before = core.trade_count
        core.process(nifty, options, index, settings)
        timestamp = _row_time(nifty, index)
        if core.trade_count > before:
            entries.append(_expected_entry_row(core.trades[-1], index))
        else:
            skips.append({
                "nifty_index": index,
                "timestamp": timestamp,
                "skip_reason": engine.last_skip_reason or "no_trade",
            })

    return {
        "entries": entries,
        "skips": skips,
        "entry_attempts": list(core.entry_attempts),
        "final_balance": core.balance,
        "trade_count": core.trade_count,
    }


def load_actual_entry_rows(db_path, session_id=""):
    if not db_path or not os.path.exists(db_path):
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        if not _table_exists(conn, "order_history"):
            return []
        rows = conn.execute(
            """
            SELECT id, timestamp, instrument, option_type, action, order_type,
                   order_status, entry_price, early_score, entry_type,
                   final_decision, decision_reason, target_price, stop_loss_price,
                   related_trade_id, zerodha_order_id, data
            FROM order_history
            WHERE (? = '' OR session_id = ?)
              AND UPPER(COALESCE(action, '')) = 'BUY'
            ORDER BY id
            """,
            (session_id, session_id),
        ).fetchall()

    by_trade = {}
    for row in rows:
        item = _actual_entry_row(row)
        key = item.get("related_trade_id") or item.get("order_id") or str(item["sequence"])
        existing = by_trade.get(key)
        if existing is None or _entry_status_rank(item) >= _entry_status_rank(existing):
            by_trade[key] = item
    return sorted(by_trade.values(), key=lambda item: (item.get("timestamp", ""), item.get("sequence", 0)))


def compare_entries(expected_entries, actual_entries, price_tolerance=0.01):
    comparisons = []
    total = max(len(expected_entries), len(actual_entries))
    for index in range(total):
        expected = expected_entries[index] if index < len(expected_entries) else None
        actual = actual_entries[index] if index < len(actual_entries) else None
        row = {
            "sequence": index + 1,
            "match": True,
            "mismatches": [],
            "expected": expected or {},
            "actual": actual or {},
        }
        if expected is None:
            row["match"] = False
            row["mismatches"].append({"field": "entry", "expected": "", "actual": "extra_live_entry"})
        elif actual is None:
            row["match"] = False
            row["mismatches"].append({"field": "entry", "expected": "backtest_entry", "actual": ""})
        else:
            _compare_text(row, "option_type", expected.get("option_type"), actual.get("option_type"))
            _compare_text(row, "entry_type", expected.get("entry_type"), actual.get("entry_type"))
            _compare_text(row, "order_type", expected.get("order_type"), actual.get("order_type"))
            _compare_number(row, "entry_price", expected.get("entry_price"), actual.get("entry_price"), price_tolerance)
            _compare_number(row, "target_price", expected.get("target_price"), actual.get("target_price"), price_tolerance)
            _compare_number(row, "stoploss_price", expected.get("stoploss_price"), actual.get("stoploss_price"), price_tolerance)
            _compare_number(row, "early_score", expected.get("early_score"), actual.get("early_score"), 0.001)
        comparisons.append(row)
    return comparisons


def _expected_entry_row(trade, nifty_index):
    return {
        "nifty_index": nifty_index,
        "signal_time": format_datetime_value(trade.get("Signal Time", "")),
        "entry_time": format_datetime_value(trade.get("Entry Time", "")),
        "instrument": trade.get("Instrument", ""),
        "option_type": str(trade.get("Type", "") or "").upper(),
        "entry_type": trade.get("Entry Type", ""),
        "order_type": str(trade.get("Order Type", "") or "").upper(),
        "entry_price": _float_or_blank(trade.get("Entry")),
        "target_price": _target_price(trade),
        "stoploss_price": _stoploss_price(trade),
        "early_score": _float_or_blank(trade.get("Early Score")),
        "entry_remark": trade.get("Entry Remark", ""),
        "exit_reason": trade.get("Reason", ""),
    }


def _actual_entry_row(row):
    (
        row_id,
        timestamp,
        instrument,
        option_type,
        action,
        order_type,
        order_status,
        entry_price,
        early_score,
        entry_type,
        final_decision,
        decision_reason,
        target_price,
        stop_loss_price,
        related_trade_id,
        order_id,
        data,
    ) = row
    payload = _json(data)
    return {
        "sequence": row_id,
        "timestamp": format_datetime_value(timestamp),
        "instrument": instrument or "",
        "option_type": str(option_type or "").upper(),
        "action": action or "",
        "order_type": str(order_type or "").upper(),
        "order_status": str(order_status or "").upper(),
        "entry_price": _float_or_blank(entry_price),
        "early_score": _float_or_blank(early_score),
        "entry_type": entry_type or payload.get("Entry Type", ""),
        "final_decision": final_decision or "",
        "decision_reason": decision_reason or "",
        "target_price": _float_or_blank(target_price),
        "stoploss_price": _float_or_blank(stop_loss_price),
        "related_trade_id": related_trade_id or "",
        "order_id": order_id or "",
    }


def _target_price(trade):
    entry = _float_or_blank(trade.get("Entry"))
    points = _float_or_blank(trade.get("Profit Points"))
    return "" if entry == "" or points == "" else entry + points


def _stoploss_price(trade):
    entry = _float_or_blank(trade.get("Entry"))
    points = _float_or_blank(trade.get("Safety Points"))
    return "" if entry == "" or points == "" else entry - points


def _compare_text(row, field, expected, actual):
    expected_text = str(expected or "").strip().upper()
    actual_text = str(actual or "").strip().upper()
    if expected_text != actual_text:
        row["match"] = False
        row["mismatches"].append({"field": field, "expected": expected, "actual": actual})


def _compare_number(row, field, expected, actual, tolerance):
    expected_number = _float_or_none(expected)
    actual_number = _float_or_none(actual)
    if expected_number is None and actual_number is None:
        return
    if expected_number is None or actual_number is None or abs(expected_number - actual_number) > tolerance:
        row["match"] = False
        row["mismatches"].append({"field": field, "expected": expected, "actual": actual})


def _prepare_frame(frame):
    frame = frame.copy()
    if "datetime" in frame.columns:
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    try:
        from engine import attach_datetime_index_map

        frame = attach_datetime_index_map(frame)
    except Exception:
        pass
    return frame


def _ensure_option_formula_frame(frame, settings):
    try:
        from strategy import ensure_option_formula_columns

        return ensure_option_formula_columns(frame, settings)
    except Exception:
        return frame


def _row_time(df, index):
    if index >= len(df):
        return ""
    row = df.iloc[index]
    if "datetime" in row:
        return format_datetime_value(row.get("datetime", ""))
    date = row.get("date", "")
    time = row.get("time", "")
    return format_datetime_value(f"{date} {time}".strip())


def _entry_status_rank(item):
    return ENTRY_STATUS_PRIORITY.get(str(item.get("order_status") or "").upper(), 0)


def _float_or_blank(value):
    number = _float_or_none(value)
    return "" if number is None else number


def _float_or_none(value):
    try:
        if value in ("", None):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _json(text):
    try:
        value = json.loads(text or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None
