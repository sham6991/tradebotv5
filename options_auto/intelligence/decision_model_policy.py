from __future__ import annotations

from typing import Any


ENTRY_FAST = "FAST_OHLCV_VOLUME"
ENTRY_FULL = "FULL_CONFIRMATION"


def canonical_entry_logic(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = dict(settings or {})
    raw = str(settings.get("entry_logic") or settings.get("entry_dependency_mode") or ENTRY_FULL).strip().upper()
    deprecated = False
    if raw in {"SIMPLE", "SIMPLE_OHLCV", "MAIN_APP_STYLE", "OHLCV_VOLUME", "OHLCV_VOLUME_PROFILE", ENTRY_FAST}:
        entry_logic = ENTRY_FAST
        score_engine = "SimpleOHLCVVolumeScore"
        label = "Fast OHLCV + Volume"
    elif raw in {"FULL", "FULL_CONFIRMATION", "CONFIRMATION_STACK", ENTRY_FULL}:
        entry_logic = ENTRY_FULL
        score_engine = "TradeScoreEngine"
        label = "Full Confirmation"
    elif raw == "PROFILE":
        entry_logic = ENTRY_FULL
        score_engine = "TradeScoreEngine"
        label = "Full Confirmation"
        deprecated = True
    else:
        entry_logic = ENTRY_FULL
        score_engine = "TradeScoreEngine"
        label = "Full Confirmation"
    return {
        "entry_logic": entry_logic,
        "entry_logic_label": label,
        "score_engine": score_engine,
        "deprecated_alias": deprecated,
        "raw_entry_dependency_mode": raw,
    }


def market_context_policy(settings: dict[str, Any] | None = None) -> str:
    settings = dict(settings or {})
    if not _bool(settings.get("market_context_enabled"), True):
        return "OFF"
    if _bool(settings.get("market_context_enforcement_enabled"), False) or _bool(settings.get("market_cue_alignment_required"), False):
        return "ENFORCED"
    return "REPORT_ONLY"


def profile_policy(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = dict(settings or {})
    profile = str(settings.get("strategy_profile") or "BALANCED").strip().upper()
    if profile not in {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}:
        profile = "BALANCED"
    base_spread = _float(settings.get("max_spread_pct"), 0.6)
    if profile == "CONSERVATIVE":
        return {
            "profile": profile,
            "threshold_adjustment": 8,
            "max_spread_pct_effective": round(min(base_spread, 0.4), 4),
            "depth_requirement": "FULL_DEPTH_OR_STRONG_TOP_OF_BOOK",
            "size_multiplier": 0.75,
            "cooldown_multiplier": 1.35,
            "rr_profile": "HIGHER_CONFIRMATION",
            "scan_interval_seconds": _float(settings.get("adaptive_scan_seconds_conservative"), 3),
            "reason": "Conservative profile raises confirmation threshold and tightens depth/spread requirements.",
        }
    if profile == "AGGRESSIVE":
        return {
            "profile": profile,
            "threshold_adjustment": -5,
            "max_spread_pct_effective": base_spread,
            "depth_requirement": "TOP_OF_BOOK_OR_DEGRADED_SCANNER_ONLY",
            "size_multiplier": 1.0,
            "cooldown_multiplier": 0.75,
            "rr_profile": "FAST_SCALP",
            "scan_interval_seconds": _float(settings.get("adaptive_scan_seconds_aggressive"), 1),
            "reason": "Aggressive profile can scan faster but cannot bypass quote freshness or real final validation.",
        }
    return {
        "profile": profile,
        "threshold_adjustment": 0,
        "max_spread_pct_effective": base_spread,
        "depth_requirement": "TOP_OF_BOOK_ALLOWED",
        "size_multiplier": 1.0,
        "cooldown_multiplier": 1.0,
        "rr_profile": "BALANCED",
        "scan_interval_seconds": _float(settings.get("adaptive_scan_seconds_balanced"), 2),
        "reason": "Balanced profile keeps default threshold and standard depth/spread requirements.",
    }


def entry_logic_policy(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = canonical_entry_logic(settings)
    if entry["entry_logic"] == ENTRY_FAST:
        return {
            **entry,
            "components_used": ["index_ohlcv_direction", "option_premium_ohlcv", "volume_profile", "quote_quality"],
            "blockers_relaxed": ["theta_expected_edge_warning_in_paper_scanner", "some_timing_warnings"],
            "blockers_never_relaxed": ["stale_quote", "missing_ltp", "invalid_bid_ask", "wide_spread", "real_final_validation", "kill_switch", "stop_new_entries"],
            "threshold": _float((settings or {}).get("simple_ohlcv_score_threshold"), 50.0),
            "reason": "Fast logic uses fewer confirmations while keeping quote, spread, and real-safety blockers hard.",
        }
    return {
        **entry,
        "components_used": ["regime", "market_cue", "trend", "premium_momentum", "vwap_ema", "volume", "liquidity_oi", "spread_depth", "theta", "news", "time_of_day"],
        "blockers_relaxed": [],
        "blockers_never_relaxed": ["all_data_quality", "score_threshold", "risk_governor", "real_final_validation", "kill_switch", "stop_new_entries"],
        "threshold": _float((settings or {}).get("buy_score_threshold"), 70.0),
        "reason": "Full confirmation requires the broader confirmation stack and risk/governor checks.",
    }


def decision_model(settings: dict[str, Any] | None = None, data_readiness_state: str = "", execution_safety_state: str = "", final_decision: str = "", primary_blocker: str = "", primary_blocker_stage: str = "") -> dict[str, Any]:
    settings = dict(settings or {})
    entry = canonical_entry_logic(settings)
    return {
        "profile": str(settings.get("strategy_profile") or "BALANCED").upper(),
        "entry_logic": entry["entry_logic"],
        "entry_logic_label": entry["entry_logic_label"],
        "market_context_policy": market_context_policy(settings),
        "score_engine": entry["score_engine"],
        "data_readiness_state": data_readiness_state or "WAITING_FOR_FIRST_TICK",
        "execution_safety_state": execution_safety_state or "PAPER_SAFE",
        "final_decision": final_decision or "WAIT",
        "primary_blocker": primary_blocker or "",
        "primary_blocker_stage": primary_blocker_stage or "",
        "deprecated_entry_alias": bool(entry.get("deprecated_alias")),
    }


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
