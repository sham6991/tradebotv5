from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT


@dataclass
class TradeCandidateValidation:
    allowed: bool
    stage: str
    selected_contract: dict[str, Any]
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)
    theta_premium_risk: dict[str, Any] = field(default_factory=dict)
    trade_score: dict[str, Any] = field(default_factory=dict)
    entry_timing: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "stage": self.stage,
            "selected_contract": dict(self.selected_contract or {}),
            "blockers": list(self.blockers or []),
            "warnings": list(self.warnings or []),
            "evidence": dict(self.evidence or {}),
            "data_quality": dict(self.data_quality or {}),
            "theta_premium_risk": dict(self.theta_premium_risk or {}),
            "trade_score": dict(self.trade_score or {}),
            "entry_timing": dict(self.entry_timing or {}),
        }


class TradeCandidateValidator:
    def validate(
        self,
        *,
        selected_side: str,
        selected_contract: dict[str, Any] | None,
        settings: dict[str, Any] | None = None,
        data_quality: dict[str, Any] | None = None,
        theta_premium_risk: dict[str, Any] | None = None,
        trade_score: dict[str, Any] | None = None,
        entry_timing: dict[str, Any] | None = None,
        selection_blockers: list[str] | None = None,
        effective_score_threshold: float | None = None,
    ) -> TradeCandidateValidation:
        side = str(selected_side or SIDE_WAIT).upper()
        selected = dict(selected_contract or {})
        settings = dict(settings or {})
        data_quality = dict(data_quality or {})
        theta = dict(theta_premium_risk or {})
        score = dict(trade_score or {})
        timing = dict(entry_timing or {})
        selection_blockers = list(selection_blockers or [])

        if side not in {SIDE_CE, SIDE_PE}:
            return self._result(False, "NO_SIDE", selected, ["No CE/PE side was selected."], [], settings, data_quality, theta, score, timing)
        if not selected:
            blockers = selection_blockers or ["No selected option contract."]
            return self._result(False, "NO_CONTRACT", selected, blockers, [], settings, data_quality, theta, score, timing)

        blockers: list[str] = []
        warnings: list[str] = []
        blockers.extend(selection_blockers)
        warnings.extend(selected.get("warnings") or [])
        warnings.extend(data_quality.get("warnings") or [])
        warnings.extend(theta.get("warnings") or [])
        warnings.extend(timing.get("warnings") or [])

        contract_blockers = self._contract_blockers(selected)
        contract_side = str(selected.get("option_type") or selected.get("instrument_type") or "").upper()
        if contract_side in {SIDE_CE, SIDE_PE} and side in {SIDE_CE, SIDE_PE} and contract_side != side:
            contract_blockers.append(f"Selected contract side {contract_side} does not match intended side {side}.")
        if contract_blockers:
            blockers.extend(contract_blockers)
            return self._result(False, "CONTRACT_INVALID", selected, blockers, warnings, settings, data_quality, theta, score, timing)

        quote_blockers = self._quote_blockers(data_quality)
        if quote_blockers:
            blockers.extend(quote_blockers)
            return self._result(False, "QUOTE_INVALID", selected, blockers, warnings, settings, data_quality, theta, score, timing)

        liquidity_blockers = self._liquidity_blockers(selected, settings)
        if liquidity_blockers:
            blockers.extend(liquidity_blockers)
            return self._result(False, "LIQUIDITY_BLOCKED", selected, blockers, warnings, settings, data_quality, theta, score, timing)

        theta_blockers = list(theta.get("blockers") or [])
        if theta_blockers:
            blockers.extend(theta_blockers)
            return self._result(False, "THETA_BLOCKED", selected, blockers, warnings, settings, data_quality, theta, score, timing)

        threshold = _number(effective_score_threshold, settings.get("buy_score_threshold", 70.0))
        score_value = _number(score.get("score"))
        if score_value < threshold:
            blockers.append(f"TotalScore {score_value:.1f} is below threshold {threshold:.1f}.")
            return self._result(False, "SCORE_BLOCKED", selected, blockers, warnings, settings, data_quality, theta, score, timing)

        timing_blockers = list(timing.get("blockers") or [])
        if timing_blockers:
            blockers.extend(timing_blockers)
            return self._result(False, "TIMING_BLOCKED", selected, blockers, warnings, settings, data_quality, theta, score, timing)

        return self._result(True, "VALID", selected, blockers, warnings, settings, data_quality, theta, score, timing)

    def _contract_blockers(self, selected: dict[str, Any]) -> list[str]:
        blockers = []
        if not selected.get("instrument_token") and not selected.get("token"):
            blockers.append("Missing instrument token.")
        if int(_number(selected.get("lot_size"))) <= 0:
            blockers.append("Missing lot size.")
        if str(selected.get("option_type") or selected.get("instrument_type") or "").upper() not in {SIDE_CE, SIDE_PE}:
            blockers.append("Selected contract is not a CE/PE option.")
        return blockers

    def _quote_blockers(self, data_quality: dict[str, Any]) -> list[str]:
        blockers = []
        for item in data_quality.get("blockers") or []:
            text = str(item or "")
            if not text:
                continue
            blockers.append(text)
        return blockers

    def _liquidity_blockers(self, selected: dict[str, Any], settings: dict[str, Any]) -> list[str]:
        blockers = []
        bid = _number(selected.get("bid"))
        ask = _number(selected.get("ask"))
        spread = _number(selected.get("spread_pct"))
        total_depth = _number(selected.get("total_depth"), _number(selected.get("bid_qty")) + _number(selected.get("ask_qty")))
        if bid <= 0 or ask <= 0 or ask < bid:
            blockers.append("Invalid bid/ask spread.")
        elif spread > _number(settings.get("max_spread_pct"), 0.6):
            blockers.append("Spread too wide.")
        if settings.get("strict_liquidity_filter") and not bool(selected.get("depth_present", bid > 0 and ask > 0)):
            blockers.append("Market depth is missing.")
        if total_depth < _number(settings.get("min_depth_qty"), 1):
            blockers.append("Depth too low.")
        min_volume = _number(settings.get("min_volume"))
        if min_volume > 0 and _number(selected.get("volume")) < min_volume:
            blockers.append("Volume below configured minimum.")
        min_oi = _number(settings.get("min_oi"))
        if min_oi > 0 and _number(selected.get("oi")) < min_oi:
            blockers.append("OI below configured minimum.")
        if settings.get("strict_liquidity_filter") and _number(selected.get("liquidity_score")) < 45:
            blockers.append("Liquidity score too low.")
        return blockers

    def _result(
        self,
        allowed: bool,
        stage: str,
        selected: dict[str, Any],
        blockers: list[str],
        warnings: list[str],
        settings: dict[str, Any],
        data_quality: dict[str, Any],
        theta: dict[str, Any],
        score: dict[str, Any],
        timing: dict[str, Any],
    ) -> TradeCandidateValidation:
        unique_blockers = _dedupe_root_causes(blockers)
        evidence = {
            "side": selected.get("option_type") or selected.get("instrument_type"),
            "tradingsymbol": selected.get("tradingsymbol"),
            "ltp": selected.get("ltp"),
            "bid": selected.get("bid"),
            "ask": selected.get("ask"),
            "spread_pct": selected.get("spread_pct"),
            "total_depth": selected.get("total_depth"),
            "volume": selected.get("volume"),
            "oi": selected.get("oi"),
            "score": score.get("score"),
            "threshold": settings.get("buy_score_threshold"),
        }
        return TradeCandidateValidation(
            allowed=bool(allowed and not unique_blockers),
            stage=stage,
            selected_contract=selected,
            blockers=unique_blockers,
            warnings=list(dict.fromkeys(warnings or [])),
            evidence=evidence,
            data_quality=data_quality,
            theta_premium_risk=theta,
            trade_score=score,
            entry_timing=timing,
        )


def _dedupe_root_causes(blockers: list[str]) -> list[str]:
    seen = set()
    result = []
    for blocker in blockers or []:
        text = str(blocker or "").strip()
        if not text:
            continue
        key = _root_key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _root_key(text: str) -> str:
    lowered = text.lower()
    if "spread" in lowered:
        return "spread"
    if "stale" in lowered:
        return "stale"
    if "depth" in lowered:
        return "depth"
    if "liquidity" in lowered:
        return "liquidity"
    if "theta" in lowered:
        return "theta"
    if "totalscore" in lowered or "score" in lowered:
        return "score"
    if "chasing" in lowered or "chase" in lowered or "moved too far" in lowered:
        return "chase"
    if "quote" in lowered and ("missing" in lowered or "unavailable" in lowered):
        return "quote_missing"
    return lowered


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
