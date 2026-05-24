import json
import os

from settings_validation import raise_for_fast_ohlcv_settings


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PROFILE_PATH = os.path.join(BASE_DIR, "data", "settings_profiles.json")
RUNTIME_ACCOUNT_KEYS = {"zerodha_margin_fetched", "broker_user_id", "available_margin", "used_margin", "valid_until"}

DEFAULT_SETTINGS = {
    "balance": "100000",
    "lot_size": "1",
    "max_trades": "5",
    "profit_points": "20",
    "safety_points": "10",
    "stoploss_limit_buffer_points": "2",
    "live_option_market_entry_as_limit_enabled": "false",
    "live_option_market_entry_limit_buffer_points": "2",
    "trailing_sl_enabled": "false",
    "trailing_start_points": "10",
    "trailing_step_points": "5",
    "trailing_lock_points": "5",
    "time_exit": "10",
    "cooldown": "5",
    "chart_interval": "3 min",
    "trend_set": "Auto",
    "bullish_threshold": "16",
    "bearish_threshold": "-15",
    "rsi_bull": "55",
    "rsi_bear": "45",
    "rsi_reversal_bullish": "70",
    "rsi_reversal_bearish": "20",
    "bullish_reversal_condition": "-20",
    "bearish_reversal_condition": "10",
    "fast_ohlcv_entry_enabled": "true",
    "buy_limit_score_low": "40",
    "market_entry_score": "50",
    "aggressive_entry_score": "50",
    "trigger_upper_wick_max": "45",
    "hard_rejection_upper_wick_max": "50",
    "aggressive_upper_wick_max": "35",
    "minimum_body_percent": "20",
    "market_entry_minimum_body_percent": "25",
    "aggressive_minimum_body_percent": "25",
    "minimum_close_position": "55",
    "market_entry_minimum_close_position": "60",
    "aggressive_minimum_close_position": "65",
    "volume_previous_multiplier": "0.80",
    "avg_volume_minimum_multiplier": "0.50",
    "volume_pickup_avg_multiplier": "0.70",
    "large_candle_multiplier": "2.2",
    "move_from_low_max_multiplier": "1.10",
    "aggressive_move_from_low_max_multiplier": "0.90",
    "gap_spike_multiplier": "1.2",
    "buy_limit_offset_multiplier": "0.15",
    "minimum_offset": "1",
    "maximum_offset": "2",
    "buy_limit_validity_seconds": "30",
    "backtest_limit_fill_mode": "CONSERVATIVE",
    "enable_chop_filter": "false",
    "chop_lookback_candles": "3",
    "chop_overlap_count": "2",
    "aggressive_live_entry_enabled": "false",
    "aggressive_setup_score": "40",
    "one_entry_attempt_per_candle": "true",
    "missed_limit_cooldown_candles": "0",
    "max_spread_points": "2.0",
    "max_daily_loss": "0",
    "max_daily_profit": "0",
    "max_consecutive_losses": "0",
    "square_off_time": "15:20",
    "order_product": "NRML",
}

