import unittest
from datetime import datetime, timedelta

import pandas as pd

from engine import TradingEngine, attach_datetime_index_map
from strategy import OPTION_FORMULA_COLUMNS
from backtest_runtime import BacktestTradingCore


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
        "trend_set": "Auto",
        "bullish_threshold": 16,
        "bearish_threshold": -15,
        "rsi_bull": 55,
        "rsi_bear": 45,
        "rsi_reversal_bullish": 70,
        "rsi_reversal_bearish": 20,
        "bullish_reversal_condition": -20,
        "bearish_reversal_condition": 10,
        "buy_limit_score_low": 40,
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


def option_frame(option_type, entry_score=85, exit_mode="target", count=6):
    base_time = datetime(2026, 5, 12, 9, 15)
    rows = []
    for index in range(count):
        row = {
            "datetime": base_time + timedelta(minutes=3 * index),
            "open": 100,
            "high": 110,
            "low": 90,
            "close": 101,
            "volume": 1000 + index,
        }
        rows.append(row)

    # Signal at index 1. The candle shape is fast-OHLCV valid.
    if entry_score >= 60 and count > 1:
        rows[1].update({
            "open": 108,
            "high": 112,
            "low": 107,
            "close": 111,
            "volume": 1500,
        })
    elif count > 1:
        rows[1].update({
            "open": 100,
            "high": 105,
            "low": 99,
            "close": 100,
            "volume": 800,
        })
    if exit_mode == "target":
        for index in range(2, count):
            rows[index].update({"high": 122, "low": 108, "close": 121})
    elif exit_mode == "stoploss":
        for index in range(2, count):
            rows[index].update({"high": 114, "low": 105, "close": 106})
        if count > 5:
            rows[4].update({"open": 118, "high": 122, "low": 117, "close": 121, "volume": 1800})
            rows[5].update({"open": 121, "high": 123, "low": 115, "close": 116, "volume": 1500})
    elif exit_mode == "time":
        for index in range(2, min(count, 5)):
            rows[index].update({"high": 118, "low": 108, "close": 113 + index})

    df = pd.DataFrame(rows)
    for column in OPTION_FORMULA_COLUMNS:
        df[column] = 0
    df["Early Score"] = entry_score
    df["Buy Entry"] = "BUY" if entry_score >= 60 else ""
    df["Sell Entry"] = ""
    df["High Probability Buy"] = ""

    df.attrs["instrument"] = f"NIFTY25000{option_type}"
    df.attrs["tradingsymbol"] = f"NIFTY25000{option_type}"
    df.attrs["option_type"] = option_type
    return attach_datetime_index_map(df)


def run_backtest_trade(nifty, option, test_settings=None):
    engine = TradingEngine(cooldown=0)
    core = BacktestTradingCore(engine)
    core.balance = 100000
    core.lot_size = 1
    core.max_trades = 1
    core.process(nifty, [option], 1, test_settings or settings())
    return core


class FixedSignalEngine:
    def __init__(self, signal):
        self.signal = signal
        self.cooldown_until = -1
        self.last_skip_reason = ""

    def find_trade(self, nifty, options, i, settings):
        self.last_skip_reason = "entry_created"
        return self.signal

    def mark_trade_complete(self, exit_index):
        self.cooldown_until = exit_index


def fixed_exit_core(option, test_settings=None):
    signal = {
        "option": option,
        "option_index": 0,
        "type": "CE",
        "instrument": "NIFTY25000CE",
        "tradingsymbol": "NIFTY25000CE",
        "entry": 100,
        "entry_order_type": "MARKET",
        "entry_type": "MARKET ENTRY",
        "entry_offset": 0,
        "signal_index": 0,
        "nifty_signal_index": 0,
        "entry_index": 0,
        "target": 120,
        "stoploss": 90,
        "score_row": {"Early Score": 85, "Buy Entry": "BUY"},
    }
    core = BacktestTradingCore(FixedSignalEngine(signal))
    core.balance = 100000
    core.lot_size = 1
    core.max_trades = 1
    core.process(nifty_frame("bullish", count=len(option)), [option], 0, settings(**(test_settings or {})))
    return core


