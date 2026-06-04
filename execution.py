import time
import os
import sqlite3
from datetime import datetime, timedelta
import pandas as pd

from config import LOT_SIZE
from engine import TradingEngine
from indicators import clean_and_add_indicators
from reporting import ensure_risk_engine_schema
from trading_core import TradingCore
from zerodha_client import ZerodhaClient


class LivePaperSession:

    def __init__(
        self,
        nifty,
        option_dfs,
        token_map,
        settings,
        save_path=None,
        on_trade=None,
        mode="PAPER",
        zerodha=None
    ):
        self.nifty = nifty
        self.options = option_dfs
        self.token_map = {int(k): v for k, v in token_map.items()}
        self.settings = settings
        self.save_path = save_path
        self.on_trade = on_trade
        self.mode = mode
        self.zerodha = zerodha

        self.engine = TradingEngine(
            settings.get("cooldown", 0)
        )

        self.balance = settings.get("balance", 0)
        self.lots = settings["lot_size"]

        self.fallback_quantity = self.lots * LOT_SIZE

        self.max_trades = settings["max_trades"]

        # INCREASED HOLD TIME
        self.max_hold_ticks = settings.get("max_hold_ticks", 50)

        self.trades = []
        self.trade_count = 0
        self.open_position = None
        self.session_id = f"{self.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.sqlite_session_initialized = False

        self.last_signal_index = min(
            len(self.nifty),
            *[len(option) for option in self.options]
        ) - 1

    def on_ticks(self, ticks):

        changed = False

        for tick in ticks:

            token = int(tick.get("instrument_token", 0))
            price = tick.get("last_price")

            if token not in self.token_map or price is None:
                continue

            target = self.token_map[token]

            row = self._tick_to_row(tick, price)

            if target == "NIFTY":

                self.nifty = self._append_tick_row(
                    self.nifty,
                    row
                )

            elif str(target).startswith("OPTION_"):

                option_index = int(
                    str(target).split("_")[1]
                )

                self.options[option_index] = (
                    self._append_tick_row(
                        self.options[option_index],
                        row
                    )
                )

            changed = True

        if changed:
            self._process_live_tick()

    def _tick_to_row(self, tick, price):

        timestamp = (
            tick.get("exchange_timestamp")
            or tick.get("last_trade_time")
            or datetime.now()
        )

        return {
            "datetime": timestamp,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": (
                tick.get("volume_traded")
                or tick.get("last_traded_quantity")
                or 0
            )
        }

    def _append_tick_row(self, df, row):

        attrs = dict(df.attrs)

        df = pd.concat(
            [df, pd.DataFrame([row])],
            ignore_index=True
        )

        df = clean_and_add_indicators(df)

        df.attrs.update(attrs)

        return df

    def _process_live_tick(self):

        min_len = min(
            len(self.nifty),
            *[len(option) for option in self.options]
        )

        i = min_len - 1

        if i <= 0:
            return

        # CHECK EXIT FIRST
        if self.open_position:
            self._check_live_exit(i)
            return

        if (
            self.trade_count >= self.max_trades
            or self.last_signal_index == i
        ):
            return

        signal = self.engine.find_trade(
            self.nifty,
            self.options,
            i,
            self.settings
        )

        self.last_signal_index = i

        if signal is None:
            return

        quantity, contract_lot_size = (
            self._resolve_quantity(signal)
        )

        entry_status, entry_order_id = (
            self._place_entry_order(
                signal,
                quantity
            )
        )

        if entry_status.startswith("ENTRY FAILED"):

            self._record_rejected_entry(
                signal,
                i,
                entry_status
            )

            return

        self.open_position = {
            "trade_no": self.trade_count + 1,
            "signal": signal,
            "option_index": signal.get("option_index", 0),
            "entry_index": i,
            "entry_time": self._row_time(self.nifty, i),
            "entry_price": signal["entry"],
            "target": signal["target"],
            "stoploss": signal["stoploss"],
            "instrument": signal.get(
                "instrument",
                signal["type"]
            ),
            "type": signal["type"],
            "entry_order_id": entry_order_id,
            "entry_status": entry_status,
            "quantity": quantity,
            "contract_lot_size": contract_lot_size
        }

    def _resolve_quantity(self, signal):

        tradingsymbol = (
            signal.get("tradingsymbol")
            or signal.get("instrument")
        )

        if self.zerodha:

            try:
                contract_lot_size = (
                    self.zerodha.get_lot_size(
                        tradingsymbol
                    )
                )

                return (
                    self.lots * contract_lot_size,
                    contract_lot_size
                )

            except Exception:
                pass

        return self.fallback_quantity, LOT_SIZE

    def _place_entry_order(self, signal, quantity):

        if self.mode != "LIVE":
            return "LIVE PAPER ENTRY", ""

        if not self.zerodha:
            return "ENTRY FAILED: ZERODHA NOT CONNECTED", ""

        tradingsymbol = (
            signal.get("tradingsymbol")
            or signal.get("instrument")
        )

        try:

            order_id = self.zerodha.place_market_order(
                tradingsymbol=tradingsymbol,
                transaction_type="BUY",
                quantity=quantity
            )

            return "ENTRY ORDER PLACED", order_id

        except Exception as exc:

            return f"ENTRY FAILED: {exc}", ""

    def _place_exit_order(self, position):

        if self.mode != "LIVE":
            return "LIVE PAPER EXIT", ""

        if not self.zerodha:
            return "EXIT FAILED: ZERODHA NOT CONNECTED", ""

        signal = position["signal"]

        tradingsymbol = (
            signal.get("tradingsymbol")
            or signal.get("instrument")
        )

        try:

            order_id = self.zerodha.place_market_order(
                tradingsymbol=tradingsymbol,
                transaction_type="SELL",
                quantity=position["quantity"]
            )

            return "EXIT ORDER PLACED", order_id

        except Exception as exc:

            return f"EXIT FAILED: {exc}", ""

    # ======================================================
    # UPDATED EXIT LOGIC
    # ======================================================

    def _check_live_exit(self, i):

        position = self.open_position
        if not position:
            return

        option_index = position.get("option_index")
        if option_index is None:
            return

        if not self.options or option_index >= len(self.options):
            return

        option = self.options[option_index]
        if option is None or i >= len(option):
            return

        current_price = option.iloc[i]["close"]

        entry_price = position["entry_price"]

        target_price = position["target"]

        stoploss_price = position["stoploss"]

        reason = None

        # TARGET HIT
        if current_price >= target_price:
            reason = "TARGET"

        # STOPLOSS HIT
        elif current_price <= stoploss_price:
            reason = "STOPLOSS"

        # TRAILING EXIT
        else:

            profit_percent = (
                (current_price - entry_price)
                / entry_price
            ) * 100

            if profit_percent >= 10:

                trailing_stop = (
                    current_price * 0.97
                )

                if current_price <= trailing_stop:
                    reason = "TRAILING EXIT"

        # TIME EXIT
        if reason is None:

            if (
                i - position["entry_index"]
                >= self.max_hold_ticks
            ):
                reason = "TIME EXIT"

        if reason is None:
            return

        exit_status, exit_order_id = (
            self._place_exit_order(position)
        )

        pnl = (
            current_price
            - position["entry_price"]
        ) * position["quantity"]

        self.balance += pnl

        trade = {
            "Trade No": position["trade_no"],
            "Type": position["type"],
            "Instrument": position["instrument"],
            "ATM Trade": position["instrument"],
            "Entry Time": position["entry_time"],
            "Entry": position["entry_price"],
            "Exit Time": self._row_time(
                self.nifty,
                i
            ),
            "Exit": current_price,
            "PnL": pnl,
            "Total PnL": self.balance,
            "Quantity": position["quantity"],
            "Contract Lot Size": position["contract_lot_size"],
            "Reason": reason,
            "Order Status": (
                exit_status
                if self.mode == "LIVE"
                else "LIVE PAPER"
            ),
            "Order ID": (
                exit_order_id
                or position.get("entry_order_id", "")
            ),
            "Entry Order ID": position.get(
                "entry_order_id",
                ""
            ),
            "Exit Order ID": exit_order_id
        }

        self.trades.append(trade)

        self.trade_count += 1

        self.open_position = None

        if self.save_path:
            self._save_trade(trade)

        if self.on_trade:
            self.on_trade(
                trade,
                self.balance
            )

    def _record_rejected_entry(self, signal, i, status):

        trade = {
            "Trade No": self.trade_count + 1,
            "Type": signal["type"],
            "Instrument": signal.get(
                "instrument",
                signal["type"]
            ),
            "ATM Trade": signal.get(
                "instrument",
                signal["type"]
            ),
            "Entry Time": self._row_time(
                self.nifty,
                i
            ),
            "Entry": signal["entry"],
            "Exit Time": "",
            "Exit": "",
            "PnL": 0,
            "Total PnL": self.balance,
            "Reason": "ENTRY REJECTED",
            "Order Status": status,
            "Order ID": "",
            "Entry Order ID": "",
            "Exit Order ID": ""
        }

        self.trades.append(trade)

        self.trade_count += 1

        if self.save_path:
            self._save_trade(trade)

        if self.on_trade:
            self.on_trade(
                trade,
                self.balance
            )

    def _save_trade(self, trade):

        if not self.save_path:
            return

        df = pd.DataFrame([trade])

        if os.path.exists(self.save_path):
            old = pd.read_excel(self.save_path)
            df = pd.concat(
                [old, df],
                ignore_index=True
            )

        df.to_excel(
            self.save_path,
            index=False
        )

        self._save_trade_sqlite(trade)

    def _sqlite_trade_path(self):
        if not self.save_path:
            return None
        base, _ = os.path.splitext(self.save_path)
        return f"{base}.db"

    def _save_trade_sqlite(self, trade):
        db_path = self._sqlite_trade_path()
        if not db_path:
            return

        normalized = {
            "trade_id": trade.get("trade_id") or f"{self.session_id}_{self.trade_count + 1}",
            "mode": self.mode,
            "strategy_name": self.settings.get("strategy_name", "tradebotV5_livepaper"),
            "strategy_version": self.settings.get("strategy_version", "1.0"),
            "entry_time": trade.get("Entry Time"),
            "exit_time": trade.get("Exit Time"),
            "instrument": trade.get("Instrument", ""),
            "option_symbol": trade.get("Instrument", ""),
            "option_type": trade.get("Type", ""),
            "strike": trade.get("Strike"),
            "expiry": trade.get("Expiry"),
            "entry_price": trade.get("Entry"),
            "exit_price": trade.get("Exit"),
            "quantity": trade.get("Quantity", 0),
            "lot_size": trade.get("Contract Lot Size", 0),
            "pnl_points": trade.get("PnL", 0),
            "pnl_amount": trade.get("PnL", 0),
            "pnl_percent": trade.get("PnL %") if trade.get("PnL %") is not None else None,
            "charges": trade.get("Charges", 0.0),
            "net_pnl": trade.get("Total PnL", self.balance),
            "exit_reason": trade.get("Reason", ""),
            "trade_duration_minutes": trade.get("Duration", None),
            "market_regime_at_entry": trade.get("Market Regime", ""),
            "market_regime_at_exit": trade.get("Market Regime", ""),
            "risk_profile_id": None,
        }

        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        ensure_risk_engine_schema(db_path)

        with sqlite3.connect(db_path) as conn:
            pd.DataFrame([normalized]).to_sql("trades", conn, if_exists="append", index=False)
            if not self.sqlite_session_initialized:
                session_row = [{
                    "session_id": self.session_id,
                    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "ended_at": None,
                    "strategy_name": self.settings.get("strategy_name", "tradebotV5_livepaper"),
                    "strategy_version": self.settings.get("strategy_version", "1.0"),
                    "initial_balance": self.settings.get("balance", 0),
                    "final_balance": self.balance,
                    "net_pnl": self.balance - self.settings.get("balance", 0),
                    "total_trades": self.trade_count,
                    "notes": "Live/Paper session export for risk engine",
                }]
                table_name = f"{self.mode.lower()}_sessions"
                pd.DataFrame(session_row).to_sql(table_name, conn, if_exists="append", index=False)
                self.sqlite_session_initialized = True

    def _row_time(self, df, index):

        if index >= len(df):
            return ""

        row = df.iloc[index]

        if (
            "datetime" in row
            and str(row["datetime"]) != "nan"
        ):
            return row["datetime"]

        return index


# ======================================================
# IMPORTANT
# DO NOT DELETE THIS CLASS
# ======================================================

class Executor:

    def __init__(self, zerodha=None):

        self.running = False
        self.zerodha = zerodha

        self.live_paper_session = None
        self.live_real_session = None