SETTING_LABELS = {
    "balance": "Balance",
    "lot_size": "Lots",
    "max_trades": "Max Trades",
    "profit_points": "Profit Points",
    "safety_points": "Safety Points",
    "stoploss_limit_buffer_points": "Stoploss Limit Buffer Points",
    "live_option_market_entry_as_limit_enabled": "Live Option Market Entry As Limit",
    "live_option_market_entry_limit_buffer_points": "Live Option Entry Limit Buffer Points",
    "trailing_sl_enabled": "Enable Trailing Stop Loss",
    "trailing_start_points": "Trailing Start Points",
    "trailing_step_points": "Trailing Step Points",
    "trailing_lock_points": "Trailing Lock Points",
    "time_exit": "Time Exit",
    "cooldown": "Cooldown",
    "chart_interval": "Chart Interval",
    "trend_set": "Trend Set",
    "bullish_threshold": "Bullish Threshold",
    "bearish_threshold": "Bearish Threshold",
    "rsi_bull": "RSI Bull",
    "rsi_bear": "RSI Bear",
    "rsi_reversal_bullish": "RSI Reversal Bullish",
    "rsi_reversal_bearish": "RSI Reversal Bearish",
    "bullish_reversal_condition": "Bullish Reversal Condition",
    "bearish_reversal_condition": "Bearish Reversal Condition",
    "fast_ohlcv_entry_enabled": "Fast OHLCV Entry",
    "buy_limit_score_low": "Buy Limit Score Low",
    "market_entry_score": "Market Entry Score",
    "aggressive_entry_score": "Aggressive Entry Score",
    "trigger_upper_wick_max": "Trigger Upper Wick Max",
    "hard_rejection_upper_wick_max": "Hard Rejection Wick Max",
    "aggressive_upper_wick_max": "Aggressive Wick Max",
    "minimum_body_percent": "Minimum Body %",
    "market_entry_minimum_body_percent": "Market Body %",
    "aggressive_minimum_body_percent": "Aggressive Body %",
    "minimum_close_position": "Minimum Close Position",
    "market_entry_minimum_close_position": "Market Close Position",
    "aggressive_minimum_close_position": "Aggressive Close Position",
    "volume_previous_multiplier": "Previous Volume Multiplier",
    "avg_volume_minimum_multiplier": "Avg Volume Minimum",
    "volume_pickup_avg_multiplier": "Volume Pickup Avg",
    "large_candle_multiplier": "Large Candle Multiplier",
    "move_from_low_max_multiplier": "Move From Low Max",
    "aggressive_move_from_low_max_multiplier": "Aggressive Move From Low",
    "gap_spike_multiplier": "Gap Spike Multiplier",
    "buy_limit_offset_multiplier": "Limit Offset Multiplier",
    "minimum_offset": "Minimum Offset",
    "maximum_offset": "Maximum Offset",
    "buy_limit_validity_seconds": "Limit Validity Seconds",
    "backtest_limit_fill_mode": "Backtest Limit Fill Mode",
    "enable_chop_filter": "Enable Chop Filter",
    "chop_lookback_candles": "Chop Lookback Candles",
    "chop_overlap_count": "Chop Overlap Count",
    "aggressive_live_entry_enabled": "Aggressive Live Entry",
    "aggressive_setup_score": "Aggressive Setup Score",
    "one_entry_attempt_per_candle": "One Attempt Per Candle",
    "missed_limit_cooldown_candles": "Missed Limit Cooldown",
    "max_spread_points": "Max Spread Points",
    "max_daily_loss": "Max Daily Loss",
    "max_daily_profit": "Max Daily Profit",
    "max_consecutive_losses": "Max Loss Streak",
    "square_off_time": "Square Off Time",
    "order_product": "Order Product",
}


def normalise_interval(value):
    text = str(value or "").strip().lower()
    return {
        "1 min": "minute",
        "1minute": "minute",
        "minute": "minute",
        "2 min": "2minute",
        "2minute": "2minute",
        "3 min": "3minute",
        "3minute": "3minute",
        "5 min": "5minute",
        "5minute": "5minute",
    }.get(text, "3minute")


def interval_label(value):
    normalised = normalise_interval(value)
    return {
        "minute": "1 min",
        "2minute": "2 min",
        "3minute": "3 min",
        "5minute": "5 min",
    }.get(normalised, "3 min")


def normalise_order_product(value):
    text = str(value or "NRML").strip().upper()
    return "MIS" if text in ("MIS", "INTRADAY") else "NRML"


def normalise_trend_set(value):
    text = str(value or "Auto").strip().upper()
    if text in {"BULLISH", "BULL", "CE"}:
        return "Bullish"
    if text in {"BEARISH", "BEAR", "PE"}:
        return "Bearish"
    return "Auto"


def setting_value(values, key):
    value = (values or {}).get(key, DEFAULT_SETTINGS.get(key, ""))
    if value is None or str(value).strip() == "":
        return DEFAULT_SETTINGS.get(key, "")
    return value


def sanitize_settings_profile(values, profile=""):
    values = values or {}
    cleaned = {key: value for key, value in values.items() if key not in RUNTIME_ACCOUNT_KEYS}
    if str(profile or "").lower() == "real":
        cleaned.pop("balance", None)
    return cleaned


def normalized_settings_profile(values, profile=""):
    values = values or {}
    normalized = {key: setting_value(values, key) for key in DEFAULT_SETTINGS}
    if str(profile or "").lower() == "real" and "balance" not in values:
        normalized["balance"] = "0"
    return normalized


def persisted_settings_profile(profile, values):
    normalized = normalized_settings_profile(sanitize_settings_profile(values, profile), profile)
    if str(profile or "").lower() == "real":
        normalized.pop("balance", None)
    return normalized


def persisted_settings_profiles(profiles):
    profiles = profiles or {}
    return {
        "backtest": persisted_settings_profile("backtest", profiles.get("backtest", {})),
        "paper": persisted_settings_profile("paper", profiles.get("paper", {})),
        "real": persisted_settings_profile("real", profiles.get("real", {})),
    }


