import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from itertools import product

import pandas as pd

from backtest import trim_to_common_datetime
from reporting import timestamped_file
from strategy import option_scoring_settings
from trade_settings_optimizer import (
    equity_drawdown,
    emit_optimizer_progress,
    optimizer_worker_count,
    optimizer_stop_requested,
    raise_if_optimizer_stopped,
    run_backtest_in_memory,
    summarize_phases,
    unique_sorted,
    whole_day_reliability_score,
    _float_value,
    _int_value,
    _trade_pnl,
)


TRADING_TAB_OPTIMIZED_KEYS = [
    "buy_limit_score_low",
    "market_entry_score",
    "minimum_body_percent",
    "minimum_close_position",
    "market_entry_minimum_body_percent",
    "market_entry_minimum_close_position",
    "trigger_upper_wick_max",
    "hard_rejection_upper_wick_max",
    "volume_previous_multiplier",
    "avg_volume_minimum_multiplier",
    "volume_pickup_avg_multiplier",
    "large_candle_multiplier",
    "move_from_low_max_multiplier",
    "gap_spike_multiplier",
    "buy_limit_offset_multiplier",
    "minimum_offset",
    "maximum_offset",
    "enable_chop_filter",
    "chop_lookback_candles",
    "chop_overlap_count",
    "missed_limit_cooldown_candles",
]

DEFAULT_TRADING_TAB_MAX_RUNS = 6000
QUICK_TRADING_TAB_MAX_RUNS = 500


def optimizer_config_dict(config):
    if isinstance(config, str):
        try:
            parsed = json.loads(config)
        except json.JSONDecodeError:
            return {}
        return dict(parsed or {}) if isinstance(parsed, dict) else {}
    return dict(config or {})


def trading_max_runs(config):
    config = optimizer_config_dict(config)
    if "max_runs" in config:
        return max(1, _int_value(config.get("max_runs"), DEFAULT_TRADING_TAB_MAX_RUNS))
    if enabled_text(config.get("quick_mode") or config.get("quick")) == "Enabled":
        return max(1, _int_value(config.get("quick_max_runs"), QUICK_TRADING_TAB_MAX_RUNS))
    return DEFAULT_TRADING_TAB_MAX_RUNS

FIXED_TRADING_TAB_KEYS = [
    "fast_ohlcv_entry_enabled",
    "backtest_limit_fill_mode",
    "aggressive_live_entry_enabled",
    "aggressive_setup_score",
    "aggressive_entry_score",
    "aggressive_upper_wick_max",
    "aggressive_minimum_body_percent",
    "aggressive_minimum_close_position",
    "aggressive_move_from_low_max_multiplier",
    "one_entry_attempt_per_candle",
    "max_spread_points",
]


