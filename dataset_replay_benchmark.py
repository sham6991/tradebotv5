import argparse
import os
import time
from datetime import timedelta

import pandas as pd

from execution_v2 import Executor, LivePaperSession
from indicators import clean_and_add_indicators
from reporting import timestamped_file, write_excel
from strategy import ensure_option_formula_columns
from engine import parse_option_metadata_from_text


RESULT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def load_nifty(path):
    return clean_and_add_indicators(pd.read_csv(path))


def load_option(path):
    df = ensure_option_formula_columns(clean_and_add_indicators(pd.read_csv(path)))
    filename = os.path.splitext(os.path.basename(path))[0]
    meta = parse_option_metadata_from_text(filename)
    df.attrs["instrument"] = filename
    df.attrs["tradingsymbol"] = filename
    df.attrs["strike"] = meta.get("strike", "")
    df.attrs["expiry"] = meta.get("expiry", "")
    df.attrs["option_type"] = meta.get("option_type", "")
    return df


def trim_common(nifty, options):
    frames = [nifty, *options]
    if any("datetime" not in frame.columns or frame["datetime"].isna().all() for frame in frames):
        return nifty, options

    start = max(pd.to_datetime(frame["datetime"], errors="coerce").min() for frame in frames)
    end = min(pd.to_datetime(frame["datetime"], errors="coerce").max() for frame in frames)
    if pd.isna(start) or pd.isna(end) or start > end:
        return nifty, options

    def trim(frame):
        attrs = dict(frame.attrs)
        times = pd.to_datetime(frame["datetime"], errors="coerce")
        result = frame[(times >= start) & (times <= end)].copy().reset_index(drop=True)
        result.attrs.update(attrs)
        return result

    return trim(nifty), [trim(option) for option in options]


def tick_prices(row):
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    return [open_price, high, low, close]


def build_tick_sequence(frame):
    sequence = []
    for _, row in frame.iterrows():
        timestamp = row.get("datetime")
        if pd.isna(timestamp):
            timestamp = pd.Timestamp.now()
        base_time = pd.to_datetime(timestamp).to_pydatetime()
        for price_index, price in enumerate(tick_prices(row)):
            sequence.append((base_time + timedelta(milliseconds=price_index * 250), price))
    return sequence


def generate_replay_batch(sequences, token_map, start_index, batch_size):
    tokens = list(token_map)
    batch = []
    for offset in range(batch_size):
        index = start_index + offset
        token = tokens[index % len(tokens)]
        sequence = sequences[token]
        timestamp, price = sequence[(index // len(tokens)) % len(sequence)]
        batch.append({
            "instrument_token": token,
            "last_price": price,
            "volume_traded": int(index + 1),
            "exchange_timestamp": timestamp,
        })
    return batch


def run_replay(nifty_path, ce_path, pe_path, total_ticks, batch_size):
    nifty = load_nifty(nifty_path)
    ce = load_option(ce_path)
    pe = load_option(pe_path)
    nifty, options = trim_common(nifty, [ce, pe])

    token_map = {
        1: "NIFTY",
        2: "OPTION_0",
        3: "OPTION_1",
    }
    sequences_by_token = {
        1: build_tick_sequence(nifty),
        2: build_tick_sequence(options[0]),
        3: build_tick_sequence(options[1]),
    }
    settings = {
        "balance": 100000,
        "lot_size": 1,
        "max_trades": 5,
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
        "buy_limit_score_low": 40,
        "max_daily_loss": 0,
        "max_daily_profit": 0,
        "max_consecutive_losses": 0,
        "square_off_time": "",
    }
    trades = []
    session = LivePaperSession(
        nifty,
        options,
        token_map,
        settings,
        on_trade=lambda trade, _balance: trades.append(dict(trade)),
        mode="PAPER",
    )
    executor = Executor()
    executor._start_tick_dispatcher(session.on_ticks)

    start = time.perf_counter()
    sent = 0
    while sent < total_ticks:
        size = min(batch_size, total_ticks - sent)
        executor._enqueue_ticks(generate_replay_batch(sequences_by_token, token_map, sent, size))
        sent += size

    if executor.tick_queue:
        executor.tick_queue.join()
    elapsed = time.perf_counter() - start
    metrics = executor.feed_metrics()
    executor._stop_tick_dispatcher()

    summary = {
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
        "final_balance": session.balance,
        "passed": (
            sent == metrics["processed_ticks"]
            and metrics["dropped_batches"] == 0
            and metrics["dispatcher_errors"] == 0
            and not session.order_transition_in_progress
        ),
    }
    inputs = [
        {"name": "NIFTY", "path": nifty_path, "rows": len(nifty), "instrument": "NIFTY"},
        {"name": "CE", "path": ce_path, "rows": len(options[0]), "instrument": options[0].attrs.get("instrument", ""), "strike": options[0].attrs.get("strike", ""), "expiry": options[0].attrs.get("expiry", "")},
        {"name": "PE", "path": pe_path, "rows": len(options[1]), "instrument": options[1].attrs.get("instrument", ""), "strike": options[1].attrs.get("strike", ""), "expiry": options[1].attrs.get("expiry", "")},
    ]
    return summary, trades, inputs


def export_report(summary, trades, inputs):
    os.makedirs(RESULT_FOLDER, exist_ok=True)
    path = timestamped_file("dataset_replay_benchmark", RESULT_FOLDER)
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame([summary]).to_excel(writer, sheet_name="Summary", index=False)
        pd.DataFrame(trades).to_excel(writer, sheet_name="Trades", index=False)
        pd.DataFrame(inputs).to_excel(writer, sheet_name="Input Files", index=False)
    csv_path = path.replace(".xlsx", ".csv")
    pd.DataFrame([summary]).to_csv(csv_path, index=False)
    return path, csv_path


def main():
    parser = argparse.ArgumentParser(description="Replay real CSV data as a synthetic 1M tick benchmark.")
    parser.add_argument("--nifty", required=True)
    parser.add_argument("--ce", required=True)
    parser.add_argument("--pe", required=True)
    parser.add_argument("--ticks", type=int, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    summary, trades, inputs = run_replay(args.nifty, args.ce, args.pe, args.ticks, args.batch_size)
    xlsx_path, csv_path = export_report(summary, trades, inputs)
    for key, value in summary.items():
        if isinstance(value, float):
            print(f"{key}: {value:.2f}")
        else:
            print(f"{key}: {value}")
    print(f"excel_report: {xlsx_path}")
    print(f"csv_summary: {csv_path}")


if __name__ == "__main__":
    main()
