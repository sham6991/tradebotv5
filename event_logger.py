from typing import Any

ORDER_PLACED = "ORDER_PLACED"
ORDER_OPEN = "ORDER_OPEN"
ORDER_PARTIAL_FILL = "ORDER_PARTIAL_FILL"
ORDER_COMPLETE = "ORDER_COMPLETE"
ORDER_REJECTED = "ORDER_REJECTED"
ORDER_CANCELLED = "ORDER_CANCELLED"
ENTRY_FILLED = "ENTRY_FILLED"
PROTECTIVE_ORDER_PLACED = "PROTECTIVE_ORDER_PLACED"
PARTIAL_EXIT_DETECTED = "PARTIAL_EXIT_DETECTED"
KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"
RECONCILIATION_WARNING = "RECONCILIATION_WARNING"
RECONCILIATION_ERROR = "RECONCILIATION_ERROR"

KNOWN_EVENT_TYPES = {
    ORDER_PLACED,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    ORDER_COMPLETE,
    ORDER_REJECTED,
    ORDER_CANCELLED,
    ENTRY_FILLED,
    PROTECTIVE_ORDER_PLACED,
    PARTIAL_EXIT_DETECTED,
    KILL_SWITCH_ACTIVATED,
    RECONCILIATION_WARNING,
    RECONCILIATION_ERROR,
}

SCHEMA_VERSION = 1


def normalize_event(
    event_type,
    level,
    message,
    session_id="",
    order_id="",
    trade_no: Any = "",
    status="",
    side="",
    instrument="",
    quantity: Any = None,
    payload: Any = None,
    source="",
):
    event_type = str(event_type or "").strip().upper()
    if not event_type:
        raise ValueError("event_type is required")

    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "known_event_type": event_type in KNOWN_EVENT_TYPES,
        "level": str(level or "INFO").strip().upper(),
        "message": str(message or ""),
        "session_id": str(session_id or ""),
        "order_id": str(order_id or ""),
        "trade_no": trade_no if trade_no is not None else "",
        "status": str(status or ""),
        "side": str(side or ""),
        "instrument": str(instrument or ""),
        "quantity": quantity,
        "source": str(source or ""),
        "payload": payload or {},
    }


class StructuredEventLogger:
    def __init__(self, store, session_id="", source=""):
        self.store = store
        self.session_id = session_id
        self.source = source

    def log(
        self,
        event_type,
        level,
        message,
        order_id="",
        trade_no: Any = "",
        status="",
        side="",
        instrument="",
        quantity: Any = None,
        payload: Any = None,
    ):
        event = normalize_event(
            event_type,
            level,
            message,
            session_id=self.session_id,
            order_id=order_id,
            trade_no=trade_no,
            status=status,
            side=side,
            instrument=instrument,
            quantity=quantity,
            payload=payload,
            source=self.source,
        )
        if self.store:
            self.store.log_event(event["level"], event["message"], event)
        return event
