import json
import os
import sqlite3
from collections import Counter
from contextlib import closing


def load_session_timeline(db_path, session_id="", include_order_history=True):
    timeline = []
    timeline.extend(_event_items(db_path, session_id))
    if include_order_history:
        timeline.extend(_order_history_items(db_path, session_id))
    return sorted(timeline, key=lambda item: (item.get("timestamp", ""), item.get("sequence", 0)))


def build_session_replay(db_path, session_id="", include_order_history=True):
    timeline = load_session_timeline(
        db_path,
        session_id=session_id,
        include_order_history=include_order_history,
    )
    return {
        "db_path": db_path,
        "session_id": session_id,
        "summary": summarize_timeline(timeline),
        "highlights": replay_highlights(timeline),
        "timeline": timeline,
    }


def summarize_timeline(timeline):
    event_types = Counter()
    event_levels = Counter()
    order_actions = Counter()
    order_statuses = Counter()
    order_ids = set()
    trade_ids = set()
    first_timestamp = ""
    last_timestamp = ""

    for item in timeline:
        timestamp = item.get("timestamp", "")
        if timestamp and not first_timestamp:
            first_timestamp = timestamp
        if timestamp:
            last_timestamp = timestamp
        if item.get("order_id"):
            order_ids.add(str(item.get("order_id")))
        if item.get("trade_no") not in ("", None):
            trade_ids.add(str(item.get("trade_no")))
        if item.get("related_trade_id"):
            trade_ids.add(str(item.get("related_trade_id")))

        if item.get("kind") == "event":
            event_types[item.get("event_type", "") or "UNSTRUCTURED"] += 1
            event_levels[item.get("level", "") or "INFO"] += 1
        elif item.get("kind") == "order_history":
            order_actions[item.get("action", "") or "UNKNOWN"] += 1
            order_statuses[item.get("order_status", "") or "UNKNOWN"] += 1

    return {
        "total_items": len(timeline),
        "event_items": sum(event_types.values()),
        "order_history_items": sum(order_actions.values()),
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "event_type_counts": dict(sorted(event_types.items())),
        "event_level_counts": dict(sorted(event_levels.items())),
        "order_action_counts": dict(sorted(order_actions.items())),
        "order_status_counts": dict(sorted(order_statuses.items())),
        "unique_order_count": len(order_ids),
        "unique_trade_count": len(trade_ids),
    }


def replay_highlights(timeline):
    highlights = {
        "critical_events": [],
        "warning_events": [],
        "partial_events": [],
        "rejected_or_failed_orders": [],
        "kill_switch_events": [],
        "reconciliation_events": [],
        "unknown_broker_state_events": [],
    }
    for item in timeline:
        event_type = item.get("event_type", "")
        level = str(item.get("level", "") or "").upper()
        status = str(item.get("order_status", "") or item.get("status", "") or "").upper()
        message = str(item.get("message", "") or "")

        if level == "CRITICAL":
            highlights["critical_events"].append(item)
        if level in {"WARN", "WARNING"}:
            highlights["warning_events"].append(item)
        if event_type in {"ORDER_PARTIAL_FILL", "PARTIAL_EXIT_DETECTED"} or "PARTIAL" in status:
            highlights["partial_events"].append(item)
        if "REJECT" in status or "FAILED" in status or "ERROR" in status:
            highlights["rejected_or_failed_orders"].append(item)
        if event_type == "KILL_SWITCH_ACTIVATED":
            highlights["kill_switch_events"].append(item)
        if event_type in {"RECONCILIATION_WARNING", "RECONCILIATION_ERROR"}:
            highlights["reconciliation_events"].append(item)
        if event_type == "ORDER_UNKNOWN_BROKER_STATE" or "unknown broker state" in message.lower():
            highlights["unknown_broker_state_events"].append(item)
    return highlights


def format_timeline_lines(timeline, include_payload=False):
    lines = []
    for item in timeline:
        if item["kind"] == "event":
            line = (
                f"{item['timestamp']} EVENT {item.get('event_type', '')} "
                f"{item.get('level', '')} {item.get('status', '')} "
                f"{item.get('order_id', '')} {item.get('message', '')}".strip()
            )
        else:
            line = (
                f"{item['timestamp']} ORDER {item.get('action', '')} "
                f"{item.get('order_status', '')} {item.get('order_id', '')} "
                f"{item.get('quantity', '')} {item.get('instrument', '')}".strip()
            )
        if include_payload:
            payload = item.get("payload") or {}
            if payload:
                line = f"{line} | payload={json.dumps(payload, default=str, sort_keys=True)}"
        lines.append(" ".join(line.split()))
    return lines


