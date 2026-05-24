from datetime import datetime

from fast_ohlcv_entry import backtest_limit_fill_status
from strategy import OPTION_ENTRY_REPORT_COLUMNS, option_score_calculation_details
from trailing_stop import calculate_trailing_stop, trailing_settings


class BacktestTradingCore:
    """Backtesting-only execution simulator.

    Paper and real-money runtime behavior lives in live_session.py. This class
    owns only historical OHLC backtest entry/exit simulation.
    """

    def __init__(self, engine):

        self.engine = engine
        self.mode = "BACKTEST"
        self.balance = 0
        self.lot_size = 0
        self.max_trades = 0

        self.trades = []
        self.entry_attempts = []
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

    def process(self, nifty, options, i, settings, order_handler=None):
        if self.start_balance is None:
            self.start_balance = self.balance
        if self.trade_count >= self.max_trades:
            return
        if self._trading_blocked(settings, nifty, i):
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
        entry_order_type = str(signal.get("entry_order_type", "MARKET") or "MARKET").upper()
        signal = self._apply_market_entry_limit_for_backtest(signal, settings)
        entry = signal["entry"]
        entry_order_type = str(signal.get("entry_order_type", "MARKET") or "MARKET").upper()
        limit_fill_status = ""
        if self.mode == "BACKTEST" and entry_order_type == "LIMIT":
            if signal.get("market_entry_limit_from_market"):
                fill_index = entry_index
                limit_fill_status = "FILLED"
            else:
                fill_mode = str(settings.get("backtest_limit_fill_mode", "CONSERVATIVE") or "CONSERVATIVE").upper()
                fill_index = entry_index + 1
                next_row = option.iloc[fill_index] if fill_index < len(option) else None
                limit_fill_status = backtest_limit_fill_status(next_row, entry, fill_mode)
            attempt = self._entry_attempt_row(signal, option, entry_index, settings, limit_fill_status)
            self.entry_attempts.append(attempt)
            if limit_fill_status != "FILLED":
                cooldown = int(settings.get("missed_limit_cooldown_candles", 0) or 0)
                if cooldown > 0:
                    self.engine.cooldown_until = max(self.engine.cooldown_until, i + cooldown)
                self.engine.last_skip_reason = f"buy_limit_{limit_fill_status.lower()}"
                return
            entry_index = fill_index
        exit_result = self._execute_backtest_exit(signal, option, entry_index, settings)
        target = exit_result["target_price"]
        stoploss = exit_result["initial_stoploss_price"]
        exit_index = exit_result["exit_index"]
        exit_price = exit_result["exit_price"]
        exit_reason = exit_result["exit_reason"]

        nifty_exit_index = self._index_at_or_after_time(option, self._row_time(option, exit_index), i)
        nifty_exit_index = self._index_at_or_after_time(nifty, self._row_time(option, exit_index), nifty_exit_index)
        self.engine.mark_trade_complete(nifty_exit_index)

        pnl = (exit_price - entry) * self.lot_size
        self.balance += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        if exit_reason in {"STOPLOSS", "STOPLOSS_SAME_CANDLE", "TRAILING_STOPLOSS"}:
            self.stoploss_trades += 1
            max_stoploss_trades = int(settings.get("max_stoploss_trades", 2) or 0)
            if max_stoploss_trades and self.stoploss_trades >= max_stoploss_trades:
                self.trading_blocked_reason = "stoploss_trade_limit_hit"
        order_status = "PAPER"
        order_id = ""
        if order_handler is not None:
            order_status, order_id = order_handler(signal, self.trade_count + 1)

        score_row = dict(signal.get("score_row", {}))
        if self.mode == "BACKTEST":
            score_row.update(option_score_calculation_details(score_row, settings))
        trade = {
            "Trade No": self.trade_count + 1,
            "Type": option_type,
            "Instrument": instrument,
            "ATM Trade": instrument,
            "Strike": signal.get("strike", ""),
            "Expiry": signal.get("expiry", ""),
            "Signal Time": self._row_time(nifty, i),
            "Entry Time": self._row_time(option, entry_index),
            "Entry": entry,
            "entry_index": entry_index,
            "entry_time": self._row_time(option, entry_index),
            "entry_price": entry,
            "Entry Type": signal.get("entry_type", entry_order_type),
            "Order Type": entry_order_type,
            "Entry Offset": signal.get("entry_offset", ""),
            "Buy Limit Price": entry if entry_order_type == "LIMIT" else "",
            "Limit Offset": signal.get("limit_offset", ""),
            "Limit Validity Seconds": signal.get("limit_validity_seconds", settings.get("buy_limit_validity_seconds", "")),
            "Limit Fill Status": limit_fill_status,
            "Profit Points": settings.get("profit_points", ""),
            "Safety Points": settings.get("safety_points", ""),
            "Target Price": target,
            "Stop Loss Price": stoploss,
            "target_price": target,
            "initial_stoploss_price": stoploss,
            "current_sl_price": exit_result["current_sl_price"],
            "trailing_sl_enabled": exit_result["trailing_sl_enabled"],
            "trailing_start_points": exit_result["trailing_start_points"],
            "trailing_step_points": exit_result["trailing_step_points"],
            "trailing_lock_points": exit_result["trailing_lock_points"],
            "trailing_modifications": exit_result["trailing_modifications"],
            "same_candle_target_ignored": exit_result["same_candle_target_ignored"],
            "entry_candle_high": exit_result["entry_candle_high"],
            "entry_candle_low": exit_result["entry_candle_low"],
            "Time Exit Candles": settings.get("time_exit", ""),
            "Cooldown": settings.get("cooldown", ""),
            "Bullish Threshold": settings.get("bullish_threshold", ""),
            "Bearish Threshold": settings.get("bearish_threshold", ""),
            "RSI Bull": settings.get("rsi_bull", ""),
            "RSI Bear": settings.get("rsi_bear", ""),
            "RSI Reversal Bullish": settings.get("rsi_reversal_bullish", ""),
            "RSI Reversal Bearish": settings.get("rsi_reversal_bearish", ""),
            "Bullish Reversal Condition": settings.get("bullish_reversal_condition", ""),
            "Bearish Reversal Condition": settings.get("bearish_reversal_condition", ""),
            "Buy Limit Score Low": settings.get("buy_limit_score_low", ""),
            "Market Entry Score": settings.get("market_entry_score", ""),
            "Entry Remark": signal.get("entry_remark", ""),
            "Exit Time": self._row_time(option, exit_index),
            "Exit": exit_price,
            "exit_index": exit_index,
            "exit_time": self._row_time(option, exit_index),
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "PnL": pnl,
            "Live PnL": pnl,
            "Final PnL": pnl,
            "Total PnL": self.balance,
            "Reason": exit_reason,
            "Remarks": exit_reason,
            "Order Status": order_status,
            "Order ID": order_id,
            "Early Score": score_row.get("Early Score", ""),
            "Buy Entry": score_row.get("Buy Entry", ""),
        }
        for column in OPTION_ENTRY_REPORT_COLUMNS:
            trade.setdefault(column, score_row.get(column, ""))
        self.trades.append(trade)
        self.trade_count += 1

    def _apply_market_entry_limit_for_backtest(self, signal, settings):
        if not self._enabled(settings.get("live_option_market_entry_as_limit_enabled")):
            return signal
        if str(signal.get("entry_order_type", "MARKET") or "MARKET").upper() != "MARKET":
            return signal

        try:
            entry = float(signal.get("entry"))
            buffer_points = float(settings.get("live_option_market_entry_limit_buffer_points", 2) or 2)
            profit_points = float(settings.get("profit_points", 0) or 0)
            safety_points = float(settings.get("safety_points", 0) or 0)
        except (TypeError, ValueError):
            return signal

        buffer_points = max(buffer_points, 0)
        effective_entry = entry + buffer_points
        updated = dict(signal)
        updated["entry"] = effective_entry
        updated["entry_order_type"] = "LIMIT"
        updated["entry_type"] = signal.get("entry_type") or "MARKET ENTRY"
        updated["entry_offset"] = buffer_points
        updated["limit_offset"] = buffer_points
        updated["target"] = effective_entry + profit_points
        updated["stoploss"] = effective_entry - safety_points
        updated["market_entry_limit_from_market"] = True
        updated["market_entry_limit_buffer_points"] = buffer_points
        return updated

    def _enabled(self, value):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in ("1", "true", "yes", "on", "enabled")

    def _execute_backtest_exit(self, signal, option, entry_index, settings):
        entry_price = float(signal["entry"])
        target_price = float(signal["target"])
        initial_stoploss_price = float(signal["stoploss"])
        current_sl_price = initial_stoploss_price
        trailing_config = trailing_settings(settings)
        trailing_modifications = []
        time_exit_candles = max(1, int(settings.get("time_exit", 10)))

        entry_row = option.iloc[entry_index]
        entry_candle_high = float(entry_row["high"])
        entry_candle_low = float(entry_row["low"])
        same_candle_target_ignored = entry_candle_high >= target_price

        exit_start_index = entry_index
        if entry_index == signal.get("signal_index") and entry_index + 1 < len(option):
            exit_start_index = entry_index + 1
        time_exit_index = min(exit_start_index + time_exit_candles, len(option) - 1)
        square_off_index = self._square_off_index(option, entry_index, time_exit_index, settings)
        forced_square_off = square_off_index is not None and square_off_index <= time_exit_index
        if forced_square_off:
            time_exit_index = square_off_index

        exit_index = time_exit_index
        exit_price = float(option.iloc[exit_index]["close"])
        exit_reason = "AUTO SQUARE OFF" if forced_square_off else "TIME_EXIT"

        if entry_candle_low <= current_sl_price:
            return {
                "target_price": target_price,
                "initial_stoploss_price": initial_stoploss_price,
                "current_sl_price": current_sl_price,
                "trailing_sl_enabled": trailing_config["enabled"],
                "trailing_start_points": trailing_config["start_points"],
                "trailing_step_points": trailing_config["step_points"],
                "trailing_lock_points": trailing_config["lock_points"],
                "trailing_modifications": trailing_modifications,
                "same_candle_target_ignored": same_candle_target_ignored,
                "entry_candle_high": entry_candle_high,
                "entry_candle_low": entry_candle_low,
                "exit_index": entry_index,
                "exit_price": current_sl_price,
                "exit_reason": "STOPLOSS_SAME_CANDLE",
            }

        for j in range(entry_index + 1, time_exit_index + 1):
            row = option.iloc[j]
            open_price = float(row.get("open", row.get("close", 0)))
            high_price = float(row["high"])
            low_price = float(row["low"])
            trailing_update = calculate_trailing_stop(entry_price, current_sl_price, high_price, settings)
            if trailing_update:
                old_sl = current_sl_price
                current_sl_price = float(trailing_update["new_sl_price"])
                trailing_modifications.append({
                    "timestamp": self._row_time(option, j),
                    "old_sl_price": old_sl,
                    "new_sl_price": current_sl_price,
                    "ltp_at_modification": high_price,
                    "unrealized_profit_points": trailing_update["profit"],
                    "modify_status": "BACKTEST",
                })

            target_touched = high_price >= target_price
            stop_touched = low_price <= current_sl_price
            if target_touched and stop_touched:
                exit_reason = self._same_candle_exit_reason(
                    open_price,
                    target_price,
                    current_sl_price,
                    initial_stoploss_price,
                    settings,
                )
                exit_index = j
                exit_price = target_price if exit_reason == "TARGET" else current_sl_price
                break
            if target_touched:
                exit_index = j
                exit_price = target_price
                exit_reason = "TARGET"
                break
            if stop_touched:
                exit_reason = "TRAILING_STOPLOSS" if current_sl_price > initial_stoploss_price else "STOPLOSS"
                exit_index = j
                exit_price = current_sl_price
                break

        return {
            "target_price": target_price,
            "initial_stoploss_price": initial_stoploss_price,
            "current_sl_price": current_sl_price,
            "trailing_sl_enabled": trailing_config["enabled"],
            "trailing_start_points": trailing_config["start_points"],
            "trailing_step_points": trailing_config["step_points"],
            "trailing_lock_points": trailing_config["lock_points"],
            "trailing_modifications": trailing_modifications,
            "same_candle_target_ignored": same_candle_target_ignored,
            "entry_candle_high": entry_candle_high,
            "entry_candle_low": entry_candle_low,
            "exit_index": exit_index,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
        }

    def _same_candle_exit_reason(self, open_price, target_price, current_sl_price, initial_stoploss_price, settings):
        distance_to_sl = abs(float(open_price) - float(current_sl_price))
        distance_to_target = abs(float(target_price) - float(open_price))
        if distance_to_sl < distance_to_target:
            return "TRAILING_STOPLOSS" if current_sl_price > initial_stoploss_price else "STOPLOSS"
        mode = str(settings.get("backtest_same_candle_exit_mode", "CONSERVATIVE") or "CONSERVATIVE").upper()
        if mode == "OPTIMISTIC":
            return "TARGET"
        return "TRAILING_STOPLOSS" if current_sl_price > initial_stoploss_price else "STOPLOSS"

    def _entry_attempt_row(self, signal, option, entry_index, settings, limit_fill_status):
        score_row = dict(signal.get("score_row", {}))
        row = option.iloc[entry_index] if entry_index < len(option) else {}
        return {
            "Mode": self.mode,
            "Symbol": signal.get("instrument", ""),
            "Option Type": signal.get("type", ""),
            "Candle Time": self._row_time(option, entry_index),
            "Signal Time": self._row_time(option, signal.get("signal_index", entry_index)),
            "Entry Type": signal.get("entry_type", ""),
            "Order Type": signal.get("entry_order_type", ""),
            "Entry Price": signal.get("entry", ""),
            "Buy Limit Price": signal.get("entry", "") if signal.get("entry_order_type") == "LIMIT" else "",
            "Limit Offset": signal.get("limit_offset", ""),
            "Limit Validity Seconds": signal.get("limit_validity_seconds", settings.get("buy_limit_validity_seconds", "")),
            "Open": row.get("open", ""),
            "High": row.get("high", ""),
            "Low": row.get("low", ""),
            "Close": row.get("close", ""),
            "Volume": row.get("volume", ""),
            "FinalDecision": score_row.get("Final Decision", ""),
            "DecisionReason": score_row.get("Decision Reason", ""),
            **{column: score_row.get(column, "") for column in OPTION_ENTRY_REPORT_COLUMNS},
            "Limit Fill Status": limit_fill_status,
        }

    def _trading_blocked(self, settings, nifty=None, index=None):
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
        elif self._square_off_reached(settings, nifty, index):
            self.trading_blocked_reason = "square_off_time_reached"
        return bool(self.trading_blocked_reason)

    def _square_off_index(self, option, start_index, end_index, settings):
        cutoff = self._square_off_cutoff(settings)
        if cutoff is None:
            return None
        for index in range(start_index, end_index + 1):
            row_time = self._row_datetime(option, index)
            if row_time is not None and row_time.time() >= cutoff:
                return index
        return None

    def _square_off_reached(self, settings, frame, index):
        cutoff = self._square_off_cutoff(settings)
        if cutoff is None or frame is None or index is None:
            return False
        row_time = self._row_datetime(frame, index)
        return bool(row_time is not None and row_time.time() >= cutoff)

    def _square_off_cutoff(self, settings):
        text = str(settings.get("square_off_time", "") or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%H:%M").time()
        except ValueError:
            return None

    def _row_datetime(self, df, index):
        import pandas as pd

        if df is None or index is None or index < 0 or index >= len(df):
            return None
        row = df.iloc[index]
        if "datetime" in row:
            value = pd.to_datetime(row.get("datetime"), errors="coerce")
            if not pd.isna(value):
                return value.to_pydatetime()
        date = row.get("date", "") if "date" in row else ""
        time = row.get("time", "") if "time" in row else ""
        if date and time:
            value = pd.to_datetime(f"{date} {time}", errors="coerce")
            if not pd.isna(value):
                return value.to_pydatetime()
        return None
