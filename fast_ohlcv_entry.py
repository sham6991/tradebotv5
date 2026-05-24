DEFAULT_FAST_OHLCV_SETTINGS = {
    "fast_ohlcv_entry_enabled": True,
    "buy_limit_score_low": 40,
    "market_entry_score": 50,
    "aggressive_entry_score": 50,
    "trigger_upper_wick_max": 45,
    "hard_rejection_upper_wick_max": 50,
    "aggressive_upper_wick_max": 35,
    "minimum_body_percent": 20,
    "market_entry_minimum_body_percent": 25,
    "aggressive_minimum_body_percent": 25,
    "minimum_close_position": 55,
    "market_entry_minimum_close_position": 60,
    "aggressive_minimum_close_position": 65,
    "volume_previous_multiplier": 0.80,
    "avg_volume_minimum_multiplier": 0.50,
    "volume_pickup_avg_multiplier": 0.70,
    "large_candle_multiplier": 2.2,
    "move_from_low_max_multiplier": 1.10,
    "aggressive_move_from_low_max_multiplier": 0.90,
    "gap_spike_multiplier": 1.2,
    "buy_limit_offset_multiplier": 0.15,
    "minimum_offset": 1,
    "maximum_offset": 2,
    "buy_limit_validity_seconds": 30,
    "backtest_limit_fill_mode": "CONSERVATIVE",
    "enable_chop_filter": False,
    "chop_lookback_candles": 3,
    "chop_overlap_count": 2,
    "aggressive_live_entry_enabled": False,
    "aggressive_setup_score": 40,
    "one_entry_attempt_per_candle": True,
    "missed_limit_cooldown_candles": 0,
    "max_spread_points": 2.0,
}


FAST_ENTRY_REPORT_COLUMNS = (
    "Entry Type",
    "Final Decision",
    "Decision Reason",
    "Setup Status",
    "Early Score",
    "Price Stopped Falling Points",
    "Green Candle Points",
    "Previous High Attack Points",
    "Volume Pickup Points",
    "Main Fast Trigger Passed",
    "Rejection Active",
    "Rejection Reason",
    "Chop Filter Active",
    "Chop Filter Reason",
    "Gap Spike Warning",
    "Aggressive Mode Enabled",
    "Live Candle Entry",
    "Spread",
    "Spread Allowed",
    "CurrentRange",
    "SignedBody",
    "BodyAbs",
    "BodyPercent",
    "ClosePosition",
    "UpperWick",
    "UpperWickPercent",
    "LowerWick",
    "LowerWickPercent",
    "AvgRange10",
    "AvgVolume10",
    "PreviousHigh",
    "PreviousLow",
    "PreviousClose",
    "PreviousVolume",
    "RecentHigh3",
    "RecentLow3",
    "MoveFromLow",
    "GapFromPreviousClose",
    "Buy Limit Price",
    "Limit Offset",
    "Limit Validity Seconds",
    "Limit Fill Status",
    "Backtest Limit Fill Mode",
)


def fast_ohlcv_settings(settings=None):
    values = dict(DEFAULT_FAST_OHLCV_SETTINGS)
    if isinstance(settings, dict):
        for key in values:
            if key in settings:
                values[key] = settings[key]
    parsed = {}
    for key, default in DEFAULT_FAST_OHLCV_SETTINGS.items():
        if isinstance(default, bool):
            parsed[key] = _bool_setting(values.get(key), default)
        elif isinstance(default, int) and not isinstance(default, bool):
            parsed[key] = _int_setting(values.get(key), default)
        elif isinstance(default, float):
            parsed[key] = _float_setting(values.get(key), default)
        else:
            parsed[key] = str(values.get(key, default) or default).strip().upper()
    return parsed


