from __future__ import annotations

from copy import deepcopy
from typing import Any

from options_auto.constants import MODE_PAPER


DEFAULT_OPTIONS_AUTO_SETTINGS: dict[str, Any] = {
    "mode": MODE_PAPER,
    "underlying": "NIFTY",
    "enabled_underlyings": ["NIFTY", "SENSEX"],
    "chart_interval": "3minute",
    "strategy_profile": "BALANCED",
    "paper_starting_balance": 20000.0,
    "buy_score_threshold": 70.0,
    "approval_timeout_seconds": 30,
    "ask_permission_before_entry": True,
    "auto_entry_enabled": False,
    "real_orders_enabled": False,
    "confirm_real_mode": False,
    "static_ip_confirmed": False,
    "allow_real_emergency_orders": False,
    "max_capital_per_trade_pct": 20.0,
    "max_lots_per_trade": 2,
    "max_trades_per_day": 3,
    "max_open_trades": 1,
    "max_daily_loss": 1000.0,
    "max_daily_profit_lock": 2500.0,
    "max_consecutive_losses": 2,
    "cooldown_after_trade_seconds": 300,
    "cooldown_after_loss_seconds": 600,
    "cooldown_after_rejection_seconds": 180,
    "cooldown_after_api_error_seconds": 300,
    "avoid_first_minutes": 15,
    "no_new_trade_after": "15:00",
    "square_off_time": "15:15",
    "buy_order_type": "LIMIT",
    "target_order_type": "LIMIT",
    "stoploss_order_type": "SL",
    "limit_order_timeout_seconds": 30,
    "modify_limit_allowed": True,
    "max_buy_limit_modifications": 2,
    "max_chase_points": 3.0,
    "slippage_buffer_points": 0.10,
    "max_allowed_slippage_points": 5.0,
    "max_spread_pct": 0.60,
    "min_depth_qty": 1,
    "min_volume": 0,
    "min_oi": 0,
    "quote_stale_seconds": 3.0,
    "market_cue_alignment_required": True,
    "news_sentiment_weight": 3.0,
    "trend_strength_threshold": 55.0,
    "atr_target_multiplier": 1.5,
    "atr_stoploss_multiplier": 1.0,
    "trailing_stop_enabled": True,
    "partial_exit_enabled": False,
    "break_even_sl_enabled": True,
    "time_exit_enabled": True,
    "reversal_exit_enabled": True,
    "volatility_exit_enabled": True,
    "max_holding_minutes": 45,
    "sl_modify_throttle_seconds": 10,
    "theta_exit_risk_score": 80.0,
    "iv_crush_exit_pct": 25.0,
    "max_latency_warning_ms": 1500.0,
    "telegram_command_cooldown_seconds": 2,
    "telegram_duplicate_window_seconds": 10,
    "require_telegram_position_preview": True,
    "expiry_preference": "AUTO",
    "allow_deep_otm": False,
    "expiry_day_max_lots": 1,
    "dry_run_real_only": True,
}


def default_settings() -> dict[str, Any]:
    return deepcopy(DEFAULT_OPTIONS_AUTO_SETTINGS)


def _bool(value: Any, default: bool) -> bool:
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def normalize_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    settings = default_settings()
    payload = dict(payload or {})
    settings.update({key: value for key, value in payload.items() if value not in (None, "")})

    for key in (
        "ask_permission_before_entry",
        "auto_entry_enabled",
        "real_orders_enabled",
        "confirm_real_mode",
        "static_ip_confirmed",
        "allow_real_emergency_orders",
        "modify_limit_allowed",
        "market_cue_alignment_required",
        "trailing_stop_enabled",
        "partial_exit_enabled",
        "break_even_sl_enabled",
        "time_exit_enabled",
        "reversal_exit_enabled",
        "volatility_exit_enabled",
        "allow_deep_otm",
        "dry_run_real_only",
        "require_telegram_position_preview",
    ):
        settings[key] = _bool(settings.get(key), bool(DEFAULT_OPTIONS_AUTO_SETTINGS[key]))

    for key in (
        "paper_starting_balance",
        "buy_score_threshold",
        "max_capital_per_trade_pct",
        "max_daily_loss",
        "max_daily_profit_lock",
        "slippage_buffer_points",
        "max_allowed_slippage_points",
        "max_chase_points",
        "max_spread_pct",
        "quote_stale_seconds",
        "news_sentiment_weight",
        "trend_strength_threshold",
        "atr_target_multiplier",
        "atr_stoploss_multiplier",
        "theta_exit_risk_score",
        "iv_crush_exit_pct",
        "max_latency_warning_ms",
    ):
        settings[key] = _float(settings.get(key), float(DEFAULT_OPTIONS_AUTO_SETTINGS[key]))

    for key in (
        "approval_timeout_seconds",
        "max_lots_per_trade",
        "max_trades_per_day",
        "max_open_trades",
        "max_consecutive_losses",
        "cooldown_after_trade_seconds",
        "cooldown_after_loss_seconds",
        "cooldown_after_rejection_seconds",
        "cooldown_after_api_error_seconds",
        "avoid_first_minutes",
        "limit_order_timeout_seconds",
        "max_buy_limit_modifications",
        "min_depth_qty",
        "min_volume",
        "min_oi",
        "max_holding_minutes",
        "sl_modify_throttle_seconds",
        "telegram_command_cooldown_seconds",
        "telegram_duplicate_window_seconds",
        "expiry_day_max_lots",
    ):
        settings[key] = _int(settings.get(key), int(DEFAULT_OPTIONS_AUTO_SETTINGS[key]))

    settings["mode"] = str(settings.get("mode") or MODE_PAPER).strip().upper()
    settings["underlying"] = str(settings.get("underlying") or "NIFTY").strip().upper()
    settings["strategy_profile"] = str(settings.get("strategy_profile") or "BALANCED").strip().upper()
    settings["expiry_preference"] = str(settings.get("expiry_preference") or "AUTO").strip().upper()
    return settings
