from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

import pandas as pd

from options_auto.config.options_auto_defaults import normalize_settings
from options_auto.constants import MODE_BACKTEST, MODE_PAPER, MODE_REAL, REAL_EXECUTION_DISABLED_REASON, SIDE_CE, SIDE_PE, SIDE_WAIT
from options_auto.core.mode_guard import ModeGuard, normalize_mode
from options_auto.execution.execution_safety import DataQualityEngine
from options_auto.intelligence.adaptive_risk_engine import PositionSizer, RiskEngine
from options_auto.intelligence.entry_timing_engine import EntryTimingEngine
from options_auto.intelligence.exit_manager import build_long_option_trade_plan
from options_auto.intelligence.feature_builder import build_index_features
from options_auto.intelligence.master_governor import MasterGovernor
from options_auto.intelligence.market_cue_engine import MarketCueEngine
from options_auto.intelligence.options_greeks_risk_engine import OptionsGreeksRiskEngine
from options_auto.intelligence.professional_discipline import ProfessionalDisciplineEngine
from options_auto.intelligence.regime_classifier import RegimeClassifier
from options_auto.intelligence.simple_ohlcv_entry import resolve_entry_dependency_mode, resolve_simple_ohlcv_side, simple_ohlcv_entry_enabled, simple_ohlcv_threshold
from options_auto.intelligence.strike_selector import StrikeSelector


