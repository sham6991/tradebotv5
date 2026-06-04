from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, time
from itertools import product
from statistics import median

import pandas as pd

from backtest import trim_to_common_datetime
from backtest_runtime import BacktestTradingCore
from config import LOT_SIZE
from engine import TradingEngine
from reporting import timestamped_file
from strategy import ensure_option_formula_columns


OPTIMIZED_RISK_SETTING_KEYS = [
    "profit_points",
    "safety_points",
    "time_exit",
    "cooldown",
    "max_trades",
]

PHASES = [
    ("Morning", time(9, 15), time(11, 15)),
    ("Midday", time(11, 15), time(13, 15)),
    ("Afternoon", time(13, 15), time(15, 30)),
]

MAX_OPTIMIZER_WORKERS = 2


class OptimizerStopped(RuntimeError):
    pass


def run_risk_settings_optimizer(
    nifty,
    options,
    base_settings,
    output_dir,
    source_metadata=None,
    optimizer_config=None,
    progress_callback=None,
    stop_requested=None,
):
    settings = dict(base_settings or {})
    config = dict(optimizer_config or {})
    raise_if_optimizer_stopped(stop_requested, "Risk settings optimizer")
    emit_optimizer_progress(progress_callback, "Preparing", 0, 0, "Preparing risk setting candidates")
    prepared_nifty, prepared_options = trim_to_common_datetime(nifty.copy(), [option.copy() for option in options], settings)
    if prepared_nifty.empty or not prepared_options:
        raise ValueError("Backtest risk optimizer needs one full day of NIFTY, CE, and PE candles.")

    ranges = build_candidate_ranges(settings, prepared_options, config)
    candidates = build_candidate_settings(settings, ranges)
    raise_if_optimizer_stopped(stop_requested, "Risk settings optimizer")
    max_runs = int(config.get("max_runs", 60000) or 60000)
    if len(candidates) > max_runs:
        raise ValueError(f"Risk optimizer generated {len(candidates)} runs. Narrow ranges below {max_runs}.")

    prepared_options = precompute_risk_option_columns(prepared_options, settings)
    worker_count = optimizer_worker_count(config, len(candidates))
    rows, phase_rows, trades_by_key = evaluate_candidates(
        prepared_nifty,
        prepared_options,
        candidates,
        settings,
        progress_callback=progress_callback,
        workers=worker_count,
        stop_requested=stop_requested,
    )
    refine_top_n = int(config.get("refine_top_n", 18) or 0)
    raise_if_optimizer_stopped(stop_requested, "Risk settings optimizer")
    if refine_top_n > 0 and not config.get("candidate_ranges") and not config.get("ranges"):
        refined = refined_candidates(settings, rows[:refine_top_n], ranges)
        existing = {candidate_key(candidate) for candidate in candidates}
        refined = [candidate for candidate in refined if candidate_key(candidate) not in existing]
        remaining_slots = max(0, max_runs - len(rows))
        if refined and remaining_slots:
            more_rows, more_phase_rows, more_trades = evaluate_candidates(
                prepared_nifty,
                prepared_options,
                refined[:remaining_slots],
                settings,
                stage="REFINED",
                progress_callback=progress_callback,
                workers=worker_count,
                stop_requested=stop_requested,
            )
            rows.extend(more_rows)
            phase_rows.extend(more_phase_rows)
            trades_by_key.update(more_trades)

    raise_if_optimizer_stopped(stop_requested, "Risk settings optimizer")
    emit_optimizer_progress(progress_callback, "Finalizing", len(rows), len(rows), "Testing complete; writing optimizer report")
    rows = add_robustness_scores(rows)
    ranked = sorted(rows, key=lambda row: row["Reliable Score"], reverse=True)
    best = ranked[0] if ranked else {}
    best_profit = max(rows, key=lambda row: row["Net PnL"], default={})
    best_key = best.get("Candidate Key", "")
    best_settings = best_settings_from_row(settings, best)

    output_path = timestamped_file("risk_settings_optimizer", output_dir)
    write_optimizer_workbook(
        output_path,
        ranked,
        phase_rows,
        trades_by_key.get(best_key, []),
        ranges,
        settings,
        best,
        best_profit,
        source_metadata or {},
    )
    return {
        "output_path": output_path,
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runs": len(rows),
        "parallel_workers": worker_count,
        "optimized_keys": list(OPTIMIZED_RISK_SETTING_KEYS),
        "best_settings": best_settings,
        "best_reliable_result": public_result_row(best),
        "best_profit_result": public_result_row(best_profit),
        "top_results": [public_result_row(row) for row in ranked[:10]],
        "source_metadata": source_metadata or {},
    }