def calculate_candle_features(df, i, settings=None, spread=None):
    import pandas as pd

    score_settings = fast_ohlcv_settings(settings)
    empty = _empty_feature_row(score_settings, spread)
    if df is None or i <= 0 or i >= len(df):
        empty["Decision Reason"] = "insufficient_candle_history"
        empty["Rejection Reason"] = "insufficient_candle_history"
        empty["Rejection Active"] = "YES"
        return empty

    row = df.iloc[i]
    previous = df.iloc[i - 1]
    prior = df.iloc[max(0, i - 10):i]
    recent = df.iloc[max(0, i - 3):i]
    if prior.empty or recent.empty:
        empty["Decision Reason"] = "insufficient_candle_history"
        empty["Rejection Reason"] = "insufficient_candle_history"
        empty["Rejection Active"] = "YES"
        return empty

    open_price = _num(row.get("open", row.get("Open", 0)))
    high = _num(row.get("high", row.get("High", 0)))
    low = _num(row.get("low", row.get("Low", 0)))
    close = _num(row.get("close", row.get("Close", 0)))
    volume = _num(row.get("volume", row.get("Volume", 0)))
    previous_high = _num(previous.get("high", previous.get("High", 0)))
    previous_low = _num(previous.get("low", previous.get("Low", 0)))
    previous_close = _num(previous.get("close", previous.get("Close", 0)))
    previous_open = _num(previous.get("open", previous.get("Open", 0)))
    previous_volume = _num(previous.get("volume", previous.get("Volume", 0)))

    current_range = high - low
    signed_body = close - open_price
    body_abs = abs(signed_body)
    upper_wick = max(high - max(open_price, close), 0)
    lower_wick = max(min(open_price, close) - low, 0)

    prior_ranges = (
        pd.to_numeric(prior.get("high", prior.get("High")), errors="coerce")
        - pd.to_numeric(prior.get("low", prior.get("Low")), errors="coerce")
    )
    prior_volumes = pd.to_numeric(prior.get("volume", prior.get("Volume", 0)), errors="coerce")
    avg_range10 = _safe_mean(prior_ranges)
    avg_volume10 = _safe_mean(prior_volumes)
    recent_high3 = _safe_max(pd.to_numeric(recent.get("high", recent.get("High")), errors="coerce"))
    recent_low3 = _safe_min(pd.to_numeric(recent.get("low", recent.get("Low")), errors="coerce"))

    body_percent = _percent(body_abs, current_range)
    close_position = _percent(close - low, current_range)
    upper_wick_percent = _percent(upper_wick, current_range)
    lower_wick_percent = _percent(lower_wick, current_range)
    gap_from_previous_close = abs(open_price - previous_close)

    gap_spike_warning = (
        avg_range10 > 0
        and gap_from_previous_close > avg_range10 * score_settings["gap_spike_multiplier"]
    )

    chop_active, chop_reason = _chop_filter(df, i, avg_volume10, volume, score_settings)

    return {
        **empty,
        "Open": open_price,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
        "CurrentRange": current_range,
        "SignedBody": signed_body,
        "BodyAbs": body_abs,
        "BodyPercent": body_percent,
        "ClosePosition": close_position,
        "UpperWick": max(upper_wick, 0),
        "UpperWickPercent": upper_wick_percent,
        "LowerWick": max(lower_wick, 0),
        "LowerWickPercent": lower_wick_percent,
        "AvgRange10": avg_range10,
        "AvgVolume10": avg_volume10,
        "PreviousHigh": previous_high,
        "PreviousLow": previous_low,
        "PreviousClose": previous_close,
        "PreviousOpen": previous_open,
        "PreviousVolume": previous_volume,
        "RecentHigh3": recent_high3,
        "RecentLow3": recent_low3,
        "MoveFromLow": close - low,
        "GapFromPreviousClose": gap_from_previous_close,
        "Gap Spike Warning": _yes_no(gap_spike_warning),
        "Chop Filter Active": _yes_no(chop_active),
        "Chop Filter Reason": chop_reason,
        "Spread": "" if spread in ("", None) else _num(spread),
        "Spread Allowed": _yes_no(_spread_allowed(spread, score_settings)),
        "IsGreen": close > open_price,
        "HigherHighContext": high > previous_high,
        "HigherLowContext": low >= previous_low or low >= recent_low3,
        "RecoveryContinuation": previous_close > previous_open,
    }


