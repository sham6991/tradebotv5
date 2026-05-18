from copy import deepcopy
from datetime import datetime, time, timedelta
from itertools import count

import pandas as pd

from backtest import trim_to_common_datetime
from config import LOT_SIZE
from engine import TradingEngine
from indicators import clean_and_add_indicators
from reporting import timestamped_file
from strategy import ensure_option_formula_columns
from trading_core import TradingCore


OPTIMIZED_SETTING_KEYS = [
    "safety_points",
    "time_exit",
    "bullish_threshold",
    "bearish_threshold",
    "rsi_bull",
    "rsi_bear",
    "rsi_reversal_bullish",
    "rsi_reversal_bearish",
    "min_buy_score",
    "min_volume_ratio",
    "max_chase_range_ratio",
]

INT_SETTINGS = {
    "max_trades",
    "time_exit",
    "cooldown",
    "max_consecutive_losses",
}


def clone_frame(frame):
    copied = frame.copy(deep=True)
    copied.attrs.update(deepcopy(getattr(frame, "attrs", {})))
    return copied


def setting_candidates(key, value):
    if value in ("", None):
        return [value]
    try:
        base = float(value)
    except (TypeError, ValueError):
        return [value]

    if key == "entry_offset":
        values = [base - 1, base, base + 1]
    elif key == "cooldown":
        values = [max(0, base - 1), base, base + 2]
    elif key == "max_trades":
        values = [max(1, base - 2), base, base + 2]
    elif key == "time_exit":
        values = [max(1, round(base * 0.6)), base, max(base + 1, round(base * 1.5))]
    elif key in {"profit_points", "safety_points"}:
        values = [max(0.05, round(base * 0.7, 2)), base, round(base * 1.3, 2)]
    elif key in {"bullish_threshold", "rsi_bull", "rsi_reversal_bullish", "watch_buy_score", "min_buy_score", "strong_buy_score", "aggression_score_cap", "early_breakout_min_score"}:
        values = [max(0, base - 10), base, base + 10]
    elif key in {"bearish_threshold", "failed_breakout_penalty"}:
        values = [base - 10, base, min(0, base + 10)]
    elif key == "rsi_bear":
        values = [max(0, base - 5), base, min(100, base + 5)]
    elif key == "rsi_reversal_bearish":
        values = [max(0, base - 10), base, min(100, base + 10)]
    elif key in {"min_volume_ratio", "compression_range_ratio"}:
        values = [max(0, round(base * 0.8, 2)), base, round(base * 1.2, 2)]
    elif key in {"expansion_range_ratio", "max_chase_range_ratio"}:
        values = [max(0.1, round(base * 0.85, 2)), base, round(base * 1.15, 2)]
    elif key == "min_option_volume":
        step = max(100, base * 0.25)
        values = [max(0, base - step), base, base + step]
    else:
        values = [base]

    normalized = []
    for candidate in values:
        if key in INT_SETTINGS:
            candidate = int(round(candidate))
        else:
            candidate = round(float(candidate), 4)
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


def setting_grid(base_settings):
    rows = []
    for key in OPTIMIZED_SETTING_KEYS:
        if key in base_settings:
            rows.append({
                "Setting": key,
                "Low": setting_candidates(key, base_settings[key])[0],
                "Default": base_settings[key],
                "High": setting_candidates(key, base_settings[key])[-1],
                "Candidates": ", ".join(str(item) for item in setting_candidates(key, base_settings[key])),
            })
    return rows


def prepare_day_frame(frame, settings=None, option_contract=None, option_data=False):
    if frame is None or frame.empty:
        return pd.DataFrame()
    prepared = clean_and_add_indicators(frame.copy())
    if option_data:
        prepared = ensure_option_formula_columns(prepared, settings)
        if option_contract:
            prepared.attrs["instrument"] = option_contract.get("tradingsymbol", "")
            prepared.attrs["tradingsymbol"] = option_contract.get("tradingsymbol", "")
            prepared.attrs["strike"] = option_contract.get("strike", "")
            prepared.attrs["expiry"] = str(option_contract.get("expiry", ""))[:10]
            prepared.attrs["option_type"] = option_contract.get("option_type", "")
    return prepared


