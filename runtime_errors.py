def classify_runtime_error(error, context=""):
    text = str(error or "").strip()
    lower = text.lower()
    context = str(context or "").strip().lower()
    if not text:
        return _classification("", "", False, False)

    if "unknown broker state" in lower or "reconciliation" in lower:
        return _classification("reconciliation_required", "UNKNOWN_BROKER_STATE", True, True)

    auth_patterns = (
        "api key",
        "access token",
        "authorization",
        "unauthorized",
        "forbidden",
        "invalid session",
        "session expired",
        "token is invalid",
        "403",
        "401",
    )
    if any(pattern in lower for pattern in auth_patterns):
        return _classification("auth", "BROKER_AUTH_ERROR", False, False)

    timeout_patterns = ("timed out", "timeout", "read timed", "504", "gateway timeout")
    if any(pattern in lower for pattern in timeout_patterns):
        if context in {"order", "order_placement", "entry_order", "exit_order"}:
            return _classification("unknown_broker_state", "UNKNOWN_BROKER_STATE", True, True)
        return _classification("timeout", "BROKER_TIMEOUT", True, False)

    network_patterns = (
        "connection",
        "network",
        "temporarily unavailable",
        "service unavailable",
        "unreachable",
        "connection refused",
        "connection reset",
        "max retries",
        "dns",
        "name resolution",
        "503",
    )
    if any(pattern in lower for pattern in network_patterns):
        return _classification("network", "BROKER_CONNECTION_ERROR", True, False)

    if "margin" in lower or ("insufficient" in lower and "fund" in lower):
        return _classification("margin", "BROKER_MARGIN_ERROR", False, False)

    rejected_patterns = ("rejected", "invalid", "not allowed", "rms", "blocked", "outside", "trigger")
    if any(pattern in lower for pattern in rejected_patterns):
        return _classification("rejected", "BROKER_REJECTED", False, False)

    if "zerodha not connected" in lower or "connect zerodha" in lower or "not connected" in lower:
        return _classification("auth", "LOCAL_VALIDATION_ERROR", False, False)

    return _classification("unknown", "BROKER_ERROR", False, False)


def _classification(category, error_class, retriable, requires_reconciliation):
    return {
        "category": category,
        "class": error_class,
        "retriable": bool(retriable),
        "requires_reconciliation": bool(requires_reconciliation),
    }
