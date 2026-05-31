from trailing_stop import bool_setting, trailing_settings


DEFAULT_CANDLE_LIMIT = 5
DEFAULT_TARGET_POINTS = 5.0
DEFAULT_STOPLOSS_POINTS = 5.0


def float_setting(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def int_setting(value, default):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def trailing_safeguard_settings(settings=None):
    settings = settings or {}
    return {
        "enabled": bool_setting(settings.get("trailing_sl_enabled", False))
        and bool_setting(settings.get("trailing_time_safeguard_enabled", True)),
        "candle_limit": max(1, int_setting(settings.get("trailing_time_safeguard_candles", DEFAULT_CANDLE_LIMIT), DEFAULT_CANDLE_LIMIT)),
        "target_points": max(0.0, float_setting(settings.get("trailing_time_safeguard_target_points", DEFAULT_TARGET_POINTS), DEFAULT_TARGET_POINTS)),
        "stoploss_points": max(0.0, float_setting(settings.get("trailing_time_safeguard_stoploss_points", DEFAULT_STOPLOSS_POINTS), DEFAULT_STOPLOSS_POINTS)),
        "trailing_start_points": trailing_settings(settings)["start_points"],
    }


def signal_start_index(signal=None, fallback=0):
    signal = signal or {}
    for key in ("signal_index", "nifty_signal_index", "entry_index"):
        value = signal.get(key)
        if value not in ("", None):
            try:
                return int(value)
            except (TypeError, ValueError):
                break
    return int(fallback or 0)


def initial_trailing_safeguard_state(signal, entry_price, settings=None):
    config = trailing_safeguard_settings(settings)
    return {
        "trailing_start_reached": False,
        "trailing_time_safeguard_enabled": config["enabled"],
        "trailing_time_safeguard_applied": False,
        "trailing_time_safeguard_candles": config["candle_limit"],
        "trailing_time_safeguard_target_points": config["target_points"],
        "trailing_time_safeguard_stoploss_points": config["stoploss_points"],
        "trailing_time_safeguard_signal_index": signal_start_index(signal),
        "trailing_time_safeguard_modifications": [],
    }


def trailing_start_reached(entry_price, observed_price, settings=None):
    config = trailing_safeguard_settings(settings)
    if not config["enabled"]:
        return False
    return float(observed_price) >= float(entry_price) + config["trailing_start_points"]


def should_apply_trailing_safeguard(state, current_index):
    if not state.get("trailing_time_safeguard_enabled"):
        return False
    if state.get("trailing_start_reached") or state.get("trailing_time_safeguard_applied"):
        return False
    signal_index = int(state.get("trailing_time_safeguard_signal_index", 0) or 0)
    candle_limit = int(state.get("trailing_time_safeguard_candles", DEFAULT_CANDLE_LIMIT) or DEFAULT_CANDLE_LIMIT)
    return int(current_index) - signal_index >= candle_limit


def safeguard_prices(entry_price, settings=None, round_price=None, minimum_price=None):
    config = trailing_safeguard_settings(settings)
    target = float(entry_price) + config["target_points"]
    stoploss = float(entry_price) - config["stoploss_points"]
    if minimum_price is not None:
        stoploss = max(stoploss, float(minimum_price))
    if round_price:
        target = round_price(target)
        stoploss = round_price(stoploss)
    return target, stoploss


def build_safeguard_event(timestamp, old_target, new_target, old_sl, new_sl, observed_price, current_index, status):
    return {
        "timestamp": timestamp,
        "old_target_price": old_target,
        "new_target_price": new_target,
        "old_sl_price": old_sl,
        "new_sl_price": new_sl,
        "ltp_at_safeguard": observed_price,
        "candle_index": current_index,
        "modify_status": status,
    }