def format_replay_report(report, include_payload=False):
    summary = report.get("summary", {})
    highlights = report.get("highlights", {})
    lines = [
        "SESSION REPLAY",
        f"Database: {report.get('db_path', '')}",
        f"Session: {report.get('session_id', '') or 'ALL'}",
        (
            "Window: "
            f"{summary.get('first_timestamp', '') or 'n/a'}"
            f" -> {summary.get('last_timestamp', '') or 'n/a'}"
        ),
        (
            "Totals: "
            f"{summary.get('total_items', 0)} timeline items, "
            f"{summary.get('event_items', 0)} events, "
            f"{summary.get('order_history_items', 0)} order rows, "
            f"{summary.get('unique_order_count', 0)} orders"
        ),
        "",
        "HIGHLIGHTS",
        f"Critical events: {len(highlights.get('critical_events', []))}",
        f"Warnings: {len(highlights.get('warning_events', []))}",
        f"Partial fills/exits: {len(highlights.get('partial_events', []))}",
        f"Rejected/failed orders: {len(highlights.get('rejected_or_failed_orders', []))}",
        f"Reconciliation events: {len(highlights.get('reconciliation_events', []))}",
        f"Unknown broker state events: {len(highlights.get('unknown_broker_state_events', []))}",
        "",
        "TIMELINE",
    ]
    lines.extend(format_timeline_lines(report.get("timeline", []), include_payload=include_payload))
    return lines


def _event_items(db_path, session_id):
    if not db_path or not os.path.exists(db_path):
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        if not _table_exists(conn, "events"):
            return []
        rows = conn.execute(
            "SELECT id, created_at, level, message, payload FROM events ORDER BY id"
        ).fetchall()
    items = []
    for row_id, created_at, level, message, payload_text in rows:
        payload = _json(payload_text)
        if session_id and payload.get("session_id", "") not in ("", session_id):
            continue
        items.append({
            "kind": "event",
            "sequence": row_id,
            "timestamp": created_at,
            "level": level,
            "message": message,
            "event_type": payload.get("event_type", ""),
            "order_id": payload.get("order_id", ""),
            "trade_no": payload.get("trade_no", ""),
            "status": payload.get("status", ""),
            "side": payload.get("side", ""),
            "instrument": payload.get("instrument", ""),
            "quantity": payload.get("quantity", None),
            "payload": payload,
        })
    return items


def _order_history_items(db_path, session_id):
    if not db_path or not os.path.exists(db_path):
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        if not _table_exists(conn, "order_history"):
            return []
        rows = conn.execute(
            """
            SELECT id, timestamp, instrument, option_type, action, order_type,
                   quantity, ordered_quantity, filled_quantity, pending_quantity,
                   cancelled_quantity, is_partial_fill, order_status,
                   entry_price, buy_score, exit_price, exit_reason,
                   target_price, stop_loss_price, ltp_at_order_placement,
                   zerodha_order_id, parent_order_id, related_trade_id,
                   error_reason, data
            FROM order_history
            WHERE (? = '' OR session_id = ?)
            ORDER BY id
            """,
            (session_id, session_id),
        ).fetchall()
    return [
        {
            "kind": "order_history",
            "sequence": row_id,
            "timestamp": timestamp,
            "instrument": instrument,
            "option_type": option_type,
            "action": action,
            "order_type": order_type,
            "quantity": quantity,
            "ordered_quantity": ordered_quantity,
            "filled_quantity": filled_quantity,
            "pending_quantity": pending_quantity,
            "cancelled_quantity": cancelled_quantity,
            "is_partial_fill": bool(is_partial_fill),
            "order_status": order_status,
            "status": order_status,
            "entry_price": entry_price,
            "buy_score": buy_score,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "target_price": target_price,
            "stop_loss_price": stop_loss_price,
            "ltp_at_order_placement": ltp_at_order_placement,
            "order_id": order_id,
            "parent_order_id": parent_order_id,
            "related_trade_id": related_trade_id,
            "error_reason": error_reason,
            "payload": _json(data),
        }
        for (
            row_id,
            timestamp,
            instrument,
            option_type,
            action,
            order_type,
            quantity,
            ordered_quantity,
            filled_quantity,
            pending_quantity,
            cancelled_quantity,
            is_partial_fill,
            order_status,
            entry_price,
            buy_score,
            exit_price,
            exit_reason,
            target_price,
            stop_loss_price,
            ltp_at_order_placement,
            order_id,
            parent_order_id,
            related_trade_id,
            error_reason,
            data,
        ) in rows
    ]


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _json(text):
    try:
        return json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Replay a tradebot session timeline from SQLite logs.")
    parser.add_argument("db_path")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--events-only", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", default="")
    parser.add_argument("--payload", action="store_true")
    args = parser.parse_args()

    replay = build_session_replay(
        args.db_path,
        session_id=args.session_id,
        include_order_history=not args.events_only,
    )
    if args.format == "json":
        output = json.dumps(replay, default=str, indent=2)
    else:
        output = "\n".join(format_replay_report(replay, include_payload=args.payload))

    if args.output:
        directory = os.path.dirname(args.output)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output)
            handle.write("\n")
    else:
        print(output)
