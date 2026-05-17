from datetime import datetime


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
    _validate_settings(settings or {}, checks)
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


def _validate_settings(settings, checks):
    _positive_int(settings, "lot_size", checks)
    _positive_int(settings, "max_trades", checks)
    _positive_float(settings, "profit_points", checks)
    _positive_float(settings, "safety_points", checks)
    _positive_float(settings, "balance", checks)
    _range_float(settings, "min_buy_score", checks, 0, 200)
    _float_setting(settings, "bullish_threshold", checks)
    _float_setting(settings, "bearish_threshold", checks)
    _float_setting(settings, "rsi_bull", checks)
    _float_setting(settings, "rsi_bear", checks)
    _float_setting(settings, "rsi_reversal_bullish", checks, default=70)
    _float_setting(settings, "rsi_reversal_bearish", checks, default=20)

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
