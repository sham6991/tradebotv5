from trailing_stop import validate_trailing_stop_settings


FAST_OHLCV_RELATIONSHIP_RULES = (
    ("market_entry_score", "buy_limit_score_low", ">", "Market Entry Score must be greater than Buy Limit Score Low."),
    ("aggressive_entry_score", "buy_limit_score_low", ">=", "Aggressive Entry Score must be at least Buy Limit Score Low."),
    ("hard_rejection_upper_wick_max", "trigger_upper_wick_max", ">", "Hard Rejection Wick Max must be greater than Trigger Upper Wick Max."),
    ("trigger_upper_wick_max", "aggressive_upper_wick_max", ">=", "Trigger Upper Wick Max must be at least Aggressive Wick Max."),
    ("market_entry_minimum_body_percent", "minimum_body_percent", ">=", "Market Body % must be at least Minimum Body %."),
    ("aggressive_minimum_body_percent", "minimum_body_percent", ">=", "Aggressive Body % must be at least Minimum Body %."),
    ("market_entry_minimum_close_position", "minimum_close_position", ">=", "Market Close Position must be at least Minimum Close Position."),
    ("aggressive_minimum_close_position", "minimum_close_position", ">=", "Aggressive Close Position must be at least Minimum Close Position."),
    ("move_from_low_max_multiplier", "aggressive_move_from_low_max_multiplier", ">=", "Move From Low Max must be at least Aggressive Move From Low."),
    ("maximum_offset", "minimum_offset", ">=", "Maximum Offset must be at least Minimum Offset."),
    ("chop_lookback_candles", "chop_overlap_count", ">=", "Chop Lookback Candles must be at least Chop Overlap Count."),
)


def validate_fast_ohlcv_settings(values):
    errors = []
    for left_key, right_key, operator, message in FAST_OHLCV_RELATIONSHIP_RULES:
        if left_key not in values or right_key not in values:
            continue
        left = _number(values.get(left_key))
        right = _number(values.get(right_key))
        if left is None or right is None:
            continue
        if operator == ">" and not left > right:
            errors.append(message)
        elif operator == ">=" and not left >= right:
            errors.append(message)

    buy_limit = _number(values.get("buy_limit_score_low"))
    market = _number(values.get("market_entry_score"))
    if buy_limit is not None and market is not None and buy_limit >= market:
        errors.append("Buy Limit Score Low must stay below Market Entry Score.")

    min_offset = _number(values.get("minimum_offset"))
    max_offset = _number(values.get("maximum_offset"))
    if min_offset is not None and max_offset is not None and min_offset < 0:
        errors.append("Minimum Offset cannot be negative.")
    if max_offset is not None and max_offset < 0:
        errors.append("Maximum Offset cannot be negative.")

    stoploss_buffer = _number(values.get("stoploss_limit_buffer_points"))
    if stoploss_buffer is not None and stoploss_buffer <= 0:
        errors.append("Stoploss Limit Buffer Points must be greater than zero.")

    live_entry_limit_buffer = _number(values.get("live_option_market_entry_limit_buffer_points"))
    if live_entry_limit_buffer is not None and live_entry_limit_buffer <= 0:
        errors.append("Live Option Entry Limit Buffer Points must be greater than zero.")

    fill_mode = str(values.get("backtest_limit_fill_mode", "CONSERVATIVE") or "").strip().upper()
    if fill_mode and fill_mode not in {"CONSERVATIVE", "SIMPLE", "STRICT"}:
        errors.append("Backtest Limit Fill Mode must be CONSERVATIVE, SIMPLE, or STRICT.")

    trend_set = str(values.get("trend_set", "Auto") or "").strip().upper()
    if trend_set and trend_set not in {"AUTO", "BULLISH", "BEARISH", "BULL", "BEAR", "CE", "PE"}:
        errors.append("Trend Set must be Auto, Bullish, or Bearish.")

    errors.extend(validate_trailing_stop_settings(values))
    return errors


def raise_for_fast_ohlcv_settings(values):
    errors = validate_fast_ohlcv_settings(values or {})
    if errors:
        raise ValueError("; ".join(errors))


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