def calculate_early_score(features, settings=None):
    score_settings = fast_ohlcv_settings(settings)
    price_points = 15 if features["Low"] >= features["RecentLow3"] else 0
    green_points = 15 if features["IsGreen"] else 0
    high_attack = 0
    if features["High"] > features["PreviousHigh"]:
        high_attack += 10
    if features["Close"] >= features["PreviousHigh"] - features["AvgRange10"] * 0.25:
        high_attack += 5
    volume_points = 0
    if features["PreviousVolume"] > 0 and features["Volume"] > features["PreviousVolume"]:
        volume_points += 10
    elif features["PreviousVolume"] <= 0 and features["Volume"] > features["AvgVolume10"] * score_settings["volume_pickup_avg_multiplier"]:
        volume_points += 10
    if features["Volume"] > features["AvgVolume10"] * score_settings["volume_pickup_avg_multiplier"]:
        volume_points += 5

    early_score = price_points + green_points + high_attack + volume_points
    return {
        "Early Score": early_score,
        "Price Stopped Falling Points": price_points,
        "Green Candle Points": green_points,
        "Previous High Attack Points": high_attack,
        "Volume Pickup Points": volume_points,
    }


def check_rejection_filters(features, score_row=None, settings=None):
    score_settings = fast_ohlcv_settings(settings)
    early_score = _num((score_row or {}).get("Early Score", 0))
    reasons = []

    if features["CurrentRange"] <= 0:
        reasons.append("current_range_not_positive")
    if features["AvgRange10"] <= 0:
        reasons.append("avg_range10_not_positive")
    if features["AvgVolume10"] <= 0:
        reasons.append("avg_volume10_not_positive")
    if features["Close"] <= features["Open"]:
        reasons.append("red_or_flat_candle")
    if features["Low"] < features["RecentLow3"]:
        reasons.append("recent_base_failed")
    if features["UpperWickPercent"] > score_settings["hard_rejection_upper_wick_max"]:
        reasons.append("hard_upper_wick_rejection")
    if features["UpperWickPercent"] > features["BodyPercent"]:
        reasons.append("upper_wick_bigger_than_body")
    if features["CurrentRange"] > features["AvgRange10"] * score_settings["large_candle_multiplier"]:
        reasons.append("large_candle_stretched")
    if features["MoveFromLow"] > features["AvgRange10"] * score_settings["move_from_low_max_multiplier"]:
        reasons.append("move_from_low_overextended")
    if features["Volume"] < features["AvgVolume10"] * score_settings["avg_volume_minimum_multiplier"]:
        reasons.append("volume_too_weak")
    if features["BodyPercent"] < score_settings["minimum_body_percent"]:
        reasons.append("body_percent_too_weak")
    if features["ClosePosition"] < score_settings["minimum_close_position"]:
        reasons.append("close_position_too_weak")
    if features["Gap Spike Warning"] == "YES" and early_score < 55:
        reasons.append("gap_spike_without_strong_score")
    if features["Chop Filter Active"] == "YES":
        reasons.append("chop_filter_active")
    if features["Spread Allowed"] == "NO":
        reasons.append("spread_too_wide")

    return {
        "Rejection Active": _yes_no(bool(reasons)),
        "Rejection Reason": "; ".join(reasons),
    }