def evaluate_options_auto_decision(
    mode: str,
    settings: dict,
    index_history: pd.DataFrame,
    option_candidates: list[dict],
    quotes: dict,
    market_cue_payload: dict,
    risk_state: dict,
    account_state: dict,
    timestamp: Any,
) -> dict:
    settings = normalize_settings({**dict(settings or {}), "mode": mode})
    mode = normalize_mode(mode or settings.get("mode"))
    settings["mode"] = mode
    timestamp = timestamp or _timestamp_from_history(index_history) or datetime.now()
    timestamp_text = _timestamp_text(timestamp)
    settings["timestamp"] = timestamp_text

    precomputed_features = dict((market_cue_payload or {}).get("precomputed_index_features") or {})
    index_features = precomputed_features or build_index_features(_frame(index_history))
    if not index_features.get("close"):
        if mode not in {MODE_PAPER, MODE_REAL}:
            index_features = _legacy_features(market_cue_payload)

    cue_payload = {**dict(market_cue_payload or {}), "index_features": index_features, "features": index_features}
    market_cue = MarketCueEngine().evaluate(cue_payload, phase=cue_payload.get("phase") or cue_payload.get("market_phase") or cue_payload.get("cue_phase") or "")
    regime = RegimeClassifier().classify(index_features, market_cue.to_dict())
    entry_dependency_mode = resolve_entry_dependency_mode(settings)
    simple_mode = simple_ohlcv_entry_enabled(settings)
    selected_side = _selected_side(cue_payload, market_cue.to_dict(), regime.to_dict())
    simple_side = resolve_simple_ohlcv_side(index_features, settings) if simple_mode else {}
    if simple_mode and simple_side.get("side") in {SIDE_CE, SIDE_PE}:
        selected_side = str(simple_side["side"])

    available_capital = _available_capital(mode, settings, account_state)
    settings["available_capital"] = available_capital
    news_score = _number(cue_payload.get("news_score"), market_cue.components.get("news", 0.0))
    context = {
        "selected_side": selected_side,
        "regime": regime.to_dict(),
        "market_cue": market_cue.to_dict(),
        "index_features": index_features,
        "timestamp": timestamp_text,
        "news_score": news_score,
        "avoid_first_minutes_enabled": bool(settings.get("avoid_first_minutes")),
        "late_scalp_enabled": bool(settings.get("late_scalp_enabled")),
        "time_of_day_score": cue_payload.get("time_of_day_score"),
        "entry_dependency_mode": entry_dependency_mode,
        "simple_ohlcv_side": simple_side,
    }

    spot = _number(cue_payload.get("spot"), _number(cue_payload.get("index_ltp"), index_features.get("close", 0.0)))
    selection = StrikeSelector().select(list(option_candidates or []), dict(quotes or {}), spot, selected_side, settings, context)
    selected = dict(selection.selected or {})
    blockers = list(selection.blockers)
    warnings: list[str] = []
    warnings.extend(regime.warnings)

    data_quality = {"allowed": True, "state": "DATA_OK", "blockers": [], "warnings": []}
    theta_premium_risk = {"allowed": True, "blockers": [], "warnings": [], "theta_risk_score": 70}
    position_size = {"quantity": 0, "lots": 0, "reason": "No selected contract."}
    trade_plan: dict[str, Any] = {}
    trade_score = {"score": 0.0, "breakdown": {}, "weights": {}}
    entry_timing = {"allowed": True, "state": "TIMING_OK", "blockers": [], "warnings": []}

    if selected:
        data_quality = DataQualityEngine().validate_quote(
            {
                "ltp": selected.get("ltp"),
                "spread_pct": selected.get("spread_pct"),
                "demo_data": selected.get("demo_data"),
                "age_seconds": cue_payload.get("quote_age_seconds", selected.get("age_seconds", 0)),
            },
            settings,
        ).to_dict()
        blockers.extend(data_quality.get("blockers") or [])
        warnings.extend(data_quality.get("warnings") or [])

        preliminary_stop = max(
            _number(selected.get("option_atr14"), _number(selected.get("atr14"))),
            _number(selected.get("ask"), selected.get("ltp")) * _number(settings.get("min_stoploss_pct"), 3.0) / 100.0,
            _number(settings.get("minimum_stoploss_points"), 2.0),
        )
        sizing_settings = {**settings, "stop_distance_points": preliminary_stop}
        position_size = PositionSizer().quantity(
            _number(selected.get("ask"), selected.get("ltp")),
            int(_number(selected.get("lot_size"))),
            available_capital,
            sizing_settings,
        )
        if int(position_size.get("quantity") or 0) <= 0:
            blockers.append(position_size.get("reason") or "Insufficient capital/risk budget for one lot.")

        selected["quantity"] = int(position_size.get("quantity") or selected.get("lot_size") or 1)
        selected["days_to_expiry"] = _days_to_expiry(selected.get("expiry"), timestamp)
        theta_settings = {
            **settings,
            "quantity": selected["quantity"],
            "today": _date_from(timestamp),
            "regime": regime.regime,
            "regime_target_multiplier": regime.target_multiplier,
        }
        theta_premium_risk = OptionsGreeksRiskEngine().evaluate(selected, theta_settings, today=_date_from(timestamp))
        if simple_mode:
            theta_premium_risk = _relax_simple_ohlcv_theta(theta_premium_risk)
        selected["theta_premium_risk"] = theta_premium_risk
        blockers.extend(theta_premium_risk.get("blockers") or [])
        warnings.extend(theta_premium_risk.get("warnings") or [])
        warnings.extend(selected.get("warnings") or [])

        trade_score = {
            "score": selected.get("score", 0.0),
            "breakdown": selected.get("breakdown", {}),
            "weights": selected.get("weights", {}),
            "entry_dependency_mode": selected.get("entry_dependency_mode") or entry_dependency_mode,
            "entry_dependency_reason": selected.get("entry_dependency_reason"),
        }
        effective_threshold = _effective_entry_score_threshold(settings)
        if float(trade_score.get("score") or 0) < effective_threshold:
            blockers.append(f"TotalScore {float(trade_score.get('score') or 0):.1f} is below threshold {effective_threshold:.1f}.")

        entry_timing = EntryTimingEngine().evaluate(
            dict(cue_payload.get("signal_candle") or selected.get("candle") or {}),
            {
                "ltp": selected.get("ltp"),
                "spread_pct": selected.get("spread_pct"),
                "intended_entry": cue_payload.get("intended_entry", selected.get("ask") or selected.get("ltp")),
                "option_atr14": selected.get("option_atr14") or selected.get("atr14"),
                "signal_age_seconds": cue_payload.get("signal_age_seconds"),
            },
            settings,
        )
        if simple_mode:
            entry_timing = _relax_simple_ohlcv_entry_timing(entry_timing)
        blockers.extend(entry_timing.get("blockers") or [])
        warnings.extend(entry_timing.get("warnings") or [])

        trade_plan = build_long_option_trade_plan(selected, position_size, regime.to_dict(), settings)

    risk = RiskEngine().evaluate(settings, risk_state or {}, now_epoch=_epoch(timestamp))
    discipline = ProfessionalDisciplineEngine().evaluate(
        {
            "aggressiveness": regime.aggressiveness,
            "chase_detected": bool(entry_timing.get("chase_distance", 0) > 0 and entry_timing.get("blockers")),
            "manual_override_to_increase_risk": bool(cue_payload.get("manual_override_to_increase_risk")),
        },
        risk_state or {},
    )
    execution = _execution_state(mode)

    market_blockers = []
    if (market_cue.to_dict().get("fii_dii_status") or {}).get("status") == "REQUIRED_MISSING_UPLOAD":
        market_blockers.append("FII/DII CSV upload is required for pre-market cue.")
    if regime.recommended_side == SIDE_WAIT and not (simple_mode and selected_side in {SIDE_CE, SIDE_PE}):
        market_blockers.append(regime.no_trade_reason or "Regime says WAIT.")
    if mode in {MODE_PAPER, MODE_REAL} and not index_features.get("close"):
        market_blockers.append("Live index candle data is unavailable.")
    if market_cue.recommended_side == SIDE_WAIT and settings.get("market_cue_alignment_required") and not simple_mode:
        market_blockers.append("Market cue says WAIT.")
    if selected_side in {SIDE_CE, SIDE_PE} and market_cue.recommended_side in {SIDE_CE, SIDE_PE} and selected_side != market_cue.recommended_side and not simple_mode:
        market_blockers.append("Market cue is strongly opposite the selected side.")
    if simple_mode and selected_side == SIDE_WAIT:
        market_blockers.append("Simple OHLCV/volume-profile entry did not produce a directional setup.")

    strategy_blockers = list(dict.fromkeys(blockers))
    governor = MasterGovernor().evaluate(
        ModeGuard(mode=mode).to_dict(),
        data_quality,
        risk,
        discipline,
        execution,
        market={"blockers": market_blockers},
        strategy={"selected": bool(selected), "blockers": strategy_blockers},
    )
    blockers = list(dict.fromkeys(strategy_blockers + market_blockers + (risk.get("blockers") or []) + (discipline.get("blockers") or []) + (execution.get("blockers") or []) + (governor.get("blockers") or [])))
    warnings = list(dict.fromkeys(warnings + (risk.get("warnings") or []) + (discipline.get("warnings") or []) + (execution.get("warnings") or []) + (governor.get("warnings") or [])))
    allowed = bool(selected) and not blockers and bool(governor.get("allowed"))

    decision_snapshot = _decision_snapshot(
        timestamp_text,
        mode,
        settings,
        index_features,
        market_cue.to_dict(),
        regime.to_dict(),
        selected_side,
        selected,
        theta_premium_risk,
        trade_score,
        data_quality,
        risk,
        discipline,
        governor,
        blockers,
        allowed,
        trade_plan,
    )
    explanation = _explanation(allowed, selected_side, selected, trade_score, blockers)
    real_mode = mode == MODE_REAL
    return {
        "mode": mode,
        "timestamp": timestamp_text,
        "market_cue": market_cue.to_dict(),
        "regime": regime.to_dict(),
        "selected_side": selected_side if selected else SIDE_WAIT,
        "selected_contract": selected,
        "selection": selection.to_dict(),
        "trade_score": trade_score,
        "entry_dependency_mode": entry_dependency_mode,
        "simple_ohlcv_side": simple_side,
        "data_quality": data_quality,
        "theta_premium_risk": theta_premium_risk,
        "options_risk": theta_premium_risk,
        "risk": risk,
        "discipline": discipline,
        "entry_timing": entry_timing,
        "execution": execution,
        "governor": governor,
        "position_size": position_size,
        "trade_plan": trade_plan,
        "allowed": allowed,
        "blockers": blockers,
        "warnings": warnings,
        "explanation": explanation,
        "decision_snapshot": decision_snapshot,
        "real_execution_enabled": real_mode,
        "real_execution_reason": (
            "Real execution is guarded by live login, preflight, final validation, OCO, and reconciliation."
            if real_mode
            else REAL_EXECUTION_DISABLED_REASON
        ),
    }