def run_trading_tab_optimizer(
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
    config = optimizer_config_dict(optimizer_config)
    raise_if_optimizer_stopped(stop_requested, "Trading tab optimizer")
    emit_optimizer_progress(progress_callback, "Preparing", 0, 0, "Preparing Trading tab candidates")
    prepared_nifty, prepared_options = trim_to_common_datetime(nifty.copy(), [option.copy() for option in options], settings)
    if prepared_nifty.empty or not prepared_options:
        raise ValueError("Trading-tab optimizer needs one full day of NIFTY, CE, and PE candles.")

    ranges = trading_candidate_ranges(settings, config)
    candidates = build_trading_candidates(settings, ranges)
    if not candidates:
        raise ValueError("Trading-tab optimizer generated no valid candidate settings.")
    raise_if_optimizer_stopped(stop_requested, "Trading tab optimizer")
    max_runs = trading_max_runs(config)
    generated_runs = len(candidates)
    if len(candidates) > max_runs:
        candidates = candidates[:max_runs]

    worker_count = optimizer_worker_count(config, len(candidates))
    rows, phase_rows, trades_by_key = evaluate_trading_candidates(
        prepared_nifty,
        prepared_options,
        candidates,
        settings,
        progress_callback=progress_callback,
        workers=worker_count,
        stop_requested=stop_requested,
    )
    raise_if_optimizer_stopped(stop_requested, "Trading tab optimizer")
    emit_optimizer_progress(progress_callback, "Finalizing", len(rows), len(rows), "Testing complete; writing Trading tab report")
    ranked = sorted(rows, key=lambda row: row["Reliable Score"], reverse=True)
    best = ranked[0] if ranked else {}
    best_profit = max(rows, key=lambda row: row["Net PnL"], default={})
    best_settings = best_settings_from_trading_row(settings, best)
    best_key = best.get("Candidate Key", "")
    output_path = timestamped_file("trading_tab_optimizer", output_dir)

    write_trading_optimizer_workbook(
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
        "generated_runs": generated_runs,
        "max_runs": max_runs,
        "parallel_workers": worker_count,
        "optimized_keys": list(TRADING_TAB_OPTIMIZED_KEYS),
        "fixed_trading_keys": list(FIXED_TRADING_TAB_KEYS),
        "best_settings": best_settings,
        "best_reliable_result": public_trading_result_row(best),
        "best_profit_result": public_trading_result_row(best_profit),
        "top_results": [public_trading_result_row(row) for row in ranked[:10]],
        "source_metadata": source_metadata or {},
    }


def trading_candidate_ranges(settings, config=None):
    supplied = dict((config or {}).get("candidate_ranges") or (config or {}).get("ranges") or {})
    ranges = {}
    for key in TRADING_TAB_OPTIMIZED_KEYS:
        if key in supplied:
            ranges[key] = normalize_trading_values(key, supplied[key], settings)
        else:
            ranges[key] = default_trading_values(key, settings)
    return ranges


def default_trading_values(key, settings):
    value = settings.get(key)
    if key in {"buy_limit_score_low", "market_entry_score"}:
        base = _int_value(value, 50 if key == "buy_limit_score_low" else 60)
        return bounded_ints([base - 10, base - 5, base, base + 5, base + 10], 0, 100)
    if "percent" in key or "position" in key or "wick" in key:
        base = _int_value(value, 50)
        return bounded_ints([base - 10, base - 5, base, base + 5, base + 10], 0, 100)
    if key in {"volume_previous_multiplier", "avg_volume_minimum_multiplier", "volume_pickup_avg_multiplier"}:
        base = _float_value(value, 0.8)
        return bounded_floats([base - 0.2, base - 0.1, base, base + 0.1, base + 0.2], 0.1, 3.0)
    if key in {"large_candle_multiplier", "move_from_low_max_multiplier", "gap_spike_multiplier"}:
        base = _float_value(value, 1.5)
        return bounded_floats([base - 0.3, base - 0.15, base, base + 0.15, base + 0.3], 0.1, 5.0)
    if key == "buy_limit_offset_multiplier":
        base = _float_value(value, 0.15)
        return bounded_floats([base - 0.05, base, base + 0.05, base + 0.1], 0.01, 1.0)
    if key in {"minimum_offset", "maximum_offset"}:
        base = _float_value(value, 1 if key == "minimum_offset" else 2)
        return bounded_floats([base - 1, base - 0.5, base, base + 0.5, base + 1], 0.05, 20)
    if key == "enable_chop_filter":
        current = enabled_text(value)
        return unique_texts([current, "Enabled", "Disabled"])
    if key == "chop_lookback_candles":
        base = _int_value(value, 3)
        return bounded_ints([base - 1, base, base + 1, 3, 4, 5], 2, 12)
    if key == "chop_overlap_count":
        base = _int_value(value, 2)
        return bounded_ints([base - 1, base, base + 1, 1, 2, 3], 1, 8)
    if key == "missed_limit_cooldown_candles":
        base = _int_value(value, 0)
        return bounded_ints([0, 1, 2, base], 0, 8)
    return [value]


def build_trading_candidates(base_settings, ranges):
    candidates = {candidate_key(base_settings): dict(base_settings)}
    add_coordinate_candidates(candidates, base_settings, ranges)
    add_pair_candidates(candidates, base_settings, ranges)
    add_profile_candidates(candidates, base_settings, ranges)
    return [candidate for candidate in candidates.values() if valid_trading_candidate(candidate)]


def add_coordinate_candidates(candidates, base_settings, ranges):
    for key, values in ranges.items():
        for value in values:
            candidate = dict(base_settings)
            candidate[key] = value
            candidates[candidate_key(candidate)] = candidate


def add_pair_candidates(candidates, base_settings, ranges):
    pair_groups = [
        ("buy_limit_score_low", "market_entry_score"),
        ("minimum_body_percent", "minimum_close_position"),
        ("market_entry_minimum_body_percent", "market_entry_minimum_close_position"),
        ("trigger_upper_wick_max", "hard_rejection_upper_wick_max"),
        ("volume_previous_multiplier", "avg_volume_minimum_multiplier", "volume_pickup_avg_multiplier"),
        ("buy_limit_offset_multiplier", "minimum_offset", "maximum_offset"),
        ("enable_chop_filter", "chop_lookback_candles", "chop_overlap_count"),
    ]
    for group in pair_groups:
        for values in product(*(ranges[key] for key in group)):
            candidate = dict(base_settings)
            candidate.update(dict(zip(group, values)))
            candidates[candidate_key(candidate)] = candidate


def add_profile_candidates(candidates, base_settings, ranges):
    score_pairs = [
        (buy_limit, market)
        for buy_limit in ranges["buy_limit_score_low"]
        for market in ranges["market_entry_score"]
        if _float_value(market, 0) > _float_value(buy_limit, 0)
    ]
    body_profiles = [
        (body, close)
        for body in ranges["minimum_body_percent"]
        for close in ranges["minimum_close_position"]
        if abs(_float_value(body, 0) - _float_value(close, 0)) <= 20
    ]
    wick_pairs = [
        (trigger, hard)
        for trigger in ranges["trigger_upper_wick_max"]
        for hard in ranges["hard_rejection_upper_wick_max"]
        if _float_value(hard, 0) >= _float_value(trigger, 0)
    ]
    volume_profiles = list(product(
        ranges["volume_previous_multiplier"],
        ranges["avg_volume_minimum_multiplier"],
        ranges["volume_pickup_avg_multiplier"],
    ))[:40]
    for score in score_pairs[:30]:
        for body in body_profiles[:20]:
            for wick in wick_pairs[:20]:
                candidate = dict(base_settings)
                candidate["buy_limit_score_low"], candidate["market_entry_score"] = score
                candidate["minimum_body_percent"], candidate["minimum_close_position"] = body
                candidate["trigger_upper_wick_max"], candidate["hard_rejection_upper_wick_max"] = wick
                candidates[candidate_key(candidate)] = candidate
    for volume in volume_profiles:
        candidate = dict(base_settings)
        candidate["volume_previous_multiplier"], candidate["avg_volume_minimum_multiplier"], candidate["volume_pickup_avg_multiplier"] = volume
        candidates[candidate_key(candidate)] = candidate


def evaluate_trading_candidates(nifty, options, candidates, base_settings, progress_callback=None, workers=1, stop_requested=None):
    rows = []
    phase_rows = []
    trades_by_key = {}
    total = len(candidates)
    stage = "Trading Tests"
    emit_optimizer_progress(progress_callback, stage, 0, total, f"{stage.title()} tests starting")
    progress_step = max(1, total // 200) if total else 1
    raise_if_optimizer_stopped(stop_requested, stage.title())

    if workers <= 1 or total <= 1:
        for index, settings in enumerate(candidates, start=1):
            raise_if_optimizer_stopped(stop_requested, stage.title())
            row, phases, trades = evaluate_one_trading_candidate(
                nifty,
                options,
                settings,
                base_settings,
                index,
                stop_requested,
            )
            rows.append(row)
            phase_rows.extend(phases)
            trades_by_key[row["Candidate Key"]] = trades
            if index == 1 or index == total or index % progress_step == 0:
                emit_optimizer_progress(progress_callback, stage, index, total, f"{stage.title()} tests running")
        rows.sort(key=lambda row: row["Run No"])
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
            evaluate_one_trading_candidate,
            nifty,
            options,
            candidate,
            base_settings,
            sequence,
            stop_requested,
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
    except Exception:
        for future in pending:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    rows.sort(key=lambda row: row["Run No"])
    return rows, phase_rows, trades_by_key


def evaluate_one_trading_candidate(nifty, options, settings, base_settings, sequence, stop_requested=None):
    raise_if_optimizer_stopped(stop_requested, "Trading tab optimizer")
    candidate_options = prepare_trading_candidate_options(options, settings)
    final_balance, trades = run_backtest_in_memory(
        nifty,
        candidate_options,
        settings,
        copy_frames=False,
        stop_requested=stop_requested,
    )
    row, phases = summarize_trading_candidate(settings, trades, base_settings, final_balance, sequence)
    return row, phases, trades


def prepare_trading_candidate_options(options, settings):
    prepared = []
    scoring_settings = option_scoring_settings(settings)
    for option in options:
        candidate = option.copy(deep=False)
        candidate.attrs.update(dict(getattr(option, "attrs", {}) or {}))
        candidate.attrs["_fast_ohlcv_settings"] = scoring_settings
        candidate.attrs["_option_scoring_settings"] = scoring_settings
        prepared.append(candidate)
    return prepared


def summarize_trading_candidate(settings, trades, base_settings, final_balance, sequence):
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
    phases = summarize_phases(trades)
    stoploss_count = sum(1 for trade in trades if "STOPLOSS" in str(trade.get("Reason", "")).upper())
    whole_day_score = whole_day_reliability_score(phases, win_rate, trade_count, max_drawdown, gross_profit, net_pnl, stoploss_count)
    changed = changed_settings(settings, base_settings)
    score = (
        net_pnl
        - max_drawdown * 1.2
        + min(profit_factor, 5) * 25
        + win_rate * 0.4
        + whole_day_score * 1.8
        - stoploss_count * 5
        - max(0, len(changed) - 6) * 2
    )
    row = {
        "Candidate Key": candidate_key(settings),
        "Run No": sequence,
        "Changed Settings": ", ".join(changed) or "BASELINE",
        "Changed Count": len(changed),
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
        "Max Drawdown": round(max_drawdown, 2),
        "Stoploss Trades": stoploss_count,
        "Phase Coverage %": phases["coverage_percent"],
        "Positive Phase %": phases["positive_percent"],
        "Whole Day Score": round(whole_day_score, 4),
        "Reliable Score": round(score, 4),
        "Reliability Notes": reliability_notes(trade_count, phases, len(changed)),
    }
    for key in TRADING_TAB_OPTIMIZED_KEYS:
        row[key] = settings.get(key, "")
    phase_rows = [
        {
            "Candidate Key": row["Candidate Key"],
            "Changed Settings": row["Changed Settings"],
            **phase,
        }
        for phase in phases["rows"]
    ]
    return row, phase_rows


def write_trading_optimizer_workbook(path, ranked, phase_rows, best_trades, ranges, base_settings, best, best_profit, source_metadata):
    guide_rows = [
        {"Field": "Purpose", "Value": "Optimize copied Backtest Trading-tab settings against one full-day dataset."},
        {"Field": "Strategy Safety", "Value": "Code and strategy formulas are not changed; only candidate setting values are tested in memory."},
        {"Field": "Fixed Trading Keys", "Value": ", ".join(FIXED_TRADING_TAB_KEYS)},
        {"Field": "Ranking", "Value": "Reliable Score balances net PnL, drawdown, win rate, full-day phase performance, and setting complexity."},
        {"Field": "Data Source", "Value": source_metadata.get("data_source_label", source_metadata.get("data_source", ""))},
        {"Field": "Generated At", "Value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
    ]
    best_rows = [
        {"Selection": "Best Reliable", **public_trading_result_row(best)},
        {"Selection": "Best Net Profit", **public_trading_result_row(best_profit)},
    ]
    ranges_rows = [
        {"Setting": key, "Candidates": ", ".join(str(value) for value in values), "Count": len(values)}
        for key, values in ranges.items()
    ]
    settings_rows = [{"Setting": key, "Value": value} for key, value in sorted(base_settings.items())]
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(guide_rows).to_excel(writer, sheet_name="Optimizer Guide", index=False)
        pd.DataFrame(best_rows).to_excel(writer, sheet_name="Best Trading Settings", index=False)
        pd.DataFrame(ranked).to_excel(writer, sheet_name="Ranked Results", index=False)
        pd.DataFrame(phase_rows).to_excel(writer, sheet_name="Phase Breakdown", index=False)
        pd.DataFrame(best_trades or []).to_excel(writer, sheet_name="Best Trades", index=False)
        pd.DataFrame(ranges_rows).to_excel(writer, sheet_name="Candidate Ranges", index=False)
        pd.DataFrame(settings_rows).to_excel(writer, sheet_name="Base Settings", index=False)


def best_settings_from_trading_row(base_settings, row):
    settings = dict(base_settings)
    for key in TRADING_TAB_OPTIMIZED_KEYS:
        if row and key in row:
            settings[key] = row[key]
    return settings


def public_trading_result_row(row):
    if not row:
        return {}
    public_keys = [
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
        "Changed Settings",
        "Changed Count",
    ]
    public = {key: row.get(key, "") for key in public_keys}
    for key in TRADING_TAB_OPTIMIZED_KEYS:
        public[key] = row.get(key, "")
    return public


def valid_trading_candidate(candidate):
    buy_limit = _float_value(candidate.get("buy_limit_score_low"), 0)
    market = _float_value(candidate.get("market_entry_score"), 0)
    trigger_wick = _float_value(candidate.get("trigger_upper_wick_max"), 0)
    hard_wick = _float_value(candidate.get("hard_rejection_upper_wick_max"), 0)
    minimum_offset = _float_value(candidate.get("minimum_offset"), 0)
    maximum_offset = _float_value(candidate.get("maximum_offset"), 0)
    chop_lookback = _int_value(candidate.get("chop_lookback_candles"), 0)
    chop_overlap = _int_value(candidate.get("chop_overlap_count"), 0)
    return (
        market > buy_limit
        and hard_wick >= trigger_wick
        and maximum_offset >= minimum_offset
        and chop_lookback >= chop_overlap
    )


def changed_settings(settings, base_settings):
    changed = []
    for key in TRADING_TAB_OPTIMIZED_KEYS:
        if comparable(settings.get(key)) != comparable(base_settings.get(key)):
            changed.append(key)
    return changed


def candidate_key(settings):
    return "|".join(str(settings.get(key, "")) for key in TRADING_TAB_OPTIMIZED_KEYS)


def reliability_notes(trade_count, phases, changed_count):
    notes = []
    if trade_count < 2:
        notes.append("LOW_SAMPLE")
    if phases["coverage_percent"] < 66.67:
        notes.append("LIMITED_DAY_COVERAGE")
    if phases["positive_percent"] < 33.34:
        notes.append("WEAK_PHASE_PROFIT")
    if changed_count > 8:
        notes.append("MANY_SETTING_CHANGES")
    return ", ".join(notes) or "OK"


def normalize_trading_values(key, values, settings):
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    if key == "enable_chop_filter":
        return unique_texts([enabled_text(value) for value in values])
    if key in {"chop_lookback_candles", "chop_overlap_count", "missed_limit_cooldown_candles"}:
        parsed = [_int_value(value, None) for value in values]
    else:
        parsed = [_float_value(value, None) for value in values]
    clean = [value for value in parsed if value is not None]
    return unique_sorted(clean) or [settings.get(key)]


def bounded_ints(values, lower, upper):
    return unique_sorted([max(lower, min(upper, _int_value(value, lower))) for value in values])


def bounded_floats(values, lower, upper):
    return unique_sorted([round(max(lower, min(upper, _float_value(value, lower))), 2) for value in values])


def unique_texts(values):
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip() or "Disabled"
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def enabled_text(value):
    text = str(value or "").strip().lower()
    return "Enabled" if text in {"1", "true", "yes", "on", "enabled"} else "Disabled"


def comparable(value):
    if isinstance(value, str):
        return value.strip().lower()
    return value