def check_main_fast_trigger(features, score_row=None, settings=None):
    score_settings = fast_ohlcv_settings(settings)
    early_score = _num((score_row or {}).get("Early Score", 0))
    if features["PreviousVolume"] > 0:
        volume_condition = features["Volume"] > features["PreviousVolume"] * score_settings["volume_previous_multiplier"]
    else:
        volume_condition = features["Volume"] > features["AvgVolume10"] * score_settings["volume_pickup_avg_multiplier"]
    passed = (
        early_score >= score_settings["buy_limit_score_low"]
        and features["IsGreen"]
        and features["High"] > features["PreviousHigh"]
        and features["UpperWickPercent"] <= score_settings["trigger_upper_wick_max"]
        and features["Low"] >= features["RecentLow3"]
        and volume_condition
        and features["BodyPercent"] >= score_settings["minimum_body_percent"]
        and features["ClosePosition"] >= score_settings["minimum_close_position"]
    )
    return {"Main Fast Trigger Passed": _yes_no(passed)}


def check_aggressive_live_entry(features, score_row=None, rejection=None, ltp=None, settings=None):
    score_settings = fast_ohlcv_settings(settings)
    early_score = _num((score_row or {}).get("Early Score", 0))
    if features["PreviousVolume"] > 0:
        volume_ok = features["Volume"] > features["PreviousVolume"]
    else:
        volume_ok = features["Volume"] > features["AvgVolume10"] * score_settings["volume_pickup_avg_multiplier"]
    passed = (
        score_settings["aggressive_live_entry_enabled"]
        and early_score >= score_settings["aggressive_entry_score"]
        and ltp not in ("", None)
        and _num(ltp) > features["PreviousHigh"]
        and volume_ok
        and features["UpperWickPercent"] <= score_settings["aggressive_upper_wick_max"]
        and features["MoveFromLow"] <= features["AvgRange10"] * score_settings["aggressive_move_from_low_max_multiplier"]
        and features["Low"] >= features["RecentLow3"]
        and features["BodyPercent"] >= score_settings["aggressive_minimum_body_percent"]
        and features["ClosePosition"] >= score_settings["aggressive_minimum_close_position"]
        and (rejection or {}).get("Rejection Active") != "YES"
    )
    return passed


def calculate_buy_limit_price(features, settings=None):
    score_settings = fast_ohlcv_settings(settings)
    offset = min(
        max(features["AvgRange10"] * score_settings["buy_limit_offset_multiplier"], score_settings["minimum_offset"]),
        score_settings["maximum_offset"],
    )
    offset = round(offset, 2)
    return max(features["Close"] - offset, 0), offset


