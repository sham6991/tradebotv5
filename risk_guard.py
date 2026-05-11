from datetime import datetime


class LiveRiskGuard:
    def __init__(self, settings, starting_balance=0, now_provider=None):
        self.settings = settings or {}
        self.daily_start_balance = float(starting_balance or 0)
        self.consecutive_losses = 0
        self.blocked_reason = ""
        self.kill_switch_active = False
        self.kill_switch_reason = ""
        self.now_provider = now_provider or datetime.now

    def activate_kill_switch(self, reason="Manual kill switch"):
        self.kill_switch_active = True
        self.kill_switch_reason = str(reason or "Manual kill switch")
        self.blocked_reason = f"KILL SWITCH ACTIVE: {self.kill_switch_reason}"
        return self.blocked_reason

    def restore_kill_switch(self, active=False, reason=""):
        self.kill_switch_active = bool(active)
        self.kill_switch_reason = str(reason or "")
        if self.kill_switch_active:
            self.blocked_reason = f"KILL SWITCH ACTIVE: {self.kill_switch_reason or 'Restored session state'}"

    def record_trade_result(self, pnl):
        if float(pnl or 0) < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def is_blocked(self, current_balance):
        if self.kill_switch_active:
            self.blocked_reason = f"KILL SWITCH ACTIVE: {self.kill_switch_reason or 'Trading disabled'}"
            return True, self.blocked_reason
        if self.blocked_reason:
            return True, self.blocked_reason

        pnl = float(current_balance or 0) - self.daily_start_balance
        max_loss = float(self.settings.get("max_daily_loss", 0) or 0)
        max_profit = float(self.settings.get("max_daily_profit", 0) or 0)
        max_losses = int(self.settings.get("max_consecutive_losses", 0) or 0)

        if max_loss and pnl <= -abs(max_loss):
            self.blocked_reason = "DAILY LOSS LIMIT HIT"
        elif max_profit and pnl >= abs(max_profit):
            self.blocked_reason = "DAILY PROFIT TARGET HIT"
        elif max_losses and self.consecutive_losses >= max_losses:
            self.blocked_reason = "CONSECUTIVE LOSS LIMIT HIT"
        elif self.square_off_time_reached():
            self.blocked_reason = "SQUARE OFF TIME REACHED"

        return bool(self.blocked_reason), self.blocked_reason

    def square_off_time_reached(self):
        text = str(self.settings.get("square_off_time", "") or "").strip()
        if not text:
            return False
        try:
            cutoff = datetime.strptime(text, "%H:%M").time()
        except ValueError:
            return False
        return self.now_provider().time() >= cutoff