def emit_optimizer_progress(progress_callback, stage, completed=0, total=0, message=""):
    if not progress_callback:
        return
    completed = max(0, _int_value(completed, 0))
    total = max(0, _int_value(total, 0))
    percent = round((completed / total) * 100, 2) if total else 0
    progress_callback({
        "stage": stage,
        "completed": completed,
        "total": total,
        "percent": percent,
        "message": message or stage,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


def optimizer_stop_requested(stop_requested):
    return bool(stop_requested and stop_requested())


def raise_if_optimizer_stopped(stop_requested, label="Optimizer"):
    if optimizer_stop_requested(stop_requested):
        raise OptimizerStopped(f"{label} stopped by user.")


def optimizer_worker_count(config, total_candidates):
    config = dict(config or {})
    requested = config.get("parallel_workers", config.get("workers", 2))
    workers = _int_value(requested, 2)
    if workers <= 1 or total_candidates <= 1:
        return 1
    return min(workers, MAX_OPTIMIZER_WORKERS, int(total_candidates))


def precompute_risk_option_columns(options, settings):
    prepared = []
    for option in options:
        prepared.append(ensure_option_formula_columns(option, settings))
    return prepared


def build_candidate_ranges(settings, options, config=None):
    config = dict(config or {})
    supplied = config.get("candidate_ranges") or config.get("ranges") or {}
    if supplied:
        return {
            key: normalize_candidates(supplied.get(key, [settings.get(key)]), integer=key in {"time_exit", "cooldown", "max_trades"})
            for key in OPTIMIZED_RISK_SETTING_KEYS
        }

    observed_range = observed_option_range(options)
    profit_base = _float_value(settings.get("profit_points", 5), 5)
    safety_base = _float_value(settings.get("safety_points", 10), 10)
    max_trades_base = _int_value(settings.get("max_trades", 5), 5)

    profit = point_candidates(
        profit_base,
        observed_range,
        fixed=(3, 4, 5, 6, 8, 10, 12, 15, 18, 20),
        multipliers=(0.35, 0.5, 0.75, 1.0, 1.25),
        lower=1,
        upper=max(20, profit_base * 2, observed_range * 1.75),
        limit=11,
    )
    safety = point_candidates(
        safety_base,
        observed_range,
        fixed=(3, 4, 5, 6, 8, 10, 12, 15, 18, 20),
        multipliers=(0.5, 0.75, 1.0, 1.25, 1.5),
        lower=1,
        upper=max(20, safety_base * 2, observed_range * 2.25),
        limit=11,
    )
    return {
        "profit_points": profit,
        "safety_points": safety,
        "time_exit": unique_sorted([2, 3, 4, 5, 6, 8, 10, 12, 15, _int_value(settings.get("time_exit", 10), 10)]),
        "cooldown": unique_sorted([0, 1, 2, 3, 5, _int_value(settings.get("cooldown", 0), 0)]),
        "max_trades": unique_sorted([1, 2, 3, 5, 8, max_trades_base]),
    }


def build_candidate_settings(base_settings, ranges):
    candidates = []
    for values in product(*(ranges[key] for key in OPTIMIZED_RISK_SETTING_KEYS)):
        candidate = dict(base_settings)
        candidate.update(dict(zip(OPTIMIZED_RISK_SETTING_KEYS, values)))
        candidates.append(candidate)
    return candidates


def refined_candidates(base_settings, top_rows, ranges):
    refined = []
    for row in top_rows:
        local_ranges = {
            "profit_points": nearby_points(row["Profit Points"], ranges["profit_points"]),
            "safety_points": nearby_points(row["Safety Points"], ranges["safety_points"]),
            "time_exit": nearby_ints(row["Time Exit"], ranges["time_exit"], lower=1),
            "cooldown": nearby_ints(row["Cooldown"], ranges["cooldown"], lower=0),
            "max_trades": nearby_ints(row["Max Trades"], ranges["max_trades"], lower=1),
        }
        refined.extend(build_candidate_settings(base_settings, local_ranges))
    return refined


def evaluate_candidates(
    nifty,
    options,
    candidates,
    base_settings,
    stage="BROAD",
    progress_callback=None,
    workers=1,
    stop_requested=None,
):
    rows, phase_rows, trades_by_key = evaluate_candidate_results(
        nifty,
        options,
        candidates,
        base_settings,
        stage,
        progress_callback,
        workers,
        copy_frames=False,
        stop_requested=stop_requested,
        summarize_func=lambda settings, trades, base, final_balance, sequence: summarize_candidate(
            settings,
            trades,
            base,
            final_balance,
            stage,
            sequence,
        ),
    )
    rows.sort(key=lambda row: (-row["Base Score"], row["Run No"]))
    return rows, phase_rows, trades_by_key


def evaluate_candidate_results(
    nifty,
    options,
    candidates,
    base_settings,
    stage,
    progress_callback=None,
    workers=1,
    copy_frames=False,
    stop_requested=None,
    summarize_func=None,
):
    rows = []
    phase_rows = []
    trades_by_key = {}
    total = len(candidates)
    emit_optimizer_progress(progress_callback, stage, 0, total, f"{stage.title()} tests starting")
    progress_step = max(1, total // 200) if total else 1
    raise_if_optimizer_stopped(stop_requested, stage.title())
    if workers <= 1 or total <= 1:
        for index, settings in enumerate(candidates, start=1):
            raise_if_optimizer_stopped(stop_requested, stage.title())
            row, phases, trades = evaluate_one_candidate(
                nifty,
                options,
                settings,
                base_settings,
                index,
                copy_frames,
                stop_requested,
                summarize_func,
            )
            rows.append(row)
            phase_rows.extend(phases)
            trades_by_key[row["Candidate Key"]] = trades
            if index == 1 or index == total or index % progress_step == 0:
                emit_optimizer_progress(progress_callback, stage, index, total, f"{stage.title()} tests running")
        return rows, phase_rows, trades_by_key

    completed = 0
    next_index = 0
    pending = set()

    def submit_next(executor):
        nonlocal next_index
        if next_index >= total:
            return
        sequence = next_index + 1
        candidate = candidates[next_index]
        pending.add(executor.submit(
            evaluate_one_candidate,
            nifty,
            options,
            candidate,
            base_settings,
            sequence,
            copy_frames,
            stop_requested,
            summarize_func,
        ))
        next_index += 1

    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        for _unused in range(min(total, workers * 2)):
            raise_if_optimizer_stopped(stop_requested, stage.title())
            submit_next(executor)
        while pending:
            raise_if_optimizer_stopped(stop_requested, stage.title())
            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                row, phases, trades = future.result()
                rows.append(row)
                phase_rows.extend(phases)
                trades_by_key[row["Candidate Key"]] = trades
                completed += 1
                if completed == 1 or completed == total or completed % progress_step == 0:
                    emit_optimizer_progress(progress_callback, stage, completed, total, f"{stage.title()} tests running with {workers} workers")
                if not optimizer_stop_requested(stop_requested):
                    submit_next(executor)
    except OptimizerStopped:
        for future in pending:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return rows, phase_rows, trades_by_key


def evaluate_one_candidate(nifty, options, settings, base_settings, sequence, copy_frames, stop_requested, summarize_func):
    final_balance, trades = run_backtest_in_memory(nifty, options, settings, copy_frames=copy_frames, stop_requested=stop_requested)
    row, phases = summarize_func(settings, trades, base_settings, final_balance, sequence)
    return row, phases, trades


def run_backtest_in_memory(nifty, options, settings, copy_frames=False, stop_requested=None):
    if copy_frames:
        nifty = nifty.copy(deep=True)
        options = [option.copy(deep=True) for option in options]
    engine = TradingEngine(_int_value(settings.get("cooldown", 0), 0))
    core = BacktestTradingCore(engine)
    core.mode = "BACKTEST"
    core.balance = _float_value(settings.get("balance", 0), 0)
    core.lot_size = _int_value(settings.get("lot_size", 1), 1) * LOT_SIZE
    core.max_trades = _int_value(settings.get("max_trades", 1), 1)
    for index in range(1, len(nifty)):
        if index == 1 or index % 25 == 0:
            raise_if_optimizer_stopped(stop_requested)
        core.process(nifty, options, index, settings)
    return core.balance, list(core.trades)


def summarize_candidate(settings, trades, base_settings, final_balance, stage, sequence):
    initial_balance = _float_value(settings.get("balance", base_settings.get("balance", 0)), 0)
    pnl_values = [_trade_pnl(trade) for trade in trades]
    net_pnl = final_balance - initial_balance
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    trade_count = len(trades)
    win_rate = (len(wins) / trade_count) * 100 if trade_count else 0
    profit_factor = gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit > 0 else 0)
    max_drawdown = equity_drawdown(initial_balance, pnl_values)
    phase_summary = summarize_phases(trades)
    stoploss_count = sum(1 for trade in trades if "STOPLOSS" in str(trade.get("Reason", "")).upper())
    target_count = sum(1 for trade in trades if "TARGET" in str(trade.get("Reason", "")).upper())
    time_exit_count = sum(1 for trade in trades if "TIME" in str(trade.get("Reason", "")).upper())
    whole_day_score = whole_day_reliability_score(phase_summary, win_rate, trade_count, max_drawdown, gross_profit, net_pnl, stoploss_count)
    base_score = (
        net_pnl
        - (max_drawdown * 1.2)
        + min(profit_factor, 5) * 25
        + win_rate * 0.4
        + whole_day_score * 1.6
        - stoploss_count * 5
    )
    key = candidate_key(settings)
    row = {
        "Candidate Key": key,
        "Stage": stage,
        "Run No": sequence,
        "Profit Points": _float_value(settings.get("profit_points"), 0),
        "Safety Points": _float_value(settings.get("safety_points"), 0),
        "Time Exit": _int_value(settings.get("time_exit"), 0),
        "Cooldown": _int_value(settings.get("cooldown"), 0),
        "Max Trades": _int_value(settings.get("max_trades"), 0),
        "Net PnL": round(net_pnl, 2),
        "Final Balance": round(final_balance, 2),
        "Trades": trade_count,
        "Wins": len(wins),
        "Losses": len(losses),
        "Win Rate %": round(win_rate, 2),
        "Gross Profit": round(gross_profit, 2),
        "Gross Loss": round(gross_loss, 2),
        "Profit Factor": round(profit_factor, 4),
        "Average PnL": round(net_pnl / trade_count, 2) if trade_count else 0,
        "Best Trade": round(max(pnl_values), 2) if pnl_values else 0,
        "Worst Trade": round(min(pnl_values), 2) if pnl_values else 0,
        "Max Drawdown": round(max_drawdown, 2),
        "Stoploss Trades": stoploss_count,
        "Target Trades": target_count,
        "Time Exit Trades": time_exit_count,
        "Phase Coverage %": phase_summary["coverage_percent"],
        "Positive Phase %": phase_summary["positive_percent"],
        "Whole Day Score": round(whole_day_score, 4),
        "Base Score": round(base_score, 4),
        "Reliable Score": round(base_score, 4),
        "Reliability Notes": reliability_notes(trade_count, phase_summary, max_drawdown, gross_profit, net_pnl, stoploss_count),
    }
    phase_rows = [
        {
            "Candidate Key": key,
            "Profit Points": row["Profit Points"],
            "Safety Points": row["Safety Points"],
            "Time Exit": row["Time Exit"],
            "Cooldown": row["Cooldown"],
            "Max Trades": row["Max Trades"],
            **phase,
        }
        for phase in phase_summary["rows"]
    ]
    return row, phase_rows


def add_robustness_scores(rows):
    if not rows:
        return rows
    leaders = sorted(rows, key=lambda row: row["Base Score"], reverse=True)[:300]
    for row in leaders:
        neighbors = [
            item for item in rows
            if abs(item["Profit Points"] - row["Profit Points"]) <= 2
            and abs(item["Safety Points"] - row["Safety Points"]) <= 2
            and abs(item["Time Exit"] - row["Time Exit"]) <= 2
            and abs(item["Cooldown"] - row["Cooldown"]) <= 1
            and abs(item["Max Trades"] - row["Max Trades"]) <= 2
        ]
        neighbor_pnls = [item["Net PnL"] for item in neighbors]
        positive_rate = (sum(1 for value in neighbor_pnls if value > 0) / len(neighbor_pnls)) * 100 if neighbor_pnls else 0
        median_pnl = median(neighbor_pnls) if neighbor_pnls else 0
        row["Neighbor Count"] = len(neighbor_pnls)
        row["Neighbor Median PnL"] = round(median_pnl, 2)
        row["Neighbor Positive Rate %"] = round(positive_rate, 2)
        row["Reliable Score"] = round(row["Base Score"] + (median_pnl * 0.25) + positive_rate - abs(row["Net PnL"] - median_pnl) * 0.08, 4)
    for row in rows:
        row.setdefault("Neighbor Count", "")
        row.setdefault("Neighbor Median PnL", "")
        row.setdefault("Neighbor Positive Rate %", "")
    return rows


def write_optimizer_workbook(path, ranked, phase_rows, best_trades, ranges, base_settings, best, best_profit, source_metadata):
    guide_rows = [
        {"Field": "Purpose", "Value": "Find robust trade-management settings for one full-day option backtest."},
        {"Field": "Strategy Safety", "Value": "Entry and decision logic are not modified; only exit/risk settings are varied on copied settings."},
        {"Field": "Ranking", "Value": "Reliable Score balances net PnL, drawdown, win rate, phase coverage, stoploss count, and neighbor robustness."},
        {"Field": "Whole Day Check", "Value": "Morning, Midday, and Afternoon phase results are reported so one lucky time block does not hide weak settings."},
        {"Field": "Data Source", "Value": source_metadata.get("data_source_label", source_metadata.get("data_source", ""))},
        {"Field": "Generated At", "Value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
    ]
    best_rows = [
        {"Selection": "Best Reliable", **public_result_row(best)},
        {"Selection": "Best Net Profit", **public_result_row(best_profit)},
    ]
    range_rows = [
        {"Setting": key, "Candidates": ", ".join(str(value) for value in values), "Count": len(values)}
        for key, values in ranges.items()
    ]
    setting_rows = [{"Setting": key, "Value": value} for key, value in sorted(base_settings.items())]
    trades = best_trades or []
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(guide_rows).to_excel(writer, sheet_name="Optimizer Guide", index=False)
        pd.DataFrame(best_rows).to_excel(writer, sheet_name="Best Settings", index=False)
        pd.DataFrame(ranked).to_excel(writer, sheet_name="Ranked Results", index=False)
        pd.DataFrame(phase_rows).to_excel(writer, sheet_name="Phase Breakdown", index=False)
        pd.DataFrame(trades).to_excel(writer, sheet_name="Best Trades", index=False)
        pd.DataFrame(range_rows).to_excel(writer, sheet_name="Candidate Ranges", index=False)
        pd.DataFrame(setting_rows).to_excel(writer, sheet_name="Base Settings", index=False)


def best_settings_from_row(base_settings, row):
    settings = dict(base_settings)
    if not row:
        return settings
    settings.update({
        "profit_points": row["Profit Points"],
        "safety_points": row["Safety Points"],
        "time_exit": row["Time Exit"],
        "cooldown": row["Cooldown"],
        "max_trades": row["Max Trades"],
    })
    return settings


def public_result_row(row):
    if not row:
        return {}
    keys = [
        "Profit Points",
        "Safety Points",
        "Time Exit",
        "Cooldown",
        "Max Trades",
        "Net PnL",
        "Trades",
        "Win Rate %",
        "Profit Factor",
        "Max Drawdown",
        "Stoploss Trades",
        "Phase Coverage %",
        "Positive Phase %",
        "Whole Day Score",
        "Reliable Score",
        "Reliability Notes",
    ]
    return {key: row.get(key, "") for key in keys}


def observed_option_range(options):
    ranges = []
    for frame in options:
        if frame is None or frame.empty:
            continue
        values = pd.to_numeric(frame.get("high"), errors="coerce") - pd.to_numeric(frame.get("low"), errors="coerce")
        ranges.extend(float(value) for value in values.dropna().tolist() if value > 0)
    return median(ranges) if ranges else 10.0


def point_candidates(base, observed_range, fixed, multipliers, lower, upper, limit):
    values = list(fixed)
    values.extend([base - 2, base - 1, base, base + 1, base + 2, base + 4])
    values.extend(observed_range * multiplier for multiplier in multipliers)
    rounded = [round(max(lower, min(upper, value)) * 2) / 2 for value in values]
    unique = unique_sorted(rounded)
    if len(unique) <= limit:
        return unique
    if base in unique:
        center = base
    else:
        center = median(unique)
    unique.sort(key=lambda value: (abs(value - center), value))
    return sorted(unique[:limit])


def normalize_candidates(values, integer=False):
    if values in ("", None):
        return []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    result = []
    for value in values:
        parsed = _int_value(value, None) if integer else _float_value(value, None)
        if parsed is None:
            continue
        result.append(parsed)
    return unique_sorted(result)


def unique_sorted(values):
    clean = []
    seen = set()
    for value in values:
        if value in ("", None):
            continue
        numeric = _float_value(value, None)
        if numeric is None:
            continue
        normalized = int(numeric) if float(numeric).is_integer() else round(float(numeric), 2)
        if normalized in seen:
            continue
        seen.add(normalized)
        clean.append(normalized)
    return sorted(clean)


def nearby_points(value, existing):
    lower = min(existing) if existing else 1
    upper = max(existing) if existing else max(value + 1, 1)
    return unique_sorted([
        max(lower, value - 1),
        max(lower, value - 0.5),
        value,
        min(upper, value + 0.5),
        min(upper, value + 1),
    ])


def nearby_ints(value, existing, lower=0):
    upper = max(existing) if existing else max(_int_value(value, 0) + 1, lower)
    parsed = _int_value(value, lower)
    return unique_sorted([max(lower, parsed - 1), parsed, min(upper, parsed + 1)])


def candidate_key(settings):
    return "|".join(str(settings.get(key, "")) for key in OPTIMIZED_RISK_SETTING_KEYS)


def _trade_pnl(trade):
    return _float_value(trade.get("Final PnL", trade.get("PnL", 0)), 0)


def equity_drawdown(initial_balance, pnl_values):
    peak = initial_balance
    equity = initial_balance
    drawdown = 0
    for pnl in pnl_values:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def summarize_phases(trades):
    phase_map = {
        name: {"Phase": name, "Trades": 0, "Net PnL": 0.0, "Wins": 0, "Losses": 0}
        for name, _start, _end in PHASES
    }
    for trade in trades:
        phase = phase_for_trade(trade)
        pnl = _trade_pnl(trade)
        item = phase_map[phase]
        item["Trades"] += 1
        item["Net PnL"] += pnl
        if pnl > 0:
            item["Wins"] += 1
        elif pnl < 0:
            item["Losses"] += 1
    rows = []
    for item in phase_map.values():
        trades_count = item["Trades"]
        row = dict(item)
        row["Net PnL"] = round(row["Net PnL"], 2)
        row["Win Rate %"] = round((row["Wins"] / trades_count) * 100, 2) if trades_count else 0
        rows.append(row)
    covered = [row for row in rows if row["Trades"] > 0]
    positive = [row for row in rows if row["Net PnL"] > 0]
    return {
        "rows": rows,
        "coverage_percent": round((len(covered) / len(PHASES)) * 100, 2),
        "positive_percent": round((len(positive) / len(PHASES)) * 100, 2),
    }


def phase_for_trade(trade):
    text = trade.get("Entry Time") or trade.get("Exit Time") or ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return "Midday"
    value = parsed.time()
    for name, start, end in PHASES:
        if start <= value < end:
            return name
    return "Afternoon"


def whole_day_reliability_score(phase_summary, win_rate, trade_count, max_drawdown, gross_profit, net_pnl, stoploss_count):
    sample_score = min(trade_count, 6) / 6 * 100
    drawdown_base = max(gross_profit, abs(net_pnl), 1)
    drawdown_penalty = min(100, (max_drawdown / drawdown_base) * 100)
    stoploss_penalty = (stoploss_count / trade_count) * 100 if trade_count else 0
    score = (
        phase_summary["coverage_percent"] * 0.25
        + phase_summary["positive_percent"] * 0.30
        + win_rate * 0.25
        + sample_score * 0.20
        - drawdown_penalty * 0.25
        - stoploss_penalty * 0.15
    )
    return max(0, min(100, score))


def reliability_notes(trade_count, phase_summary, max_drawdown, gross_profit, net_pnl, stoploss_count):
    notes = []
    if trade_count < 2:
        notes.append("LOW_SAMPLE")
    if phase_summary["coverage_percent"] < 66.67:
        notes.append("LIMITED_DAY_COVERAGE")
    if phase_summary["positive_percent"] < 33.34:
        notes.append("WEAK_PHASE_PROFIT")
    if max_drawdown > max(gross_profit, abs(net_pnl), 1):
        notes.append("DRAWDOWN_HEAVY")
    if trade_count and stoploss_count / trade_count >= 0.5:
        notes.append("STOPLOSS_HEAVY")
    return ", ".join(notes) or "OK"


def _float_value(value, default=0.0):
    if value in ("", None):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value, default=0):
    if value in ("", None):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