def decide_entry_type(df, i, settings=None, current_candle_closed=True, ltp=None, spread=None):
    score_settings = fast_ohlcv_settings(settings)
    features = calculate_candle_features(df, i, score_settings, spread=spread)
    score = calculate_early_score(features, score_settings)
    features = {**features, **score}
    rejection = check_rejection_filters(features, score, score_settings)
    trigger = check_main_fast_trigger(features, score, score_settings)
    buy_limit_price, limit_offset = calculate_buy_limit_price(features, score_settings)

    decision = {
        **features,
        **rejection,
        **trigger,
        "Aggressive Mode Enabled": _yes_no(score_settings["aggressive_live_entry_enabled"]),
        "Live Candle Entry": _yes_no(not current_candle_closed),
        "Entry Type": "",
        "Final Decision": "NO TRADE",
        "Decision Reason": "",
        "Setup Status": "NO TRADE",
        "Buy Limit Price": buy_limit_price,
        "Limit Offset": limit_offset,
        "Limit Validity Seconds": score_settings["buy_limit_validity_seconds"],
        "Limit Fill Status": "",
        "Backtest Limit Fill Mode": score_settings["backtest_limit_fill_mode"],
    }

    if not score_settings["fast_ohlcv_entry_enabled"]:
        return _finish(decision, "fast_ohlcv_entry_disabled")

    if not current_candle_closed:
        if not score_settings["aggressive_live_entry_enabled"]:
            if decision["Early Score"] >= score_settings["aggressive_setup_score"]:
                decision["Setup Status"] = "SETUP FORMING"
                decision["Final Decision"] = "WAIT"
                decision["Decision Reason"] = "setup_forming_aggressive_live_off"
                return _with_buy_aliases(decision)
            return _finish(decision, "live_candle_score_below_setup")
        if check_aggressive_live_entry(features, score, rejection, ltp=ltp, settings=score_settings):
            decision["Entry Type"] = "MARKET"
            decision["Final Decision"] = "MARKET ENTRY"
            decision["Setup Status"] = "AGGRESSIVE LIVE ENTRY"
            decision["Decision Reason"] = "aggressive_live_entry_passed"
            return _with_buy_aliases(decision)
        decision["Final Decision"] = "WAIT"
        decision["Decision Reason"] = rejection["Rejection Reason"] or "aggressive_live_entry_not_confirmed"
        return _with_buy_aliases(decision)

    if rejection["Rejection Active"] == "YES":
        return _finish(decision, rejection["Rejection Reason"] or "rejection_filter_active")
    if trigger["Main Fast Trigger Passed"] != "YES":
        return _finish(decision, "main_fast_trigger_failed")
    if (
        decision["Early Score"] >= score_settings["market_entry_score"]
        and decision["BodyPercent"] >= score_settings["market_entry_minimum_body_percent"]
        and decision["ClosePosition"] >= score_settings["market_entry_minimum_close_position"]
    ):
        decision["Entry Type"] = "MARKET"
        decision["Final Decision"] = "MARKET ENTRY"
        decision["Setup Status"] = "ENTRY ALLOWED"
        decision["Decision Reason"] = "market_entry_conditions_passed"
        return _with_buy_aliases(decision)
    if (
        decision["Early Score"] >= score_settings["buy_limit_score_low"]
    ):
        decision["Entry Type"] = "BUY LIMIT"
        decision["Final Decision"] = "BUY LIMIT ENTRY"
        decision["Setup Status"] = "ENTRY ALLOWED"
        decision["Decision Reason"] = "buy_limit_conditions_passed"
        return _with_buy_aliases(decision)
    return _finish(decision, "score_not_in_entry_range")


def apply_fast_ohlcv_columns(df, settings=None):
    if df is None:
        return df
    df = df.copy()
    for canonical, variants in {
        "open": ("Open",),
        "high": ("High",),
        "low": ("Low",),
        "close": ("Close",),
        "volume": ("Volume",),
    }.items():
        if canonical not in df.columns:
            for variant in variants:
                if variant in df.columns:
                    df[canonical] = df[variant]
                    break
    needed = {"open", "high", "low", "close"}
    if not needed.issubset(df.columns):
        return df

    import pandas as pd

    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0
    df["volume"] = df["volume"].fillna(0)

    rows = [decide_entry_type(df, index, settings, current_candle_closed=True) for index in range(len(df))]
    for column in FAST_ENTRY_REPORT_COLUMNS + ("Buy Entry", "Sell Entry", "Sell Score"):
        df[column] = [row.get(column, "") for row in rows]
    df.attrs["_fast_ohlcv_settings"] = fast_ohlcv_settings(settings)
    df.attrs["_option_scoring_settings"] = fast_ohlcv_settings(settings)
    return df


def backtest_limit_fill_status(next_row, buy_limit_price, mode):
    if next_row is None:
        return "MISSED"
    mode = str(mode or "CONSERVATIVE").upper()
    if mode == "STRICT":
        return "NOT_TESTED_STRICT_MODE"
    low = _num(next_row.get("low", next_row.get("Low", 0)))
    close = _num(next_row.get("close", next_row.get("Close", 0)))
    open_price = _num(next_row.get("open", next_row.get("Open", 0)))
    if mode == "SIMPLE":
        return "FILLED" if low <= buy_limit_price else "MISSED"
    if low <= buy_limit_price and close >= buy_limit_price and close > open_price:
        return "FILLED"
    return "MISSED"