def parse_runtime_setting_value(key, value):
    if key == "chart_interval":
        return normalise_interval(value)
    if key == "trend_set":
        return normalise_trend_set(value)
    if key == "order_product":
        return normalise_order_product(value)
    if key in {
        "fast_ohlcv_entry_enabled",
        "enable_chop_filter",
        "aggressive_live_entry_enabled",
        "one_entry_attempt_per_candle",
        "trailing_sl_enabled",
        "live_option_market_entry_as_limit_enabled",
    }:
        return str(value).strip().lower() in ("1", "true", "yes", "on", "enabled")
    if key in {
        "lot_size",
        "max_trades",
        "time_exit",
        "cooldown",
        "buy_limit_validity_seconds",
        "chop_lookback_candles",
        "chop_overlap_count",
        "missed_limit_cooldown_candles",
        "max_consecutive_losses",
    }:
        return int(float(value))
    if key in {"square_off_time", "backtest_limit_fill_mode"}:
        return str(value).strip()
    return float(value)


def settings_from_values(values):
    values = {key: setting_value(values, key) for key in DEFAULT_SETTINGS}
    parsed = {key: parse_runtime_setting_value(key, value) for key, value in values.items()}
    raise_for_fast_ohlcv_settings(parsed)
    return parsed


def load_settings_profiles(profile_path=None):
    path = profile_path or SETTINGS_PROFILE_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    return {
        "backtest": normalized_settings_profile(data.get("backtest", {}), "backtest"),
        "paper": normalized_settings_profile(data.get("paper", {}), "paper"),
        "real": normalized_settings_profile(sanitize_settings_profile(data.get("real", {}), "real"), "real"),
    }


def save_settings_profile(profile, values, profile_path=None):
    if profile not in {"backtest", "paper", "real"}:
        raise ValueError("Unknown settings profile.")
    path = profile_path or SETTINGS_PROFILE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    profiles = load_settings_profiles(path)
    normalized = normalized_settings_profile(sanitize_settings_profile(values, profile), profile)
    raise_for_fast_ohlcv_settings(normalized)
    profiles[profile] = normalized
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(persisted_settings_profiles(profiles), handle, indent=2)
    return normalized


def save_settings_profiles(profiles, profile_path=None):
    path = profile_path or SETTINGS_PROFILE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    normalized = {
        "backtest": normalized_settings_profile((profiles or {}).get("backtest", {}), "backtest"),
        "paper": normalized_settings_profile((profiles or {}).get("paper", {}), "paper"),
        "real": normalized_settings_profile(sanitize_settings_profile((profiles or {}).get("real", {}), "real"), "real"),
    }
    for profile_values in normalized.values():
        raise_for_fast_ohlcv_settings(profile_values)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(persisted_settings_profiles(normalized), handle, indent=2)
    return load_settings_profiles(path)


def apply_backtest_settings_to_live(values=None, profile_path=None):
    path = profile_path or SETTINGS_PROFILE_PATH
    profiles = load_settings_profiles(path)
    source = normalized_settings_profile(values or profiles["backtest"], "backtest")
    raise_for_fast_ohlcv_settings(source)
    paper_existing = profiles.get("paper", {})
    paper_preserved = {
        key: paper_existing[key]
        for key in ("balance", "chart_interval")
        if key in paper_existing
    }
    real_existing = sanitize_settings_profile(profiles.get("real", {}), "real")
    real_preserved = {
        key: real_existing[key]
        for key in ("chart_interval",)
        if key in real_existing
    }

    profiles["backtest"] = source
    profiles["paper"] = normalized_settings_profile({**source, **paper_preserved}, "paper")
    profiles["real"] = normalized_settings_profile({**source, **real_preserved}, "real")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(persisted_settings_profiles(profiles), handle, indent=2)
    return load_settings_profiles(path)


def runtime_dir(profile_path=None):
    path = profile_path or SETTINGS_PROFILE_PATH
    return os.path.join(os.path.dirname(path), "runtime")


def real_account_snapshot_path(profile_path=None):
    return os.path.join(runtime_dir(profile_path), "real_account_snapshot.json")


def load_real_account_snapshot(profile_path=None):
    try:
        with open(real_account_snapshot_path(profile_path), "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return snapshot if isinstance(snapshot, dict) else {}


def save_real_account_snapshot(snapshot, profile_path=None, json_default=None):
    os.makedirs(runtime_dir(profile_path), exist_ok=True)
    with open(real_account_snapshot_path(profile_path), "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, default=json_default)
    return snapshot
