from __future__ import annotations

from typing import Any


SUPPORTED_INTRADAY_STRATEGY_PROFILES = {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}


PROFILE_POLICIES: dict[str, dict[str, Any]] = {
    "CONSERVATIVE": {
        "minimum_entry_score": 80.0,
        "relative_volume_threshold": 1.7,
        "rsi_bullish_threshold": 58.0,
        "rsi_bearish_threshold": 42.0,
        "allow_forming_candle_entry": False,
        "forming_candle_min_completion_pct": 100.0,
        "higher_timeframe_confirmation": True,
        "min_liquidity_score": 50.0,
        "max_allowed_spread_pct": 0.25,
        "cooldown_multiplier": 1.5,
        "max_trades_multiplier": 0.6,
        "trail_activation_r": 1.0,
        "breakeven_trigger_r": 0.8,
    },
    "BALANCED": {
        "minimum_entry_score": 70.0,
        "relative_volume_threshold": 1.5,
        "rsi_bullish_threshold": 55.0,
        "rsi_bearish_threshold": 45.0,
        "allow_forming_candle_entry": False,
        "forming_candle_min_completion_pct": 100.0,
        "higher_timeframe_confirmation": False,
        "min_liquidity_score": 35.0,
        "max_allowed_spread_pct": 0.35,
        "cooldown_multiplier": 1.0,
        "max_trades_multiplier": 1.0,
        "trail_activation_r": 1.2,
        "breakeven_trigger_r": 1.0,
    },
    "AGGRESSIVE": {
        "minimum_entry_score": 62.0,
        "relative_volume_threshold": 1.2,
        "rsi_bullish_threshold": 52.0,
        "rsi_bearish_threshold": 48.0,
        "allow_forming_candle_entry": True,
        "forming_candle_min_completion_pct": 60.0,
        "higher_timeframe_confirmation": False,
        "min_liquidity_score": 25.0,
        "max_allowed_spread_pct": 0.45,
        "cooldown_multiplier": 0.6,
        "max_trades_multiplier": 1.4,
        "trail_activation_r": 0.6,
        "breakeven_trigger_r": 0.5,
    },
}


HARD_SAFETY_OVERRIDES = {
    "no_stale_live_data": True,
    "no_simulated_data_in_paper_or_real_without_demo_mode": True,
    "no_missing_candles": True,
    "no_real_order_without_preflight": True,
    "no_order_without_fill_confirmation": True,
    "no_duplicate_order": True,
    "max_daily_loss_always_active": True,
    "kill_switch_always_active": True,
}


def normalize_intraday_strategy_profile(value: Any) -> str:
    profile = str(value or "BALANCED").strip().upper()
    return profile if profile in SUPPORTED_INTRADAY_STRATEGY_PROFILES else "BALANCED"


def resolve_intraday_strategy_profile(settings: Any) -> dict[str, Any]:
    profile = normalize_intraday_strategy_profile(getattr(settings, "strategy_profile", "BALANCED"))
    policy = dict(PROFILE_POLICIES[profile])
    policy["strategy_profile"] = profile
    policy["hard_safety_overrides"] = dict(HARD_SAFETY_OVERRIDES)
    return policy


def apply_intraday_strategy_profile(settings: Any, explicit_thresholds: dict[str, Any] | None = None) -> Any:
    policy = resolve_intraday_strategy_profile(settings)
    explicit_thresholds = explicit_thresholds or {}
    settings.strategy_profile = policy["strategy_profile"]
    for key in (
        "minimum_entry_score",
        "relative_volume_threshold",
        "rsi_bullish_threshold",
        "rsi_bearish_threshold",
        "higher_timeframe_confirmation",
        "min_liquidity_score",
        "max_allowed_spread_pct",
        "trail_activation_r",
        "breakeven_trigger_r",
    ):
        setattr(settings, key, policy[key])
    settings.cooldown_after_trade_seconds = max(0, int(round(settings.cooldown_after_trade_seconds * float(policy["cooldown_multiplier"]))))
    settings.cooldown_after_loss_seconds = max(0, int(round(settings.cooldown_after_loss_seconds * float(policy["cooldown_multiplier"]))))
    settings.max_trades_per_day = max(1, int(round(settings.max_trades_per_day * float(policy["max_trades_multiplier"]))))
    setattr(settings, "allow_forming_candle_entry", bool(policy["allow_forming_candle_entry"]))
    setattr(settings, "forming_candle_min_completion_pct", float(policy["forming_candle_min_completion_pct"]))
    if "max_allowed_spread_pct" in explicit_thresholds:
        explicit_spread = float(explicit_thresholds["max_allowed_spread_pct"])
        settings.max_allowed_spread_pct = min(settings.max_allowed_spread_pct, explicit_spread)
        policy["effective_max_allowed_spread_pct"] = settings.max_allowed_spread_pct
    if "min_liquidity_score" in explicit_thresholds:
        explicit_liquidity = float(explicit_thresholds["min_liquidity_score"])
        settings.min_liquidity_score = max(settings.min_liquidity_score, explicit_liquidity)
        policy["effective_min_liquidity_score"] = settings.min_liquidity_score
    setattr(settings, "profile_policy", policy)
    return settings
