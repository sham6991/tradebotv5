import math


TRAILING_TARGET_ERROR = "Trailing Stop Loss requires target/profit points greater than 10."


def bool_setting(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def trailing_settings(settings=None):
    settings = settings or {}
    return {
        "enabled": bool_setting(settings.get("trailing_sl_enabled", False)),
        "start_points": max(10.0, float_setting(settings.get("trailing_start_points", 10), 10)),
        "step_points": max(1.0, float_setting(settings.get("trailing_step_points", 5), 5)),
        "lock_points": max(1.0, float_setting(settings.get("trailing_lock_points", 5), 5)),
    }


def calculate_trailing_stop(entry_price, current_sl_price, ltp, settings=None):
    config = trailing_settings(settings)
    if not config["enabled"]:
        return None
    entry = float(entry_price)
    current_sl = float(current_sl_price)
    profit = float(ltp) - entry
    if profit < config["start_points"]:
        return None
    completed_steps = math.floor((profit - config["start_points"]) / config["step_points"])
    locked_profit = config["lock_points"] + (completed_steps * config["step_points"])
    new_sl = entry + locked_profit
    if new_sl <= current_sl:
        return None
    return {
        "new_sl_price": new_sl,
        "profit": profit,
        "trailing_level": config["start_points"] + (completed_steps * config["step_points"]),
        "locked_profit": locked_profit,
        **config,
    }


def validate_trailing_stop_settings(values):
    values = values or {}
    errors = []
    if not bool_setting(values.get("trailing_sl_enabled", False)):
        return errors
    profit_points = float_setting(values.get("profit_points", values.get("target_points", 0)), 0)
    if profit_points <= 10:
        errors.append(TRAILING_TARGET_ERROR)
    if float_setting(values.get("trailing_start_points", 10), 10) < 10:
        errors.append("Trailing Start Points must be at least 10.")
    if float_setting(values.get("trailing_step_points", 5), 5) < 1:
        errors.append("Trailing Step Points must be at least 1.")
    if float_setting(values.get("trailing_lock_points", 5), 5) < 1:
        errors.append("Trailing Lock Points must be at least 1.")
    return errors


def float_setting(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
