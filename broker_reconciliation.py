import sys
from datetime import datetime

from event_logger import RECONCILIATION_ERROR, RECONCILIATION_WARNING
from position_reconciler import PositionReconciler


def _position_reconciler_class():
    facade = sys.modules.get("execution_v2")
    if facade is not None:
        return getattr(facade, "PositionReconciler", PositionReconciler)
    return PositionReconciler


class BrokerReconciliationMixin:
    def _reconcile_startup_state(self):
        if self.mode != "LIVE" or not self.zerodha:
            return []
        reconciler = _position_reconciler_class()(self.orders)
        findings = reconciler.reconcile(self.open_position, self.pending_entry)
        self.startup_reconciliation_findings = findings
        for finding in findings:
            level = finding.get("level", "WARN")
            event_type = RECONCILIATION_ERROR if level == "ERROR" else RECONCILIATION_WARNING
            self._log_lifecycle_event(
                event_type,
                level,
                f"Startup reconciliation: {finding.get('message', '')}",
                order_id=finding.get("order_id", ""),
                trade_no=finding.get("trade_no", ""),
                status=finding.get("status", ""),
                instrument=(finding.get("context") or {}).get("instrument", ""),
                payload=finding,
            )
        if findings:
            self._emit_live_log_update({
                "Session Trade No": "",
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Instrument / Symbol": "STARTUP RECONCILIATION",
                "Option Type": "",
                "Action": "RECONCILE",
                "Order Type": "",
                "Quantity": "",
                "Order Status": findings[0].get("status", ""),
                "Entry Price": "",
                "Early Score": "",
                "Exit Price": "",
                "Exit Reason": findings[0].get("message", ""),
                "Target Price": "",
                "Stop Loss Price": "",
                "LTP at Order Placement": "",
                "Zerodha Order ID": findings[0].get("order_id", ""),
                "Parent Order ID": "",
                "Related Trade ID": findings[0].get("trade_no", ""),
                "Error / Rejection Reason": "; ".join(item.get("code", "") for item in findings),
            })
        error_codes = [finding.get("code", "") for finding in findings if finding.get("level") == "ERROR"]
        if error_codes:
            self._emit_alert(
                "ERROR",
                RECONCILIATION_ERROR,
                f"Startup reconciliation error: {', '.join(error_codes)}",
                {"error_codes": error_codes, "findings": findings},
            )
            self.activate_kill_switch(f"Startup reconciliation error: {', '.join(error_codes)}")
        return findings
