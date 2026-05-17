import unittest
from datetime import datetime, timedelta

import pandas as pd

from engine import TradingEngine, attach_datetime_index_map
from strategy import OPTION_FORMULA_COLUMNS
from trading_core import TradingCore


def settings(**overrides):
    base = {
        "cooldown": 0,
        "balance": 100000,
        "lot_size": 1,
        "max_trades": 1,
        "profit_points": 10,
        "safety_points": 5,
        "time_exit": 2,
        "entry_offset": 0,
        "chart_interval": "3minute",
        "bullish_threshold": 16,
        "bearish_threshold": -15,
        "rsi_bull": 55,
        "rsi_bear": 45,
        "rsi_reversal_bullish": 70,
        "rsi_reversal_bearish": 20,
        "min_buy_score": 60,
        "max_daily_loss": 0,
        "max_daily_profit": 0,
        "max_consecutive_losses": 0,
    }
    base.update(overrides)
    return base


def nifty_frame(trend="bullish", count=6):
    base_time = datetime(2026, 5, 12, 9, 15)
    rows = []
    for index in range(count):
        if trend == "bullish":
            ema20, ema50, rsi = 120, 100, 65
        elif trend == "bearish":
            ema20, ema50, rsi = 80, 100, 35
        else:
            ema20, ema50, rsi = 105, 100, 50
        rows.append({
            "datetime": base_time + timedelta(minutes=3 * index),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": 1000 + index,
            "EMA20": ema20,
            "EMA50": ema50,
            "RSI": rsi,
        })
    return attach_datetime_index_map(pd.DataFrame(rows))


def nifty_frame_from_values(ema20, ema50, rsi, count=6):
    df = nifty_frame("sideways", count=count)
    df["EMA20"] = ema20
    df["EMA50"] = ema50
    df["RSI"] = rsi
    return attach_datetime_index_map(df)


def option_frame(option_type, buy_score=85, exit_mode="target", count=6):
    base_time = datetime(2026, 5, 12, 9, 15)
    rows = []
    for index in range(count):
        row = {
            "datetime": base_time + timedelta(minutes=3 * index),
            "open": 100,
            "high": 104,
            "low": 97,
            "close": 101,
            "volume": 1000 + index,
        }
        rows.append(row)

    # Signal at index 1, entry at index 2 with entry_offset 0 and open 100.
    if buy_score >= 60 and count > 1:
        rows[1].update({
            "open": 100,
            "high": 112,
            "low": 98,
            "close": 111,
            "volume": 5000,
        })
    elif count > 1:
        rows[1].update({
            "open": 100,
            "high": 102,
            "low": 97,
            "close": 100,
            "volume": 800,
        })
    if exit_mode == "target":
        for index in range(2, count):
            rows[index].update({"high": 111, "low": 98, "close": 110})
    elif exit_mode == "stoploss":
        for index in range(2, count):
            rows[index].update({"high": 104, "low": 94, "close": 95})
        if count > 5:
            rows[4].update({"open": 100, "high": 112, "low": 98, "close": 111, "volume": 7000})
    elif exit_mode == "time":
        for index in range(2, min(count, 5)):
            rows[index].update({"high": 104, "low": 97, "close": 102 + index})

    df = pd.DataFrame(rows)
    for column in OPTION_FORMULA_COLUMNS:
        df[column] = 0
    df["Buy Score"] = buy_score
    df["Buy Entry"] = "BUY" if buy_score >= 60 else ""
    df["Sell Entry"] = ""
    df["High Probability Buy"] = ""

    df.attrs["instrument"] = f"NIFTY25000{option_type}"
    df.attrs["tradingsymbol"] = f"NIFTY25000{option_type}"
    df.attrs["option_type"] = option_type
    return attach_datetime_index_map(df)


def run_backtest_trade(nifty, option, test_settings=None):
    engine = TradingEngine(cooldown=0)
    core = TradingCore(engine, mode="BACKTEST")
    core.balance = 100000
    core.lot_size = 1
    core.max_trades = 1
    core.process(nifty, [option], 1, test_settings or settings())
    return core


