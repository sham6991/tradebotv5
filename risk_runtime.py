from event_logger import KILL_SWITCH_ACTIVATED


class RiskRuntimeMixin:
    def _sync_risk_state_from_guard(self):
        self.daily_start_balance = self.risk_guard.daily_start_balance
        self.consecutive_losses = self.risk_guard.consecutive_losses
        self.stoploss_trades = self.risk_guard.stoploss_trades
        self.trading_blocked_reason = self.risk_guard.blocked_reason

    def activate_kill_switch(self, reason="Manual kill switch"):
        with self.state_lock:
            blocked_reason = self.risk_guard.activate_kill_switch(reason)
            self._sync_risk_state_from_guard()
            self._save_kill_switch_state()
            self._emit_alert(
                "CRITICAL",
                KILL_SWITCH_ACTIVATED,
                blocked_reason,
                {"reason": reason, "blocked_reason": blocked_reason},
            )
            self._log_lifecycle_event(
                KILL_SWITCH_ACTIVATED,
                "CRITICAL",
                blocked_reason,
                status="BLOCKED",
                payload={"reason": reason},
            )
            self._emit_session_history_event(
                action="KILL SWITCH",
                order_status="BLOCKED",
                exit_reason=blocked_reason,
                error_reason=reason,
            )
            return blocked_reason

    def _trading_blocked(self):
        blocked, _reason = self.risk_guard.is_blocked(self.balance)
        self._sync_risk_state_from_guard()
        return blocked

    def _square_off_time_reached(self):
        return self.risk_guard.square_off_time_reached()
