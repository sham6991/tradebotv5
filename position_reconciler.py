class PositionReconciler:
    ACTIVE_STATUSES = {"OPEN", "PENDING", "TRIGGER PENDING"}
    FILLED_STATUSES = {"COMPLETE", "FILLED"}
    TERMINAL_STATUSES = {"COMPLETE", "FILLED", "CANCELLED", "REJECTED"}

    def __init__(self, order_manager):
        self.orders = order_manager

    def reconcile(self, open_position=None, pending_entry=None):
        findings = []
        if pending_entry:
            findings.extend(self._reconcile_pending_entry(pending_entry))
        if open_position:
            findings.extend(self._reconcile_open_position(open_position))
        return findings

    def _finding(self, level, code, message, order_id="", trade_no="", status="", context=None):
        return {
            "level": level,
            "code": code,
            "message": message,
            "order_id": str(order_id or ""),
            "trade_no": trade_no,
            "status": status,
            "context": context or {},
        }

    def _status(self, order_id):
        if not order_id:
            return ""
        return str(self.orders.order_status(order_id, fallback="UNKNOWN") or "UNKNOWN").upper()

    def _reconcile_pending_entry(self, pending):
        findings = []
        order_id = pending.get("order_id", "")
        signal = pending.get("signal", {}) or {}
        if not order_id:
            return [
                self._finding(
                    "WARN",
                    "PENDING_ENTRY_MISSING_ORDER_ID",
                    "Local pending entry has no broker order id.",
                    trade_no=signal.get("trade_no", ""),
                    context={"instrument": signal.get("instrument", "")},
                )
            ]

        status = self._status(order_id)
        if status == "UNKNOWN":
            findings.append(
                self._finding(
                    "WARN",
                    "PENDING_ENTRY_ORDER_UNKNOWN",
                    "Local pending entry order was not found at broker.",
                    order_id=order_id,
                    status=status,
                    context={"instrument": signal.get("instrument", "")},
                )
            )
        elif status in self.FILLED_STATUSES:
            findings.append(
                self._finding(
                    "WARN",
                    "PENDING_ENTRY_ALREADY_FILLED",
                    "Local pending entry appears filled at broker and should be reviewed before new entries.",
                    order_id=order_id,
                    status=status,
                    context={"instrument": signal.get("instrument", "")},
                )
            )
        elif status in {"CANCELLED", "REJECTED"}:
            findings.append(
                self._finding(
                    "WARN",
                    "PENDING_ENTRY_TERMINAL",
                    "Local pending entry is terminal at broker and local state may be stale.",
                    order_id=order_id,
                    status=status,
                    context={"instrument": signal.get("instrument", "")},
                )
            )
        return findings

    def _reconcile_open_position(self, position):
        findings = []
        signal = position.get("signal", {}) or {}
        trade_no = position.get("trade_no", "")
        entry_order_id = position.get("entry_order_id", "")
        target_order_id = position.get("target_order_id", "")
        stoploss_order_id = position.get("stoploss_order_id", "")

        if entry_order_id:
            entry_status = self._status(entry_order_id)
            if entry_status in {"CANCELLED", "REJECTED", "UNKNOWN"}:
                findings.append(
                    self._finding(
                        "ERROR",
                        "OPEN_POSITION_ENTRY_NOT_CONFIRMED",
                        "Local open position exists, but entry order is not confirmed at broker.",
                        order_id=entry_order_id,
                        trade_no=trade_no,
                        status=entry_status,
                        context={"instrument": signal.get("instrument", "")},
                    )
                )

        if not target_order_id and not stoploss_order_id:
            findings.append(
                self._finding(
                    "WARN",
                    "PROTECTIVE_ORDERS_MISSING",
                    "Local open position has no target or stoploss order ids.",
                    trade_no=trade_no,
                    context={"instrument": signal.get("instrument", "")},
                )
            )
            return findings

        if not target_order_id:
            findings.append(
                self._finding(
                    "WARN",
                    "TARGET_ORDER_MISSING",
                    "Local open position has no target order id.",
                    trade_no=trade_no,
                    context={"instrument": signal.get("instrument", "")},
                )
            )
        else:
            findings.extend(self._reconcile_exit_order(position, target_order_id, "TARGET"))

        if not stoploss_order_id:
            findings.append(
                self._finding(
                    "WARN",
                    "STOPLOSS_ORDER_MISSING",
                    "Local open position has no stoploss order id.",
                    trade_no=trade_no,
                    context={"instrument": signal.get("instrument", "")},
                )
            )
        else:
            findings.extend(self._reconcile_exit_order(position, stoploss_order_id, "STOPLOSS"))

        return findings

    def _reconcile_exit_order(self, position, order_id, label):
        status = self._status(order_id)
        signal = position.get("signal", {}) or {}
        trade_no = position.get("trade_no", "")
        if status == "UNKNOWN":
            return [
                self._finding(
                    "WARN",
                    f"{label}_ORDER_UNKNOWN",
                    f"{label.title()} order id was not found at broker.",
                    order_id=order_id,
                    trade_no=trade_no,
                    status=status,
                    context={"instrument": signal.get("instrument", "")},
                )
            ]
        if status in self.FILLED_STATUSES:
            return [
                self._finding(
                    "ERROR",
                    f"{label}_ORDER_ALREADY_FILLED",
                    f"{label.title()} order appears filled at broker. Local position should be reviewed before trading.",
                    order_id=order_id,
                    trade_no=trade_no,
                    status=status,
                    context={"instrument": signal.get("instrument", "")},
                )
            ]
        if status in {"CANCELLED", "REJECTED"}:
            return [
                self._finding(
                    "WARN",
                    f"{label}_ORDER_TERMINAL",
                    f"{label.title()} order is terminal at broker and local protection may be stale.",
                    order_id=order_id,
                    trade_no=trade_no,
                    status=status,
                    context={"instrument": signal.get("instrument", "")},
                )
            ]
        return []