def _legacy_features(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(payload or {})
    features = dict(payload.get("features") or payload.get("index_features") or {})
    trend = (
        _number(features.get("trend_strength_score"))
        or _number(features.get("ema_alignment_score"))
        + _number(features.get("vwap_score"))
        + _number(features.get("rsi_slope_score"))
        + _number(features.get("volume_score"))
        + _number(features.get("depth_score"))
        or _number(payload.get("technical_score"), payload.get("trend_score", 0))
    )
    close = _number(features.get("close"), payload.get("spot") or payload.get("index_ltp") or 0)
    return {
        "close": close,
        "ema9": _number(features.get("ema9"), close + 20 if trend > 0 else close - 20),
        "ema20": _number(features.get("ema20"), close if close else 0),
        "ema50": _number(features.get("ema50"), close - 50 if trend > 0 else close + 50),
        "vwap": _number(features.get("vwap"), close - 30 if trend > 0 else close + 30),
        "rsi14": _number(features.get("rsi14"), 64 if trend > 0 else 36 if trend < 0 else 50),
        "rsi_slope_3": _number(features.get("rsi_slope_3"), 5 if trend > 0 else -5 if trend < 0 else 0),
        "atr14": _number(features.get("atr14"), 50),
        "atr_pct": _number(features.get("atr_pct"), 0.25),
        "relative_volume": _number(features.get("relative_volume"), 1.6 if trend else 1.0),
        "body_pct": _number(features.get("body_pct"), 55 if trend else 20),
        "upper_wick_pct": _number(features.get("upper_wick_pct"), 12),
        "lower_wick_pct": _number(features.get("lower_wick_pct"), 20),
        "ema_alignment": features.get("ema_alignment") or ("BULLISH" if trend > 0 else "BEARISH" if trend < 0 else "MIXED"),
        "vwap_position": features.get("vwap_position") or ("ABOVE_VWAP" if trend > 0 else "BELOW_VWAP" if trend < 0 else "AT_VWAP"),
        "trend_strength_score": max(-100.0, min(100.0, trend)),
        "warmup_complete": bool(features.get("warmup_complete", False)),
    }


def _selected_side(payload: dict[str, Any], market_cue: dict[str, Any], regime: dict[str, Any]) -> str:
    explicit = str(payload.get("side") or "").upper()
    if explicit in {SIDE_CE, SIDE_PE, SIDE_WAIT}:
        return explicit
    regime_side = str(regime.get("recommended_side") or SIDE_WAIT).upper()
    cue_side = str(market_cue.get("recommended_side") or SIDE_WAIT).upper()
    if regime_side in {SIDE_CE, SIDE_PE}:
        return regime_side
    if cue_side in {SIDE_CE, SIDE_PE}:
        return cue_side
    return SIDE_WAIT


def _decision_snapshot(
    timestamp: str,
    mode: str,
    settings: dict[str, Any],
    features: dict[str, Any],
    market_cue: dict[str, Any],
    regime: dict[str, Any],
    selected_side: str,
    selected: dict[str, Any],
    theta: dict[str, Any],
    score: dict[str, Any],
    data_quality: dict[str, Any],
    risk: dict[str, Any],
    discipline: dict[str, Any],
    governor: dict[str, Any],
    blockers: list[str],
    allowed: bool,
    trade_plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "mode": mode,
        "underlying": settings.get("underlying"),
        "index_close": features.get("close"),
        "index_ema9": features.get("ema9"),
        "index_ema20": features.get("ema20"),
        "index_ema50": features.get("ema50"),
        "index_vwap": features.get("vwap"),
        "index_rsi14": features.get("rsi14"),
        "index_atr14": features.get("atr14"),
        "relative_volume": features.get("relative_volume"),
        "market_cue": market_cue,
        "regime": regime,
        "selected_side": selected_side,
        "selected_contract": selected,
        "expiry": selected.get("expiry"),
        "strike": selected.get("strike"),
        "option_ltp": selected.get("ltp"),
        "bid": selected.get("bid"),
        "ask": selected.get("ask"),
        "spread_pct": selected.get("spread_pct"),
        "volume": selected.get("volume"),
        "oi": selected.get("oi"),
        "moneyness": selected.get("moneyness"),
        "premium_momentum": selected.get("premium_momentum"),
        "theta_risk": theta,
        "expected_edge": theta.get("expected_edge") if isinstance(theta, dict) else {},
        "trade_score_breakdown": score,
        "data_quality": data_quality,
        "risk_state": risk,
        "discipline_state": discipline,
        "governor_state": governor,
        "blockers": blockers,
        "final_decision": "ALLOW" if allowed else "WAIT",
        "trade_plan": trade_plan,
    }


def _explanation(allowed: bool, side: str, selected: dict[str, Any], score: dict[str, Any], blockers: list[str]) -> str:
    if blockers:
        return "Blocked: " + "; ".join(blockers[:6])
    if allowed:
        return f"{side} setup allowed for {selected.get('tradingsymbol')} with score {float(score.get('score') or 0):.1f}."
    return "No trade: setup did not produce a valid selected contract."


def _effective_entry_score_threshold(settings: dict[str, Any]) -> float:
    if simple_ohlcv_entry_enabled(settings):
        return simple_ohlcv_threshold(settings)
    return _number(settings.get("buy_score_threshold"), 70.0)


def _relax_simple_ohlcv_theta(theta: dict[str, Any]) -> dict[str, Any]:
    result = dict(theta or {})
    blockers = []
    warnings = list(result.get("warnings") or [])
    for blocker in result.get("blockers") or []:
        text = str(blocker or "")
        if text == "Expected premium move does not beat theta/spread/slippage/charges.":
            warnings.append("Simple OHLCV mode warning: " + text)
        else:
            blockers.append(text)
    result["blockers"] = list(dict.fromkeys(blockers))
    result["warnings"] = list(dict.fromkeys(warnings))
    result["allowed"] = not result["blockers"]
    return result


def _relax_simple_ohlcv_entry_timing(entry_timing: dict[str, Any]) -> dict[str, Any]:
    result = dict(entry_timing or {})
    hard = {"Signal is stale.", "Signal age is invalid.", "Spread too wide."}
    blockers = []
    warnings = list(result.get("warnings") or [])
    for blocker in result.get("blockers") or []:
        text = str(blocker or "")
        if text in hard:
            blockers.append(text)
        else:
            warnings.append("Simple OHLCV mode warning: " + text)
    result["blockers"] = list(dict.fromkeys(blockers))
    result["warnings"] = list(dict.fromkeys(warnings))
    result["allowed"] = not result["blockers"]
    result["state"] = "TIMING_OK" if not result["blockers"] else "BLOCKED_BY_TIMING"
    return result


def _execution_state(mode: str) -> dict[str, Any]:
    if mode == MODE_REAL:
        return {
            "allowed": True,
            "state": "REAL_GUARDED_EXECUTION",
            "blockers": [],
            "warnings": ["Real orders require LIVE login, preflight, final validation, execution safety, OCO, and reconciliation."],
        }
    return {"allowed": True, "state": "SIMULATION_MODE", "blockers": [], "warnings": []}


def _available_capital(mode: str, settings: dict[str, Any], account_state: dict[str, Any]) -> float:
    if account_state:
        for key in ("available_capital", "available_balance", "available", "cash"):
            if account_state.get(key) not in ("", None):
                return _number(account_state.get(key))
    return _number(settings.get("available_capital"), settings.get("paper_starting_balance", 0))


def _frame(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value
    if isinstance(value, list):
        return pd.DataFrame(value)
    return pd.DataFrame()


def _timestamp_from_history(frame: Any) -> Any:
    frame = _frame(frame)
    if frame.empty:
        return None
    row = frame.iloc[-1]
    return row.get("datetime") or row.get("timestamp") or row.get("date")


def _timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or datetime.now().isoformat())


def _date_from(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            pass
    return date.today()


def _days_to_expiry(expiry: Any, timestamp: Any) -> int | None:
    if expiry in ("", None):
        return None
    try:
        expiry_date = datetime.fromisoformat(str(expiry)[:10]).date()
    except ValueError:
        return None
    return (expiry_date - _date_from(timestamp)).days


def _epoch(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return time.time()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