def exit_option_frame(rows):
    df = pd.DataFrame([
        {
            "datetime": datetime(2026, 5, 12, 9, 15) + timedelta(minutes=3 * index),
            "open": row.get("open", 100),
            "high": row["high"],
            "low": row["low"],
            "close": row.get("close", 100),
            "volume": row.get("volume", 1000 + index),
        }
        for index, row in enumerate(rows)
    ])
    df.attrs["instrument"] = "NIFTY25000CE"
    df.attrs["tradingsymbol"] = "NIFTY25000CE"
    df.attrs["option_type"] = "CE"
    return attach_datetime_index_map(df)


def timed_option_frame(start_time, rows):
    df = pd.DataFrame([
        {
            "datetime": start_time + timedelta(minutes=3 * index),
            "open": row.get("open", 100),
            "high": row["high"],
            "low": row["low"],
            "close": row.get("close", 100),
            "volume": row.get("volume", 1000 + index),
        }
        for index, row in enumerate(rows)
    ])
    df.attrs["instrument"] = "NIFTY25000CE"
    df.attrs["tradingsymbol"] = "NIFTY25000CE"
    df.attrs["option_type"] = "CE"
    return attach_datetime_index_map(df)


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
        engine = TradingEngine(cooldown=3)

        signal = engine.find_trade(
            nifty_frame("sideways"),
            [option_frame("CE"), option_frame("PE")],
            1,
            settings(),
        )

        self.assertIsNone(signal)
        self.assertEqual(engine.last_skip_reason, "sideways_trend")
        self.assertEqual(engine.cooldown_until, -1)

    def test_bullish_trend_set_overrides_nifty_and_selects_ce(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("bearish"),
            [option_frame("PE"), option_frame("CE")],
            1,
            settings(trend_set="Bullish"),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["type"], "CE")
        self.assertEqual(signal["instrument"], "NIFTY25000CE")
        self.assertEqual(signal["entry_remark"], "Trend Set Bullish override")

    def test_bearish_trend_set_overrides_nifty_and_selects_pe(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("bullish"),
            [option_frame("CE"), option_frame("PE")],
            1,
            settings(trend_set="Bearish"),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["type"], "PE")
        self.assertEqual(signal["instrument"], "NIFTY25000PE")
        self.assertEqual(signal["entry_remark"], "Trend Set Bearish override")

    def test_trend_set_only_evaluates_forced_option_side(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("bearish"),
            [option_frame("CE", entry_score=20), option_frame("PE")],
            1,
            settings(trend_set="Bullish"),
        )

        self.assertIsNone(signal)
        self.assertNotEqual(engine.last_skip_reason, "entry_created")

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

    def test_rsi_reversal_bullish_requires_ema_reversal_condition(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame_from_values(70, 100, 75),
            [option_frame("PE"), option_frame("CE")],
            1,
            settings(),
        )

        self.assertIsNone(signal)
        self.assertEqual(engine.last_skip_reason, "sideways_trend")

    def test_custom_bullish_reversal_condition_selects_ce(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame_from_values(88, 100, 75),
            [option_frame("PE"), option_frame("CE")],
            1,
            settings(bullish_reversal_condition=-12),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["type"], "CE")
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

    def test_rsi_reversal_bearish_requires_ema_reversal_condition(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame_from_values(120, 100, 15),
            [option_frame("CE"), option_frame("PE")],
            1,
            settings(),
        )

        self.assertIsNone(signal)
        self.assertEqual(engine.last_skip_reason, "sideways_trend")

    def test_custom_bearish_reversal_condition_selects_pe(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame_from_values(105, 100, 15),
            [option_frame("CE"), option_frame("PE")],
            1,
            settings(bearish_reversal_condition=5),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["type"], "PE")
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

    def test_fast_score_below_threshold_blocks_entry(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("bullish"),
            [option_frame("CE")],
            1,
            settings(buy_limit_score_low=70, market_entry_score=80),
        )

        self.assertIsNone(signal)
        self.assertEqual(engine.last_skip_reason, "main_fast_trigger_failed")

    def test_market_entry_uses_signal_candle_close(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("bullish", count=4),
            [option_frame("CE", count=4)],
            1,
            settings(entry_offset=0),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["entry_index"], 1)
        self.assertEqual(signal["entry"], 111)
        self.assertEqual(engine.last_skip_reason, "entry_created")

    def test_backtest_market_entry_limit_buffer_is_applied_as_filled_limit(self):
        option = exit_option_frame([
            {"open": 100, "high": 112, "low": 99, "close": 100},
            {"open": 110, "high": 125, "low": 105, "close": 124},
        ])

        core = fixed_exit_core(
            option,
            {
                "profit_points": 20,
                "safety_points": 10,
                "live_option_market_entry_as_limit_enabled": True,
                "live_option_market_entry_limit_buffer_points": 2,
            },
        )

        self.assertEqual(core.trade_count, 1)
        self.assertEqual(core.trades[0]["Entry"], 102)
        self.assertEqual(core.trades[0]["Order Type"], "LIMIT")
        self.assertEqual(core.trades[0]["Buy Limit Price"], 102)
        self.assertEqual(core.trades[0]["Limit Offset"], 2)
        self.assertEqual(core.trades[0]["Limit Fill Status"], "FILLED")
        self.assertEqual(core.entry_attempts[0]["Limit Fill Status"], "FILLED")
        self.assertEqual(core.trades[0]["entry_index"], 0)
        self.assertEqual(core.trades[0]["Target Price"], 122)

    def test_backtest_blocks_entries_at_square_off_time(self):
        start_time = datetime(2026, 5, 12, 15, 21)
        option = timed_option_frame(start_time, [
            {"open": 100, "high": 130, "low": 99, "close": 120},
            {"open": 120, "high": 132, "low": 119, "close": 130},
        ])
        signal = {
            "option": option,
            "option_index": 0,
            "type": "CE",
            "instrument": "NIFTY25000CE",
            "entry": 100,
            "entry_order_type": "MARKET",
            "entry_type": "MARKET ENTRY",
            "entry_offset": 0,
            "signal_index": 0,
            "nifty_signal_index": 0,
            "entry_index": 0,
            "target": 120,
            "stoploss": 90,
            "score_row": {"Early Score": 85, "Buy Entry": "BUY"},
        }
        core = BacktestTradingCore(FixedSignalEngine(signal))
        core.balance = 100000
        core.lot_size = 1
        core.max_trades = 1
        nifty = nifty_frame("bullish", count=2)
        nifty["datetime"] = [start_time + timedelta(minutes=3 * index) for index in range(len(nifty))]
        nifty = attach_datetime_index_map(nifty)

        core.process(nifty, [option], 0, settings(square_off_time="15:20"))

        self.assertEqual(core.trade_count, 0)
        self.assertEqual(core.engine.last_skip_reason, "square_off_time_reached")

    def test_backtest_square_off_time_caps_open_trade_exit(self):
        start_time = datetime(2026, 5, 12, 15, 18)
        option = timed_option_frame(start_time, [
            {"open": 100, "high": 110, "low": 99, "close": 100},
            {"open": 105, "high": 110, "low": 100, "close": 105},
            {"open": 105, "high": 125, "low": 104, "close": 124},
        ])

        core = fixed_exit_core(
            option,
            {"profit_points": 20, "safety_points": 10, "time_exit": 5, "square_off_time": "15:20"},
        )

        self.assertEqual(core.trade_count, 1)
        self.assertEqual(core.trades[0]["Reason"], "AUTO SQUARE OFF")
        self.assertEqual(core.trades[0]["exit_index"], 1)
        self.assertEqual(core.trades[0]["Exit"], 105)

    def test_offset_entry_uses_signal_candle_close_without_waiting_for_next_candle(self):
        engine = TradingEngine(cooldown=0)

        signal = engine.find_trade(
            nifty_frame("bullish", count=2),
            [option_frame("CE", count=2)],
            1,
            settings(entry_offset=-2, market_entry_score=70),
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal["entry_index"], 1)
        self.assertEqual(signal["entry"], 109)
        self.assertEqual(engine.last_skip_reason, "entry_created")

    def test_target_exit(self):
        core = run_backtest_trade(nifty_frame("bullish"), option_frame("CE", exit_mode="target"))

        self.assertEqual(core.trade_count, 1)
        self.assertEqual(core.trades[0]["Reason"], "TARGET")
        self.assertEqual(core.trades[0]["Exit"], 121)
        self.assertEqual(core.trades[0]["PnL"], 10)
        self.assertFalse(core.trades[0]["same_candle_target_ignored"])

    def test_stoploss_exit(self):
        core = run_backtest_trade(nifty_frame("bullish"), option_frame("CE", exit_mode="stoploss"))

        self.assertEqual(core.trade_count, 1)
        self.assertEqual(core.trades[0]["Reason"], "STOPLOSS")
        self.assertEqual(core.trades[0]["Exit"], 106)
        self.assertEqual(core.trades[0]["PnL"], -5)

    def test_backtest_ignores_same_candle_target_and_checks_next_low_first(self):
        option = exit_option_frame([
            {"open": 98, "high": 125, "low": 92, "close": 110},
            {"open": 111, "high": 122, "low": 89, "close": 118},
        ])

        core = fixed_exit_core(option)

        self.assertEqual(core.trades[0]["Reason"], "STOPLOSS")
        self.assertEqual(core.trades[0]["Exit"], 90)
        self.assertTrue(core.trades[0]["same_candle_target_ignored"])
        self.assertEqual(core.trades[0]["entry_index"], 0)
        self.assertEqual(core.trades[0]["exit_index"], 1)

    def test_backtest_allows_same_candle_stoploss_but_not_target(self):
        option = exit_option_frame([
            {"open": 101, "high": 121, "low": 88, "close": 115},
            {"open": 115, "high": 130, "low": 114, "close": 125},
        ])

        core = fixed_exit_core(option)

        self.assertEqual(core.trades[0]["Reason"], "STOPLOSS_SAME_CANDLE")
        self.assertEqual(core.trades[0]["Exit"], 90)
        self.assertTrue(core.trades[0]["same_candle_target_ignored"])
        self.assertEqual(core.trades[0]["exit_index"], 0)

    def test_backtest_updates_trailing_stop_before_low_first_exit(self):
        option = exit_option_frame([
            {"open": 100, "high": 110, "low": 95, "close": 105},
            {"open": 111, "high": 125, "low": 114, "close": 118},
        ])

        core = fixed_exit_core(
            option,
            {
                "trailing_sl_enabled": True,
                "profit_points": 20,
                "trailing_start_points": 10,
                "trailing_step_points": 5,
                "trailing_lock_points": 5,
            },
        )

        self.assertEqual(core.trades[0]["Reason"], "TRAILING_STOPLOSS")
        self.assertEqual(core.trades[0]["Exit"], 120)
        self.assertEqual(core.trades[0]["current_sl_price"], 120)
        self.assertEqual(core.trades[0]["trailing_modifications"][0]["new_sl_price"], 120)

    def test_backtest_blocks_further_entries_after_two_stoplosses(self):
        engine = TradingEngine(cooldown=0)
        core = BacktestTradingCore(engine)
        core.balance = 100000
        core.lot_size = 1
        core.max_trades = 5
        test_settings = settings(max_trades=5, failed_breakout_penalty=0)
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
        self.assertEqual(core.trades[0]["Reason"], "TIME_EXIT")
        self.assertEqual(core.trades[0]["Exit"], 117)
        self.assertEqual(core.trades[0]["PnL"], 6)


if __name__ == "__main__":
    unittest.main()
