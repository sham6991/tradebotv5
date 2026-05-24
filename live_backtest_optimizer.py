from datetime import datetime, time, timedelta
from statistics import median

import pandas as pd

from indicators import clean_and_add_indicators
from reporting import timestamped_file


OPTIMIZED_SETTING_KEYS = [
    "rsi_reversal_bullish",
    "rsi_reversal_bearish",
]

BULLISH_RSI_CANDIDATES = tuple(range(50, 86))
BEARISH_RSI_CANDIDATES = tuple(range(15, 51))
BULLISH_TARGET_DIFF = 20.0
BEARISH_TARGET_DIFF = -15.0
MIN_EFFICIENT_SETUPS = 3


def _float_value(value, default=0.0):
    try:
        parsed = pd.to_numeric(value, errors="coerce")
        if pd.isna(parsed):
            return float(default)
        return float(parsed)
    except (TypeError, ValueError):
        return float(default)


def _round_metric(value, digits=2):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return 0


def _minutes_between(start, end, interval):
    start_time = pd.to_datetime(start, errors="coerce")
    end_time = pd.to_datetime(end, errors="coerce")
    if not pd.isna(start_time) and not pd.isna(end_time):
        minutes = (end_time - start_time).total_seconds() / 60
        return max(minutes, 0)
    text = str(interval or "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return float(digits or 3)


def setting_candidates(key, value=None):
    if key == "rsi_reversal_bullish":
        return list(BULLISH_RSI_CANDIDATES)
    if key == "rsi_reversal_bearish":
        return list(BEARISH_RSI_CANDIDATES)
    if value in ("", None):
        return [value]
    try:
        return [float(value)]
    except (TypeError, ValueError):
        return [value]


def prepare_day_frame(frame, settings=None, option_contract=None, option_data=False):
    if frame is None or frame.empty:
        return pd.DataFrame()
    prepared = clean_and_add_indicators(frame.copy())
    required = {"datetime", "EMA20", "EMA50", "RSI"}
    if not required.issubset(set(prepared.columns)):
        missing = ", ".join(sorted(required - set(prepared.columns)))
        raise ValueError(f"NIFTY historical candles missing required columns after indicator enrichment: {missing}")
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


def date_range_from_months(months, end_date=None):
    months = int(months or 1)
    if months not in {1, 2, 3, 6}:
        raise ValueError("Date range must be last 1, 2, 3, or 6 months.")
    end = pd.to_datetime(end_date or datetime.now().date(), errors="raise").date()
    start = (pd.Timestamp(end) - pd.DateOffset(months=months)).date()
    return str(start), str(end)


def fetch_day_data(zerodha, trade_date, nifty_token, interval, settings=None):
    from_dt = datetime.combine(trade_date, time(9, 15))
    to_dt = datetime.combine(trade_date, time(15, 30))
    return prepare_day_frame(
        zerodha.historical_candles(nifty_token, from_dt, to_dt, interval=interval),
        settings=settings,
    )


def ema_diff(row):
    return _float_value(row.get("EMA20", 0)) - _float_value(row.get("EMA50", 0))


def event_setup_matches(row, threshold, side):
    diff = ema_diff(row)
    rsi = _float_value(row.get("RSI", 0))
    if side == "bullish":
        return diff < 0 and rsi >= threshold
    return diff > 0 and rsi <= threshold


def reversal_outcome(frame, setup_index, side, interval):
    setup = frame.iloc[setup_index]
    setup_time = setup.get("datetime", "")
    target_diff = BULLISH_TARGET_DIFF if side == "bullish" else BEARISH_TARGET_DIFF
    confirm_index = None
    target_index = None

    for index in range(setup_index + 1, len(frame)):
        diff = ema_diff(frame.iloc[index])
        if side == "bullish":
            confirmed = diff >= 0
            targeted = diff >= target_diff
        else:
            confirmed = diff <= 0
            targeted = diff <= target_diff
        if confirmed and confirm_index is None:
            confirm_index = index
        if targeted:
            target_index = index
            if confirm_index is None:
                confirm_index = index
            break

    next_row = frame.iloc[setup_index + 1] if setup_index + 1 < len(frame) else None
    confirm_row = frame.iloc[confirm_index] if confirm_index is not None else None
    target_row = frame.iloc[target_index] if target_index is not None else None
    return {
        "Next Candle Time": next_row.get("datetime", "") if next_row is not None else "",
        "Next EMA Diff": _round_metric(ema_diff(next_row)) if next_row is not None else "",
        "Confirmed": confirm_index is not None,
        "Target Crossed": target_index is not None,
        "Confirm Time": confirm_row.get("datetime", "") if confirm_row is not None else "",
        "Target Time": target_row.get("datetime", "") if target_row is not None else "",
        "Confirm Minutes": _round_metric(_minutes_between(setup_time, confirm_row.get("datetime", ""), interval)) if confirm_row is not None else "",
        "Target Minutes": _round_metric(_minutes_between(setup_time, target_row.get("datetime", ""), interval)) if target_row is not None else "",
        "Confirm EMA Diff": _round_metric(ema_diff(confirm_row)) if confirm_row is not None else "",
        "Target EMA Diff": _round_metric(ema_diff(target_row)) if target_row is not None else "",
        "Next Candle Confirmed": confirm_index == setup_index + 1,
    }


def build_event_rows(day_data, threshold, side, interval):
    rows = []
    for item in day_data:
        frame = item["nifty"].reset_index(drop=True)
        for index in range(0, len(frame) - 1):
            row = frame.iloc[index]
            if not event_setup_matches(row, threshold, side):
                continue
            outcome = reversal_outcome(frame, index, side, interval)
            rows.append({
                "Date": item["date"],
                "Side": side.title(),
                "RSI Threshold": threshold,
                "Setup Time": row.get("datetime", ""),
                "Setup RSI": _round_metric(row.get("RSI", 0)),
                "Setup EMA20": _round_metric(row.get("EMA20", 0)),
                "Setup EMA50": _round_metric(row.get("EMA50", 0)),
                "Setup EMA Diff": _round_metric(ema_diff(row)),
                **outcome,
            })
    return rows


def summarize_events(events):
    setups = len(events)
    confirmed = [row for row in events if row.get("Confirmed")]
    target_crossed = [row for row in events if row.get("Target Crossed")]
    next_confirmed = [row for row in events if row.get("Next Candle Confirmed")]
    confirm_minutes = [_float_value(row.get("Confirm Minutes")) for row in confirmed if row.get("Confirm Minutes") != ""]
    target_minutes = [_float_value(row.get("Target Minutes")) for row in target_crossed if row.get("Target Minutes") != ""]
    success_rate = (len(confirmed) / setups) * 100 if setups else 0
    target_rate = (len(target_crossed) / setups) * 100 if setups else 0
    next_rate = (len(next_confirmed) / setups) * 100 if setups else 0
    return {
        "Setups": setups,
        "Confirmed": len(confirmed),
        "Confirmation Rate %": _round_metric(success_rate),
        "Next Candle Confirmed": len(next_confirmed),
        "Next Candle Confirm %": _round_metric(next_rate),
        "Target Crossed": len(target_crossed),
        "Target Cross Rate %": _round_metric(target_rate),
        "Average Confirm Minutes": _round_metric(sum(confirm_minutes) / len(confirm_minutes)) if confirm_minutes else "",
        "Median Confirm Minutes": _round_metric(median(confirm_minutes)) if confirm_minutes else "",
        "Average Target Minutes": _round_metric(sum(target_minutes) / len(target_minutes)) if target_minutes else "",
        "Median Target Minutes": _round_metric(median(target_minutes)) if target_minutes else "",
    }


def efficiency_score(summary):
    setups = _float_value(summary.get("Setups", 0))
    sample_penalty = max(0, MIN_EFFICIENT_SETUPS - setups) * 250
    median_confirm = _float_value(summary.get("Median Confirm Minutes", 999), 999)
    median_target = _float_value(summary.get("Median Target Minutes", 999), 999)
    if not summary.get("Median Confirm Minutes"):
        median_confirm = 999
    if not summary.get("Median Target Minutes"):
        median_target = 999
    return _round_metric(
        (_float_value(summary.get("Confirmation Rate %", 0)) * 10)
        + (_float_value(summary.get("Target Cross Rate %", 0)) * 6)
        + (_float_value(summary.get("Next Candle Confirm %", 0)) * 4)
        + min(setups, 20) * 8
        - (median_confirm * 4)
        - (median_target * 2)
        - sample_penalty,
        4,
    )


def evaluate_threshold(day_data, threshold, side, interval):
    events = build_event_rows(day_data, threshold, side, interval)
    summary = summarize_events(events)
    summary.update({
        "Side": side.title(),
        "RSI Threshold": threshold,
        "Efficiency Score": efficiency_score(summary),
        "Reliable Sample": summary["Setups"] >= MIN_EFFICIENT_SETUPS,
    })
    return summary, events


def optimize_side(day_data, side, interval):
    candidates = BULLISH_RSI_CANDIDATES if side == "bullish" else BEARISH_RSI_CANDIDATES
    rows = []
    events_by_threshold = {}
    for threshold in candidates:
        summary, events = evaluate_threshold(day_data, threshold, side, interval)
        rows.append(summary)
        events_by_threshold[threshold] = events
    rows = sorted(
        rows,
        key=lambda row: (
            bool(row.get("Reliable Sample")),
            _float_value(row.get("Efficiency Score", 0)),
            _float_value(row.get("Confirmation Rate %", 0)),
            _float_value(row.get("Target Cross Rate %", 0)),
            -_float_value(row.get("Median Confirm Minutes", 999), 999),
        ),
        reverse=True,
    )
    best = rows[0] if rows else {}
    return best, rows, events_by_threshold.get(best.get("RSI Threshold"), [])


def workbook_guide_rows():
    return [
        {
            "Sheet": "Summary",
            "Purpose": "Final NIFTY-only RSI reversal values and headline efficiency metrics.",
            "Selection Role": "Decision",
        },
        {
            "Sheet": "Optimized RSI Values",
            "Purpose": "Bullish and bearish RSI reversal thresholds selected by the optimizer.",
            "Selection Role": "Decision",
        },
        {
            "Sheet": "Candidate Runs",
            "Purpose": "Every RSI threshold scored by confirmation rate, target-cross rate, and time efficiency.",
            "Selection Role": "Decision Trace",
        },
        {
            "Sheet": "Bullish Events",
            "Purpose": "Best bullish setup rows where EMA20 < EMA50 and RSI met the selected bullish threshold.",
            "Selection Role": "Audit",
        },
        {
            "Sheet": "Bearish Events",
            "Purpose": "Best bearish setup rows where EMA20 > EMA50 and RSI met the selected bearish threshold.",
            "Selection Role": "Audit",
        },
        {
            "Sheet": "Fetch Log",
            "Purpose": "Day-by-day NIFTY historical candle availability.",
            "Selection Role": "Audit",
        },
    ]


def run_live_backtest_optimizer(
    zerodha,
    nifty_token,
    option_contracts=None,
    start_date=None,
    end_date=None,
    interval="3minute",
    base_settings=None,
    output_dir=".",
):
    if not zerodha:
        raise ValueError("Connect Zerodha first.")
    if not nifty_token:
        raise ValueError("NIFTY token is required.")
    if not start_date or not end_date:
        raise ValueError("Start date and end date are required.")

    base_settings = {**(base_settings or {}), "chart_interval": interval}
    fetch_rows = []
    day_data = []
    for trade_date in date_range(start_date, end_date):
        nifty = fetch_day_data(zerodha, trade_date, nifty_token, interval, base_settings)
        usable = not nifty.empty and len(nifty) > 2
        fetch_rows.append({
            "Date": str(trade_date),
            "NIFTY Candles": len(nifty),
            "Used": usable,
        })
        if usable:
            day_data.append({"date": str(trade_date), "nifty": nifty})

    if not day_data:
        raise ValueError("No usable NIFTY historical candles found for the selected date range.")

    bullish_best, bullish_rows, bullish_events = optimize_side(day_data, "bullish", interval)
    bearish_best, bearish_rows, bearish_events = optimize_side(day_data, "bearish", interval)
    best_settings = {
        "rsi_reversal_bullish": bullish_best.get("RSI Threshold", ""),
        "rsi_reversal_bearish": bearish_best.get("RSI Threshold", ""),
        "chart_interval": interval,
    }
    path = timestamped_file("nifty_optimizer", output_dir)
    candidate_rows = sorted(
        bullish_rows + bearish_rows,
        key=lambda row: (row.get("Side", ""), _float_value(row.get("Efficiency Score", 0))),
        reverse=True,
    )
    optimized_rows = [
        {"Setting": "rsi_reversal_bullish", **bullish_best},
        {"Setting": "rsi_reversal_bearish", **bearish_best},
    ]
    summary = {
        "Bullish RSI Reversal": best_settings["rsi_reversal_bullish"],
        "Bearish RSI Reversal": best_settings["rsi_reversal_bearish"],
        "Bullish Efficiency Score": bullish_best.get("Efficiency Score", 0),
        "Bearish Efficiency Score": bearish_best.get("Efficiency Score", 0),
        "Bullish Confirmation Rate %": bullish_best.get("Confirmation Rate %", 0),
        "Bearish Confirmation Rate %": bearish_best.get("Confirmation Rate %", 0),
        "Bullish Target Cross Rate %": bullish_best.get("Target Cross Rate %", 0),
        "Bearish Target Cross Rate %": bearish_best.get("Target Cross Rate %", 0),
    }
    summary_rows = [{
        "Output File": path,
        "Start Date": str(start_date),
        "End Date": str(end_date),
        "Fetched Data Interval": interval,
        "Optimizer": "NIFTY RSI reversal",
        "Options Fetched": "No",
        "Paper/Real Settings Applied": "No",
        **summary,
    }]

    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(workbook_guide_rows()).to_excel(writer, sheet_name="Workbook Guide", index=False)
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Summary", index=False)
        pd.DataFrame(optimized_rows).to_excel(writer, sheet_name="Optimized RSI Values", index=False)
        pd.DataFrame(candidate_rows).to_excel(writer, sheet_name="Candidate Runs", index=False)
        pd.DataFrame(bullish_events or [{"Message": "No bullish events for selected threshold."}]).to_excel(writer, sheet_name="Bullish Events", index=False)
        pd.DataFrame(bearish_events or [{"Message": "No bearish events for selected threshold."}]).to_excel(writer, sheet_name="Bearish Events", index=False)
        pd.DataFrame(fetch_rows).to_excel(writer, sheet_name="Fetch Log", index=False)

    return {
        "output_path": path,
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days_requested": len(fetch_rows),
        "days_used": len(day_data),
        "runs": len(candidate_rows),
        "best_settings": best_settings,
        "summary": summary,
        "bullish_best": bullish_best,
        "bearish_best": bearish_best,
    }
