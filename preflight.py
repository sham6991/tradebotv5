from datetime import datetime

from runtime_errors import classify_runtime_error
from settings_validation import validate_fast_ohlcv_settings


class PreflightReport:
    def __init__(self, mode, checks):
        self.mode = mode
        self.checks = checks

    @property
    def errors(self):
        return [check for check in self.checks if check["level"] == "ERROR"]

    @property
    def warnings(self):
        return [check for check in self.checks if check["level"] == "WARN"]

    @property
    def ok(self):
        return not self.errors

    def raise_for_errors(self):
        if self.ok:
            return
        details = "; ".join(f"{item['code']}: {item['message']}" for item in self.errors)
        raise ValueError(f"Live pre-flight failed: {details}")

    def to_dict(self):
        return {
            "mode": self.mode,
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": self.checks,
        }


def validate_live_preflight(nifty, options, token_map, settings, mode="PAPER", zerodha=None, now=None):
    mode = str(mode or "PAPER").upper()
    checks = []
    _validate_settings(settings or {}, checks, mode=mode, zerodha=zerodha)
    _validate_market_data(nifty, options, token_map, checks)
    _validate_broker(mode, zerodha, checks)
    _validate_market_hours(settings or {}, mode, checks, now or datetime.now())
    return PreflightReport(mode, checks)


def _add(checks, level, code, message, context=None):
    checks.append({
        "level": level,
        "code": code,
        "message": message,
        "context": context or {},
    })


def _validate_settings(settings, checks, mode="PAPER", zerodha=None):
    _positive_int(settings, "lot_size", checks)
    _positive_int(settings, "max_trades", checks)
    _positive_float(settings, "profit_points", checks)
    _positive_float(settings, "safety_points", checks)
    _range_float(settings, "stoploss_limit_buffer_points", checks, 0.05, 1000)
    _range_float(settings, "live_option_market_entry_limit_buffer_points", checks, 0.05, 1000)
    _range_float(settings, "trailing_start_points", checks, 10, 1000)
    _range_float(settings, "trailing_step_points", checks, 1, 1000)
    _range_float(settings, "trailing_lock_points", checks, 1, 1000)
    if str(mode or "PAPER").upper() == "LIVE":
        _validate_live_margin_balance(settings, zerodha, checks)
    else:
        _positive_float(settings, "balance", checks)
    _range_float(settings, "buy_limit_score_low", checks, 0, 60)
    _range_float(settings, "market_entry_score", checks, 0, 60)
    _range_float(settings, "aggressive_entry_score", checks, 0, 60)
    _range_float(settings, "trigger_upper_wick_max", checks, 0, 100)
    _range_float(settings, "hard_rejection_upper_wick_max", checks, 0, 100)
    _range_float(settings, "aggressive_upper_wick_max", checks, 0, 100)
    _range_float(settings, "minimum_body_percent", checks, 0, 100)
    _range_float(settings, "market_entry_minimum_body_percent", checks, 0, 100)
    _range_float(settings, "aggressive_minimum_body_percent", checks, 0, 100)
    _range_float(settings, "minimum_close_position", checks, 0, 100)
    _range_float(settings, "market_entry_minimum_close_position", checks, 0, 100)
    _range_float(settings, "aggressive_minimum_close_position", checks, 0, 100)
    _range_float(settings, "volume_previous_multiplier", checks, 0, 10)
    _range_float(settings, "avg_volume_minimum_multiplier", checks, 0, 10)
    _range_float(settings, "volume_pickup_avg_multiplier", checks, 0, 10)
    _range_float(settings, "large_candle_multiplier", checks, 0, 20)
    _range_float(settings, "move_from_low_max_multiplier", checks, 0, 20)
    _range_float(settings, "aggressive_move_from_low_max_multiplier", checks, 0, 20)
    _range_float(settings, "gap_spike_multiplier", checks, 0, 20)
    _range_float(settings, "buy_limit_offset_multiplier", checks, 0, 10)
    _range_float(settings, "minimum_offset", checks, 0, 100)
    _range_float(settings, "maximum_offset", checks, 0, 100)
    _range_float(settings, "buy_limit_validity_seconds", checks, 1, 3600)
    _range_float(settings, "chop_lookback_candles", checks, 1, 20)
    _range_float(settings, "chop_overlap_count", checks, 1, 20)
    _range_float(settings, "missed_limit_cooldown_candles", checks, 0, 20)
    _range_float(settings, "max_spread_points", checks, 0, 100)
    fill_mode = str(settings.get("backtest_limit_fill_mode", "CONSERVATIVE")).upper()
    if fill_mode not in {"CONSERVATIVE", "SIMPLE", "STRICT"}:
        _add(checks, "ERROR", "INVALID_BACKTEST_LIMIT_FILL_MODE", "Backtest limit fill mode must be CONSERVATIVE, SIMPLE, or STRICT.", {"value": fill_mode})
    for index, message in enumerate(validate_fast_ohlcv_settings(settings), start=1):
        _add(checks, "ERROR", f"INVALID_FAST_OHLCV_RELATIONSHIP_{index}", message)
    _float_setting(settings, "bullish_threshold", checks)
    _float_setting(settings, "bearish_threshold", checks)
    _float_setting(settings, "rsi_bull", checks)
    _float_setting(settings, "rsi_bear", checks)
    _float_setting(settings, "rsi_reversal_bullish", checks, default=70)
    _float_setting(settings, "rsi_reversal_bearish", checks, default=20)
    _float_setting(settings, "bullish_reversal_condition", checks, default=-20)
    _float_setting(settings, "bearish_reversal_condition", checks, default=10)

    interval = str(settings.get("chart_interval", "")).strip().lower()
    if not interval:
        _add(checks, "ERROR", "MISSING_CHART_INTERVAL", "Chart interval is required.")
    elif not any(value in interval for value in ("minute", "min", "1", "2", "3", "5")):
        _add(checks, "ERROR", "INVALID_CHART_INTERVAL", "Chart interval must be a supported minute interval.", {"value": interval})

    square_off = str(settings.get("square_off_time", "")).strip()
    if square_off:
        try:
            datetime.strptime(square_off, "%H:%M")
        except ValueError:
            _add(checks, "ERROR", "INVALID_SQUARE_OFF_TIME", "Square off time must use HH:MM format.", {"value": square_off})


