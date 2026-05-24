ENTRY_PENDING = "ENTRY_PENDING"
ENTRY_OPEN = "ENTRY_OPEN"
ENTRY_FILLED = "ENTRY_FILLED"
ENTRY_PARTIAL = "ENTRY_PARTIAL"
ENTRY_REJECTED = "ENTRY_REJECTED"
ENTRY_CANCELLED_EMPTY = "ENTRY_CANCELLED_EMPTY"
ENTRY_CANCELLED_PARTIAL = "ENTRY_CANCELLED_PARTIAL"
EXIT_PENDING = "EXIT_PENDING"
EXIT_FILLED = "EXIT_FILLED"
EXIT_PARTIAL = "EXIT_PARTIAL"
EXIT_REJECTED = "EXIT_REJECTED"
UNKNOWN = "UNKNOWN"


PENDING_STATUSES = {
    "PENDING",
    "OPEN PENDING",
    "OPEN_PENDING",
    "MODIFY PENDING",
    "MODIFY_PENDING",
    "MODIFY VALIDATION PENDING",
    "MODIFY_VALIDATION_PENDING",
    "CANCEL PENDING",
    "CANCEL_PENDING",
    "VALIDATION PENDING",
    "VALIDATION_PENDING",
    "PUT ORDER REQ RECEIVED",
    "PUT_ORDER_REQ_RECEIVED",
}
OPEN_STATUSES = {"OPEN", "TRIGGER PENDING", "TRIGGER_PENDING"}
FILLED_STATUSES = {"COMPLETE", "FILLED"}
REJECTED_STATUSES = {"REJECTED"}
CANCELLED_STATUSES = {"CANCELLED", "CANCELED"}


def normalize_order_status(status):
    text = str(status or "").strip().upper()
    if not text:
        return "UNKNOWN"
    text = text.replace("_", " ")
    if text.startswith("PAPER"):
        return "COMPLETE"
    if text.startswith("FAILED"):
        return "REJECTED"
    padded = f" {text} "
    if ("SL-M" in text or " SL " in padded) and "PLACED" in text:
        return "TRIGGER PENDING"
    if "PLACED" in text:
        return "OPEN"
    if "COMPLETE" in text or "FILLED" in text:
        return "COMPLETE"
    if "REJECT" in text:
        return "REJECTED"
    if "CANCEL PENDING" in text:
        return "CANCEL PENDING"
    if "CANCEL" in text:
        return "CANCELLED"
    return text


def classify_order_state(order, role="ENTRY"):
    role = str(role or "ENTRY").strip().upper()
    status = normalize_order_status(_get(order, "status", "order_status"))
    quantity = _int_value(_get(order, "quantity", "ordered_quantity"), 0)
    filled_quantity = _int_value(_get(order, "filled_quantity", "filled"), 0)
    pending_quantity = _int_value(_get(order, "pending_quantity", "pending"), None)
    if pending_quantity is None:
        pending_quantity = max(quantity - filled_quantity, 0) if quantity else 0

    is_entry = role != "EXIT"
    state = UNKNOWN
    if status in PENDING_STATUSES:
        state = ENTRY_PENDING if is_entry else EXIT_PENDING
    elif status in OPEN_STATUSES:
        if filled_quantity > 0:
            state = ENTRY_PARTIAL if is_entry else EXIT_PARTIAL
        else:
            state = ENTRY_OPEN if is_entry else EXIT_PENDING
    elif status in FILLED_STATUSES:
        if filled_quantity > 0 or quantity <= 0:
            state = ENTRY_FILLED if is_entry else EXIT_FILLED
        else:
            state = UNKNOWN
    elif status in REJECTED_STATUSES:
        state = ENTRY_REJECTED if is_entry else EXIT_REJECTED
    elif status in CANCELLED_STATUSES:
        if is_entry:
            state = ENTRY_CANCELLED_PARTIAL if filled_quantity > 0 else ENTRY_CANCELLED_EMPTY
        else:
            state = EXIT_PARTIAL if filled_quantity > 0 else UNKNOWN

    terminal = state in {
        ENTRY_FILLED,
        ENTRY_REJECTED,
        ENTRY_CANCELLED_EMPTY,
        ENTRY_CANCELLED_PARTIAL,
        EXIT_FILLED,
        EXIT_REJECTED,
    } or status in CANCELLED_STATUSES
    filled = state in {ENTRY_FILLED, EXIT_FILLED}
    partial = state in {ENTRY_PARTIAL, ENTRY_CANCELLED_PARTIAL, EXIT_PARTIAL}
    active = state in {ENTRY_PENDING, ENTRY_OPEN, ENTRY_PARTIAL, EXIT_PENDING, EXIT_PARTIAL}
    safely_inactive = status in CANCELLED_STATUSES and filled_quantity <= 0
    requires_reconciliation = state == UNKNOWN and not safely_inactive

    return {
        "state": state,
        "status": status,
        "role": "ENTRY" if is_entry else "EXIT",
        "quantity": quantity,
        "filled_quantity": filled_quantity,
        "pending_quantity": pending_quantity,
        "is_active": active,
        "is_terminal": terminal,
        "is_filled": filled,
        "is_partial": partial,
        "is_safely_inactive": safely_inactive,
        "requires_reconciliation": requires_reconciliation,
    }


def _get(mapping, *keys):
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        if key in mapping:
            return mapping.get(key)
    return ""


def _int_value(value, fallback=0):
    if value in ("", None):
        return fallback
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback
