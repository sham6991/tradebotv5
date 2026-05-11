import argparse
import time
from datetime import datetime, timedelta

from execution_v2 import Executor, LivePaperSession
from tick_storm_benchmark import build_history, build_option


class BenchmarkEngine:
    def __init__(self):
        self.used = False
        self.last_skip_reason = ""

    def find_trade(self, nifty, options, i, settings):
        if self.used:
            self.last_skip_reason = "benchmark_signal_used"
            return None
        self.used = True
        option = options[0]
        entry_index = len(option) - 1
        entry = float(option.iloc[entry_index]["close"])
        return {
            "option": option,
            "option_index": 0,
            "type": "CE",
            "instrument": option.attrs.get("instrument", "NIFTY24200CE"),
            "tradingsymbol": option.attrs.get("tradingsymbol", "NIFTY24200CE"),
            "strike": option.attrs.get("strike", "24200"),
            "expiry": option.attrs.get("expiry", "2026-05-12"),
            "entry": entry,
            "entry_offset": 0,
            "signal_index": entry_index,
            "nifty_signal_index": i,
            "entry_index": entry_index,
            "target": entry + float(settings["profit_points"]),
            "stoploss": entry - float(settings["safety_points"]),
            "score_row": {"Buy Score": 100, "Buy Entry": "BUY"},
        }

    def mark_trade_complete(self, _exit_index):
        return


def generate_process_batch(start_index, batch_size, tokens):
    base = datetime(2026, 5, 10, 15, 30)
    batch = []
    for offset in range(batch_size):
        index = start_index + offset
        token = tokens[index % len(tokens)]
        timestamp = base + timedelta(milliseconds=index)
        price = 100 + (index % 20) * 0.1
        if token == 2 and index > 190_000:
            price = 160
        elif token == 2 and index > 2_000:
            price = 140
        batch.append({
            "instrument_token": token,
            "last_price": price,
            "volume_traded": index,
            "exchange_timestamp": timestamp,
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
        "profit_points": 5,
        "safety_points": 10,
        "entry_offset": 0,
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
    trades = []
    session = LivePaperSession(
        build_history(),
        [build_option("NIFTY24200CE"), build_option("NIFTY24200PE")],
        token_map,
        settings,
        on_trade=lambda trade, _balance: trades.append(trade),
        mode="PAPER",
    )
    session.engine = BenchmarkEngine()
    executor = Executor()
    executor._start_tick_dispatcher(session.on_ticks)

    start = time.perf_counter()
    sent = 0
    tokens = list(token_map)
    while sent < total_ticks:
        size = min(batch_size, total_ticks - sent)
        executor._enqueue_ticks(generate_process_batch(sent, size, tokens))
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
        "dispatcher_errors": metrics["dispatcher_errors"],
        "last_dispatcher_error": metrics["last_dispatcher_error"],
        "trade_count": session.trade_count,
        "trade_rows": len(trades),
        "open_position": bool(session.open_position),
        "pending_entry": bool(session.pending_entry),
        "order_transition_in_progress": bool(session.order_transition_in_progress),
        "exit_reason": trades[-1].get("Reason", "") if trades else "",
    }


def main():
    parser = argparse.ArgumentParser(description="Synthetic full process-flow benchmark.")
    parser.add_argument("--ticks", type=int, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    result = run_benchmark(args.ticks, args.batch_size)
    for key, value in result.items():
        if isinstance(value, float):
            print(f"{key}: {value:.2f}")
        else:
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
