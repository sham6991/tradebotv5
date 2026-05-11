import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime


def build_session_audit(db_path, session_id=""):
    events = _load_events(db_path, session_id)
    order_rows = _load_order_history(db_path, session_id)
    trade_rows = _load_trade_audit(db_path)

    event_counts = {}
    for event in events:
        event_type = event.get("event_type", "")
        if event_type:
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

    action_counts = {}
    status_counts = {}
    for row in order_rows:
        action = row.get("action", "")
        status = row.get("order_status", "")
        if action:
            action_counts[action] = action_counts.get(action, 0) + 1
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": db_path,
        "session_id": session_id,
        "event_counts": event_counts,
        "order_action_counts": action_counts,
        "order_status_counts": status_counts,
        "totals": {
            "events": len(events),
            "order_history_rows": len(order_rows),
            "trade_audit_rows": len(trade_rows),
            "kill_switch_events": event_counts.get("KILL_SWITCH_ACTIVATED", 0),
            "reconciliation_warnings": event_counts.get("RECONCILIATION_WARNING", 0),
            "reconciliation_errors": event_counts.get("RECONCILIATION_ERROR", 0),
            "partial_entry_events": event_counts.get("ORDER_PARTIAL_FILL", 0),
            "partial_exit_events": event_counts.get("PARTIAL_EXIT_DETECTED", 0),
        },
        "recent_events": events[-20:],
        "recent_order_history": order_rows[-20:],
    }


def write_session_audit(db_path, output_path, session_id=""):
    audit = build_session_audit(db_path, session_id=session_id)
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(audit, handle, default=str, indent=2)
    return audit


def _load_events(db_path, session_id):
    if not db_path or not os.path.exists(db_path):
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT created_at, level, message, payload FROM events ORDER BY id"
        ).fetchall()
    events = []
    for created_at, level, message, payload_text in rows:
        payload = _json(payload_text)
        if session_id and payload.get("session_id", "") not in ("", session_id):
            continue
        events.append({
            "created_at": created_at,
            "level": level,
            "message": message,
            **payload,
        })
    return events


def _load_order_history(db_path, session_id):
    if not db_path or not os.path.exists(db_path):
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT timestamp, action, order_type, quantity, order_status,
                   zerodha_order_id, parent_order_id, related_trade_id, error_reason
            FROM order_history
            WHERE (? = '' OR session_id = ?)
            ORDER BY id
            """,
            (session_id, session_id),
        ).fetchall()
    return [
        {
            "timestamp": row[0],
            "action": row[1],
            "order_type": row[2],
            "quantity": row[3],
            "order_status": row[4],
            "zerodha_order_id": row[5],
            "parent_order_id": row[6],
            "related_trade_id": row[7],
            "error_reason": row[8],
        }
        for row in rows
    ]


def _load_trade_audit(db_path):
    if not db_path or not os.path.exists(db_path):
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("SELECT trade_no, data FROM trade_audit ORDER BY id").fetchall()
    return [{"trade_no": trade_no, "data": _json(data)} for trade_no, data in rows]


def _json(text):
    try:
        return json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