class StrategyRegressionTests(unittest.TestCase):
    def test_bullish_nifty_selects_ce(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("bullish"),
            [option_frame("PE"), option_frame("CE")],
            1,
            settings(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["type"], "CE")
        self.assertEqual(signal["instrument"], "NIFTY25000CE")

    def test_bearish_nifty_selects_pe(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("bearish"),
            [option_frame("CE"), option_frame("PE")],
            1,
            settings(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["type"], "PE")
        self.assertEqual(signal["instrument"], "NIFTY25000PE")

    def test_sideways_nifty_gives_no_trade(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("sideways"),
            [option_frame("CE"), option_frame("PE")],
            1,
            settings(),
        )

        self.assertIsNone(signal)
        self.assertEqual(engine.last_skip_reason, "sideways_trend")

    def test_rsi_reversal_bullish_enters_ce_without_ema_threshold(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame_from_values(105, 100, 75),
            [option_frame("PE"), option_frame("CE")],
            1,
            settings(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["type"], "CE")
        self.assertEqual(signal["instrument"], "NIFTY25000CE")
        self.assertEqual(signal["entry_remark"], "RSI based early Bull entry")

    def test_rsi_reversal_bearish_enters_pe_without_ema_threshold(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame_from_values(100, 105, 15),
            [option_frame("CE"), option_frame("PE")],
            1,
            settings(),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["type"], "PE")
        self.assertEqual(signal["instrument"], "NIFTY25000PE")
        self.assertEqual(signal["entry_remark"], "RSI based early bear entry")

    def test_backtest_trade_records_rsi_reversal_entry_remark(self):
        core = run_backtest_trade(
            nifty_frame_from_values(105, 100, 75),
            option_frame("CE", exit_mode="target"),
        )

        self.assertEqual(core.trade_count, 1)
        self.assertEqual(core.trades[0]["Type"], "CE")
        self.assertEqual(core.trades[0]["Entry Remark"], "RSI based early Bull entry")
        self.assertEqual(core.trades[0]["Reason"], "TARGET")

    def test_buy_score_below_threshold_blocks_entry(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("bullish"),
            [option_frame("CE", buy_score=59)],
            1,
            settings(min_buy_score=60),
        )

        self.assertIsNone(signal)
        self.assertTrue(engine.last_skip_reason.startswith("buy_score_below_60"))

    def test_target_exit(self):
        core = run_backtest_trade(nifty_frame("bullish"), option_frame("CE", exit_mode="target"))

        self.assertEqual(core.trade_count, 1)
        self.assertEqual(core.trades[0]["Reason"], "TARGET")
        self.assertEqual(core.trades[0]["Exit"], 110)
        self.assertEqual(core.trades[0]["PnL"], 10)

    def test_stoploss_exit(self):
        core = run_backtest_trade(nifty_frame("bullish"), option_frame("CE", exit_mode="stoploss"))

        self.assertEqual(core.trade_count, 1)
        self.assertEqual(core.trades[0]["Reason"], "STOPLOSS")
        self.assertEqual(core.trades[0]["Exit"], 95)
        self.assertEqual(core.trades[0]["PnL"], -5)

    def test_backtest_blocks_further_entries_after_two_stoplosses(self):
        engine = TradingEngine(cooldown=0)
        core = TradingCore(engine, mode="BACKTEST")
        core.balance = 100000
        core.lot_size = 1
        core.max_trades = 5
        test_settings = settings(max_trades=5)
        nifty = nifty_frame("bullish", count=10)
        option = option_frame("CE", exit_mode="stoploss", count=10)

        for index in range(1, 8):
            core.process(nifty, [option], index, test_settings)

        self.assertEqual(core.trade_count, 2)
        self.assertEqual([trade["Reason"] for trade in core.trades], ["STOPLOSS", "STOPLOSS"])
        self.assertEqual(core.stoploss_trades, 2)
        self.assertEqual(core.trading_blocked_reason, "stoploss_trade_limit_hit")

    def test_time_exit(self):
        core = run_backtest_trade(nifty_frame("bullish"), option_frame("CE", exit_mode="time"))

        self.assertEqual(core.trade_count, 1)
        self.assertEqual(core.trades[0]["Reason"], "TIME EXIT")
        self.assertEqual(core.trades[0]["Exit"], 106)
        self.assertEqual(core.trades[0]["PnL"], 6)


if __name__ == "__main__":
    unittest.main()
