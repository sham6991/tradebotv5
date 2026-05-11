import argparse
import time
from datetime import datetime, timedelta

import pandas as pd

from execution_v2 import Executor, LivePaperSession
from indicators import clean_and_add_indicators
from strategy import ensure_option_formula_columns


def build_history(rows=120):
    start = datetime(2026, 5, 10, 9, 15)
    data = []
    for i in range(rows):
        price = 24000 + (i % 40)
        data.append({
            "datetime": start + timedelta(minutes=3 * i),
            "open": price,
            "high": price + 5,
            "low": price - 5,
            "close": price + 1,
            "volume": 1000 + i,
        })
    return clean_and_add_indicators(pd.DataFrame(data))


def build_option(symbol, rows=120):
    df = build_history(rows)
    df["open"] = 100 + (df.index % 20)
    df["high"] = df["open"] + 3
    df["low"] = df["open"] - 3
    df["close"] = df["open"] + 1
    df = ensure_option_formula_columns(clean_and_add_indicators(df))
    df.attrs["instrument"] = symbol
    df.attrs["tradingsymbol"] = symbol
    df.attrs["strike"] = "24200"
    df.attrs["expiry"] = "2026-05-12"
    df.attrs["option_type"] = "CE" if symbol.endswith("CE") else "PE"
    return df


def generate_batch(start_index, batch_size, tokens):
    now = datetime.now()
    batch = []
    for offset in range(batch_size):
        index = start_index + offset
        token = tokens[index % len(tokens)]
        batch.append({
            "instrument_token": token,
            "last_price": 100 + (index % 100) * 0.05,
            "volume_traded": index,
            "exchange_timestamp": now + timedelta(milliseconds=index),
        })
    return batch


def run_benchmark(total_ticks, batch_size):
    token_map = {
        1: "NIFTY",
        2: "OPTION_0",
        3: "OPTION_1",
    }
    settings = {
        "balance": 100000,
        "lot_size": 1,
        "max_trades": 1,
        "profit_points": 20,
        "safety_points": 10,
        "entry_offset": -2,
        "time_exit": 10,
        "cooldown": 5,
        "chart_interval": "3minute",
        "bullish_threshold": 16,
        "bearish_threshold": -15,
        "rsi_bull": 55,
        "rsi_bear": 45,
        "min_buy_score": 60,
        "max_daily_loss": 0,
        "max_daily_profit": 0,
        "max_consecutive_losses": 0,
        "square_off_time": "",
    }
    session = LivePaperSession(
        build_history(),
        [build_option("NIFTY24200CE"), build_option("NIFTY24200PE")],
        token_map,
        settings,
        mode="PAPER",
    )
    executor = Executor()
    executor._start_tick_dispatcher(session.on_ticks)

    start = time.perf_counter()
    sent = 0
    tokens = list(token_map)
    while sent < total_ticks:
        size = min(batch_size, total_ticks - sent)
        executor._enqueue_ticks(generate_batch(sent, size, tokens))
        sent += size

    if executor.tick_queue:
        executor.tick_queue.join()
    elapsed = time.perf_counter() - start
    metrics = executor.feed_metrics()
    executor._stop_tick_dispatcher()

    return {
        "sent_ticks": sent,
        "processed_ticks": metrics["processed_ticks"],
        "elapsed_seconds": elapsed,
        "ticks_per_second": metrics["processed_ticks"] / elapsed if elapsed else 0,
        "backlog": metrics["backlog"],
        "dropped_batches": metrics["dropped_batches"],
        "trades": session.trade_count,
    }


def main():
    parser = argparse.ArgumentParser(description="Synthetic live tick storm benchmark.")
    parser.add_argument("--ticks", type=int, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()

    result = run_benchmark(args.ticks, args.batch_size)
    for key, value in result.items():
        if isinstance(value, float):
            print(f"{key}: {value:.2f}")
        else:
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