def _validate_market_data(nifty, options, token_map, checks):
    if nifty is None or getattr(nifty, "empty", True):
        _add(checks, "ERROR", "MISSING_NIFTY_DATA", "NIFTY historical candles are missing.")
    else:
        _required_columns(nifty, "NIFTY", checks)

    if not options:
        _add(checks, "ERROR", "MISSING_OPTION_DATA", "Option historical candles are missing.")
    else:
        for index, option in enumerate(options):
            if option is None or getattr(option, "empty", True):
                _add(checks, "ERROR", "EMPTY_OPTION_DATA", "Option historical candles are empty.", {"option_index": index})
            else:
                _required_columns(option, f"OPTION_{index}", checks)

    if not token_map:
        _add(checks, "ERROR", "MISSING_TOKEN_MAP", "Live token map is empty.")
    else:
        names = {str(value) for value in token_map.values()}
        if "NIFTY" not in names:
            _add(checks, "ERROR", "TOKEN_MAP_MISSING_NIFTY", "Token map must contain NIFTY.")
        option_names = [name for name in names if name.startswith("OPTION_")]
        if not option_names:
            _add(checks, "ERROR", "TOKEN_MAP_MISSING_OPTIONS", "Token map must contain option tokens.")


def _validate_broker(mode, zerodha, checks):
    if mode == "LIVE" and not zerodha:
        _add(checks, "ERROR", "ZERODHA_NOT_CONNECTED", "Connect Zerodha before real trading.")


def _validate_live_margin_balance(settings, zerodha, checks):
    if not zerodha:
        return
    try:
        margin = zerodha.available_margin()
    except Exception as exc:
        classification = classify_runtime_error(exc, context="margin")
        _add(
            checks,
            "ERROR",
            "LIVE_MARGIN_FETCH_FAILED",
            "Could not fetch Zerodha margin for real trading preflight.",
            {
                "error": str(exc),
                "error_class": classification["class"],
                "error_category": classification["category"],
            },
        )
        return
    if margin is None:
        _add(checks, "ERROR", "LIVE_MARGIN_UNAVAILABLE", "Zerodha margin is unavailable for real trading preflight.")
        return
    try:
        margin_value = float(margin)
    except (TypeError, ValueError):
        _add(checks, "ERROR", "INVALID_LIVE_MARGIN", "Zerodha margin must be numeric.", {"value": margin})
        return
    if margin_value <= 0:
        _add(checks, "ERROR", "INVALID_LIVE_MARGIN", "Zerodha margin must be greater than zero.", {"value": margin_value})
        return
    settings["balance"] = margin_value


def _validate_market_hours(settings, mode, checks, now):
    if mode != "LIVE":
        return
    if str(settings.get("warn_market_hours", "1")).lower() in ("0", "false", "no"):
        return
    start = datetime.strptime("09:15", "%H:%M").time()
    end = datetime.strptime("15:30", "%H:%M").time()
    if now.time() < start or now.time() > end:
        _add(checks, "WARN", "OUTSIDE_MARKET_HOURS", "Current time is outside regular market hours.", {"time": now.strftime("%H:%M")})


def _required_columns(df, label, checks):
    required = {"datetime", "open", "high", "low", "close"}
    missing = sorted(required - set(df.columns))
    if missing:
        _add(checks, "ERROR", "MISSING_CANDLE_COLUMNS", "Historical candles are missing required columns.", {"label": label, "missing": missing})


def _positive_int(settings, key, checks):
    try:
        value = int(settings.get(key))
    except (TypeError, ValueError):
        _add(checks, "ERROR", f"INVALID_{key.upper()}", f"{key} must be a positive integer.")
        return
    if value <= 0:
        _add(checks, "ERROR", f"INVALID_{key.upper()}", f"{key} must be greater than zero.", {"value": value})


def _positive_float(settings, key, checks):
    try:
        value = float(settings.get(key))
    except (TypeError, ValueError):
        _add(checks, "ERROR", f"INVALID_{key.upper()}", f"{key} must be a positive number.")
        return
    if value <= 0:
        _add(checks, "ERROR", f"INVALID_{key.upper()}", f"{key} must be greater than zero.", {"value": value})


def _range_float(settings, key, checks, minimum, maximum):
    if key not in settings:
        return
    value = _float_setting(settings, key, checks)
    if value is None:
        return
    if value < minimum or value > maximum:
        _add(checks, "ERROR", f"INVALID_{key.upper()}", f"{key} must be between {minimum} and {maximum}.", {"value": value})


def _float_setting(settings, key, checks, default=None):
    try:
        return float(settings.get(key, default))
    except (TypeError, ValueError):
        _add(checks, "ERROR", f"INVALID_{key.upper()}", f"{key} must be numeric.")
        return None