def date_range(start_date, end_date):
    start = pd.to_datetime(start_date, errors="raise").date()
    end = pd.to_datetime(end_date, errors="raise").date()
    if start > end:
        raise ValueError("Start date must be before or equal to end date.")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def fetch_day_data(zerodha, trade_date, nifty_token, option_contracts, interval, settings):
    from_dt = datetime.combine(trade_date, time(9, 15))
    to_dt = datetime.combine(trade_date, time(15, 30))
    nifty = prepare_day_frame(
        zerodha.historical_candles(nifty_token, from_dt, to_dt, interval=interval),
        settings=settings,
    )
    options = []
    for contract in option_contracts:
        option = prepare_day_frame(
            zerodha.historical_candles(contract["token"], from_dt, to_dt, interval=interval),
            settings=settings,
            option_contract=contract,
            option_data=True,
        )
        options.append(option)
    return nifty, options


def run_memory_backtest(nifty, options, settings):
    nifty, options = trim_to_common_datetime(
        clone_frame(nifty),
        [clone_frame(option) for option in options],
        settings,
    )
    engine = TradingEngine(settings["cooldown"])
    core = TradingCore(engine, mode="BACKTEST")
    core.balance = float(settings["balance"])
    core.lot_size = int(settings["lot_size"]) * LOT_SIZE
    core.max_trades = int(settings["max_trades"])
    for i in range(6, len(nifty) - 1):
        core.process(nifty, options, i, settings)
    return core


def summarize_trades(trades, start_balance, final_balance):
    pnl = final_balance - start_balance
    wins = sum(1 for trade in trades if float(trade.get("PnL", 0) or 0) > 0)
    losses = sum(1 for trade in trades if float(trade.get("PnL", 0) or 0) < 0)
    total = len(trades)
    return {
        "Final Balance": round(final_balance, 2),
        "Net PnL": round(pnl, 2),
        "Trades": total,
        "Wins": wins,
        "Losses": losses,
        "Win Rate %": round((wins / total) * 100, 2) if total else 0,
        "Target Exits": sum(1 for trade in trades if trade.get("Reason") == "TARGET"),
        "Stoploss Exits": sum(1 for trade in trades if trade.get("Reason") == "STOPLOSS"),
        "Time Exits": sum(1 for trade in trades if trade.get("Reason") == "TIME EXIT"),
    }


def score_result(result):
    return (
        float(result.get("Net PnL", 0) or 0),
        float(result.get("Win Rate %", 0) or 0),
        -int(result.get("Stoploss Exits", 0) or 0),
        int(result.get("Trades", 0) or 0),
    )


def evaluate_settings(day_data, settings, run_id):
    all_trades = []
    day_rows = []
    balance = float(settings["balance"])
    for item in day_data:
        day_settings = {**settings, "balance": balance}
        core = run_memory_backtest(item["nifty"], item["options"], day_settings)
        trades = []
        for trade in core.trades:
            row = dict(trade)
            row["Run ID"] = run_id
            row["Date"] = item["date"]
            trades.append(row)
        all_trades.extend(trades)
        summary = summarize_trades(trades, balance, core.balance)
        day_rows.append({"Run ID": run_id, "Date": item["date"], **summary})
        balance = core.balance
    summary = summarize_trades(all_trades, float(settings["balance"]), balance)
    return summary, day_rows, all_trades