def _finish(decision, reason):
    decision["Decision Reason"] = reason
    return _with_buy_aliases(decision)


def _with_buy_aliases(decision):
    decision["Buy Entry"] = "BUY" if decision.get("Final Decision") in {"MARKET ENTRY", "BUY LIMIT ENTRY"} else ""
    decision["Sell Score"] = ""
    decision["Sell Entry"] = ""
    return decision


def _empty_feature_row(settings, spread):
    spread_allowed = _spread_allowed(spread, settings)
    return {
        column: "" for column in FAST_ENTRY_REPORT_COLUMNS
    } | {
        "Open": 0,
        "High": 0,
        "Low": 0,
        "Close": 0,
        "Volume": 0,
        "CurrentRange": 0,
        "SignedBody": 0,
        "BodyAbs": 0,
        "BodyPercent": 0,
        "ClosePosition": 0,
        "UpperWick": 0,
        "UpperWickPercent": 0,
        "LowerWick": 0,
        "LowerWickPercent": 0,
        "AvgRange10": 0,
        "AvgVolume10": 0,
        "PreviousHigh": 0,
        "PreviousLow": 0,
        "PreviousClose": 0,
        "PreviousOpen": 0,
        "PreviousVolume": 0,
        "RecentHigh3": 0,
        "RecentLow3": 0,
        "MoveFromLow": 0,
        "GapFromPreviousClose": 0,
        "IsGreen": False,
        "Gap Spike Warning": "NO",
        "Chop Filter Active": "NO",
        "Spread": "" if spread in ("", None) else _num(spread),
        "Spread Allowed": _yes_no(spread_allowed),
    }


def _chop_filter(df, i, avg_volume10, volume, settings):
    if not settings["enable_chop_filter"]:
        return False, ""
    lookback = max(1, settings["chop_lookback_candles"])
    start = max(1, i - lookback + 1)
    count = 0
    for index in range(start, i + 1):
        row = df.iloc[index]
        previous = df.iloc[index - 1]
        current_high = _num(row.get("high", row.get("High", 0)))
        current_low = _num(row.get("low", row.get("Low", 0)))
        previous_high = _num(previous.get("high", previous.get("High", 0)))
        previous_low = _num(previous.get("low", previous.get("Low", 0)))
        if current_high <= previous_high and current_low >= previous_low:
            count += 1
    active = count >= settings["chop_overlap_count"] and volume <= avg_volume10
    reason = f"overlap_count_{count}_volume_below_avg" if active else ""
    return active, reason


def _spread_allowed(spread, settings):
    if spread in ("", None):
        return True
    max_spread = settings.get("max_spread_points", 0)
    return max_spread <= 0 or _num(spread) <= max_spread


def _float_setting(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int_setting(value, default):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _bool_setting(value, default=False):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "enabled"):
        return True
    if text in ("0", "false", "no", "off", "disabled"):
        return False
    return bool(default)


def _num(value):
    try:
        import pandas as pd

        parsed = pd.to_numeric(value, errors="coerce")
        return 0.0 if pd.isna(parsed) else float(parsed)
    except (TypeError, ValueError):
        return 0.0


def _percent(value, denominator):
    return (float(value) / float(denominator) * 100) if denominator else 0.0


def _safe_mean(values):
    import pandas as pd

    values = pd.to_numeric(values, errors="coerce").dropna()
    return 0.0 if values.empty else float(values.mean())


def _safe_max(values):
    import pandas as pd

    values = pd.to_numeric(values, errors="coerce").dropna()
    return 0.0 if values.empty else float(values.max())


def _safe_min(values):
    import pandas as pd

    values = pd.to_numeric(values, errors="coerce").dropna()
    return 0.0 if values.empty else float(values.min())


def _yes_no(value):
    return "YES" if bool(value) else "NO"
