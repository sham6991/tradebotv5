class TradingCore:

    def __init__(self, engine, mode="LIVE"):

        self.engine = engine
        self.mode = mode
        self.balance = 0
        self.lot_size = 0
        self.max_trades = 0

        self.trades = []
        self.trade_count = 0
        self.start_balance = None
        self.consecutive_losses = 0
        self.stoploss_trades = 0
        self.trading_blocked_reason = ""

    def _row_time(self, df, index):
        import pandas as pd
        from reporting import format_datetime_value
        def present(value):
            return not pd.isna(value) and str(value).strip().lower() not in ("", "nan", "nat", "none")

        if index >= len(df):
            return ""

        row = df.iloc[index]

        date = row["date"] if "date" in row and present(row["date"]) else ""
        time = row["time"] if "time" in row and present(row["time"]) else ""

        if date and time:
            return format_datetime_value(f"{date} {time}")

        if "datetime" in row and present(row["datetime"]):
            return format_datetime_value(row["datetime"])

        return format_datetime_value(date) if date else (time or index)

    def _index_at_or_after_time(self, df, timestamp, fallback_index):
        import pandas as pd

        if timestamp is None or "datetime" not in df.columns:
            return fallback_index
        wanted = pd.to_datetime(timestamp, errors="coerce")
        if pd.isna(wanted):
            return fallback_index
        times = pd.to_datetime(df["datetime"], errors="coerce")
        matches = times[times >= wanted]
        if matches.empty:
            return fallback_index
        return int(matches.index[0])

    # ==========================================
    # COMMON PROCESS (USED BY BOTH LIVE & BACKTEST)
    # ==========================================

    def process(self, nifty, options, i, settings, order_handler=None):
        if self.start_balance is None:
            self.start_balance = self.balance
        if self.trade_count >= self.max_trades:
            return
        if self._trading_blocked(settings):
            self.engine.last_skip_reason = self.trading_blocked_reason
            return
        signal = self.engine.find_trade(nifty, options, i, settings)
        if signal is None:
            return

        option = signal["option"]
        option_type = signal["type"]
        instrument = signal.get("instrument", option_type)
        entry_index = signal["entry_index"]
        if entry_index >= len(option):
            return

        entry = signal["entry"]
        target = signal["target"]
        stoploss = signal["stoploss"]
        time_exit_candles = max(1, int(settings.get("time_exit", 10)))
        exit_index = min(entry_index + time_exit_candles, len(option) - 1)
        exit_price = float(option.iloc[exit_index]["close"])
        exit_reason = "TIME EXIT"

        for j in range(entry_index, min(len(option), entry_index + time_exit_candles + 1)):
            row = option.iloc[j]
            high_price = float(row["high"])
            low_price = float(row["low"])
            if high_price >= target:
                exit_index = j
                exit_price = target
                exit_reason = "TARGET"
                break
            if low_price <= stoploss:
                exit_index = j
                exit_price = stoploss
                exit_reason = "STOPLOSS"
                break

        nifty_exit_index = self._index_at_or_after_time(option, self._row_time(option, exit_index), i)
        nifty_exit_index = self._index_at_or_after_time(nifty, self._row_time(option, exit_index), nifty_exit_index)
        self.engine.mark_trade_complete(nifty_exit_index)

        pnl = (exit_price - entry) * self.lot_size
        self.balance += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        if exit_reason == "STOPLOSS":
            self.stoploss_trades += 1
            max_stoploss_trades = int(settings.get("max_stoploss_trades", 2) or 0)
            if max_stoploss_trades and self.stoploss_trades >= max_stoploss_trades:
                self.trading_blocked_reason = "stoploss_trade_limit_hit"
        order_status = "PAPER"
        order_id = ""
        if order_handler is not None:
            order_status, order_id = order_handler(signal, self.trade_count + 1)

        self.trades.append({
            "Trade No": self.trade_count + 1,
            "Type": option_type,
            "Instrument": instrument,
            "ATM Trade": instrument,
            "Strike": signal.get("strike", ""),
            "Expiry": signal.get("expiry", ""),
            "Signal Time": self._row_time(nifty, i),
            "Entry Time": self._row_time(option, entry_index),
            "Entry": entry,
            "Entry Offset": signal.get("entry_offset", ""),
            "Profit Points": settings.get("profit_points", ""),
            "Safety Points": settings.get("safety_points", ""),
            "Time Exit Candles": settings.get("time_exit", ""),
            "Cooldown": settings.get("cooldown", ""),
            "Bullish Threshold": settings.get("bullish_threshold", ""),
            "Bearish Threshold": settings.get("bearish_threshold", ""),
            "RSI Bull": settings.get("rsi_bull", ""),
            "RSI Bear": settings.get("rsi_bear", ""),
            "RSI Reversal Bullish": settings.get("rsi_reversal_bullish", ""),
            "RSI Reversal Bearish": settings.get("rsi_reversal_bearish", ""),
            "Min Buy Score": settings.get("min_buy_score", ""),
            "Entry Remark": signal.get("entry_remark", ""),
            "Exit Time": self._row_time(option, exit_index),
            "Exit": exit_price,
            "PnL": pnl,
            "Live PnL": pnl,
            "Final PnL": pnl,
            "Total PnL": self.balance,
            "Reason": exit_reason,
            "Remarks": exit_reason,
            "Order Status": order_status,
            "Order ID": order_id,
            "Buy Score": signal.get("score_row", {}).get("Buy Score", ""),
            "Buy Entry": signal.get("score_row", {}).get("Buy Entry", ""),
        })
        self.trade_count += 1

    def _trading_blocked(self, settings):
        if self.trading_blocked_reason:
            return True
        pnl = self.balance - (self.start_balance or self.balance)
        max_loss = float(settings.get("max_daily_loss", 0) or 0)
        max_profit = float(settings.get("max_daily_profit", 0) or 0)
        max_losses = int(settings.get("max_consecutive_losses", 0) or 0)
        max_stoploss_trades = int(settings.get("max_stoploss_trades", 2) or 0)
        if max_loss and pnl <= -abs(max_loss):
            self.trading_blocked_reason = "daily_loss_limit_hit"
        elif max_profit and pnl >= abs(max_profit):
            self.trading_blocked_reason = "daily_profit_target_hit"
        elif max_losses and self.consecutive_losses >= max_losses:
            self.trading_blocked_reason = "consecutive_loss_limit_hit"
        elif max_stoploss_trades and self.stoploss_trades >= max_stoploss_trades:
            self.trading_blocked_reason = "stoploss_trade_limit_hit"
        return bool(self.trading_blocked_reason)