def optimize_settings(day_data, base_settings):
    run_counter = count(1)
    current = dict(base_settings)
    run_rows = []
    step_rows = []
    best_run_id = next(run_counter)
    best_summary, best_day_rows, best_trades = evaluate_settings(day_data, current, best_run_id)
    run_rows.append({"Run ID": best_run_id, "Setting": "baseline", "Candidate": "", **best_summary})

    for key in OPTIMIZED_SETTING_KEYS:
        if key not in current:
            continue
        local_best_settings = dict(current)
        local_best_summary = best_summary
        local_best_day_rows = best_day_rows
        local_best_trades = best_trades
        for candidate in setting_candidates(key, current[key]):
            candidate_settings = {**current, key: candidate}
            run_id = next(run_counter)
            summary, day_rows, trades = evaluate_settings(day_data, candidate_settings, run_id)
            run_rows.append({"Run ID": run_id, "Setting": key, "Candidate": candidate, **summary})
            if score_result(summary) > score_result(local_best_summary):
                local_best_settings = candidate_settings
                local_best_summary = summary
                local_best_day_rows = day_rows
                local_best_trades = trades
        improved = score_result(local_best_summary) > score_result(best_summary)
        step_rows.append({
            "Setting": key,
            "Previous Value": current[key],
            "Selected Value": local_best_settings[key],
            "Improved": improved,
            **local_best_summary,
        })
        current = local_best_settings
        best_summary = local_best_summary
        best_day_rows = local_best_day_rows
        best_trades = local_best_trades

    return current, best_summary, best_day_rows, best_trades, run_rows, step_rows


def run_live_backtest_optimizer(
    zerodha,
    nifty_token,
    option_contracts,
    start_date,
    end_date,
    interval,
    base_settings,
    output_dir,
):
    if not zerodha:
        raise ValueError("Connect Zerodha first.")
    if len(option_contracts or []) < 2:
        raise ValueError("CE and PE contracts are required.")
    base_settings = {**base_settings, "chart_interval": interval}

    fetch_rows = []
    day_data = []
    for trade_date in date_range(start_date, end_date):
        nifty, options = fetch_day_data(zerodha, trade_date, nifty_token, option_contracts[:2], interval, base_settings)
        usable = not nifty.empty and len(options) >= 2 and all(not option.empty for option in options[:2])
        fetch_rows.append({
            "Date": str(trade_date),
            "NIFTY Candles": len(nifty),
            "CE Candles": len(options[0]) if options else 0,
            "PE Candles": len(options[1]) if len(options) > 1 else 0,
            "Used": usable,
        })
        if usable:
            day_data.append({"date": str(trade_date), "nifty": nifty, "options": options[:2]})

    if not day_data:
        raise ValueError("No usable Zerodha historical candles found for the selected date range.")

    best_settings, best_summary, day_rows, trades, run_rows, step_rows = optimize_settings(day_data, dict(base_settings))
    path = timestamped_file("livebacktesting", output_dir)
    settings_rows = [{"Setting": key, "Value": value} for key, value in sorted(best_settings.items())]
    base_rows = [{"Setting": key, "Value": value} for key, value in sorted(base_settings.items())]
    contract_rows = [{**contract} for contract in option_contracts[:2]]
    summary_rows = [{
        "Output File": path,
        "Start Date": str(start_date),
        "End Date": str(end_date),
        "Fetched Data Interval": interval,
        "Chart Interval Optimized": "No",
        **best_summary,
    }]

    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Summary", index=False)
        pd.DataFrame(settings_rows).to_excel(writer, sheet_name="Optimized Settings", index=False)
        pd.DataFrame(base_rows).to_excel(writer, sheet_name="Base Settings", index=False)
        pd.DataFrame(step_rows).to_excel(writer, sheet_name="Optimization Steps", index=False)
        pd.DataFrame(run_rows).sort_values(
            by=["Net PnL", "Win Rate %"],
            ascending=[False, False],
        ).to_excel(writer, sheet_name="All Runs", index=False)
        pd.DataFrame(day_rows).to_excel(writer, sheet_name="Day Results", index=False)
        pd.DataFrame(trades).to_excel(writer, sheet_name="Best Trades", index=False)
        pd.DataFrame(setting_grid(base_settings)).to_excel(writer, sheet_name="Setting Ranges", index=False)
        pd.DataFrame(fetch_rows).to_excel(writer, sheet_name="Fetch Log", index=False)
        pd.DataFrame(contract_rows).to_excel(writer, sheet_name="Contracts", index=False)

    return {
        "output_path": path,
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days_requested": len(fetch_rows),
        "days_used": len(day_data),
        "runs": len(run_rows),
        "best_settings": best_settings,
        "summary": best_summary,
    }
