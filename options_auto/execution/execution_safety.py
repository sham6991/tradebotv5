from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from options_auto.constants import MODE_REAL, REAL_EXECUTION_DISABLED_REASON
from options_auto.execution.quote_freshness import evaluate_quote_freshness


@dataclass
class SafetyDecision:
    allowed: bool
    state: str
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "state": self.state,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }


class DataQualityEngine:
    def validate_quote(self, quote: dict[str, Any] | None, settings: dict[str, Any] | None = None, now: datetime | None = None) -> SafetyDecision:
        settings = dict(settings or {})
        quote = dict(quote or {})
        blockers = []
        if not quote:
            blockers.append("Quote is missing.")
        if float(quote.get("ltp") or quote.get("last_price") or 0) <= 0:
            blockers.append("Quote LTP is unavailable.")
        mode = str(settings.get("mode") or "").upper()
        allow_demo = bool(settings.get("allow_demo_data") or mode in {"BACKTEST", "SHADOW", "DEBUG"})
        if quote.get("demo_data") and not allow_demo:
            blockers.append("Live quote data unavailable; demo/sample data cannot be used for paper or real trading.")
        spread_pct = float(quote.get("spread_pct") or 0)
        if spread_pct and spread_pct > float(settings.get("max_spread_pct") or 0.6):
            blockers.append("Quote spread is too wide.")
        freshness = evaluate_quote_freshness(quote, settings, now_epoch=now.timestamp() if now else None)
        for blocker in freshness.blockers:
            blockers.append("Quote is stale." if blocker == "Quote stale." else blocker)
        return SafetyDecision(not blockers, "DATA_OK" if not blockers else "BLOCKED_BY_DATA", blockers)


class RealOrderPreflight:
    def validate(self, mode: str, broker: Any = None, settings: dict[str, Any] | None = None, results_writable: bool = True) -> SafetyDecision:
        settings = dict(settings or {})
        blockers = []
        warnings = []
        if str(mode or "").upper() != MODE_REAL:
            return SafetyDecision(True, "NOT_REAL_MODE", warnings=["Real preflight skipped outside real mode."])
        if not settings.get("confirm_real_mode"):
            blockers.append("Real mode confirmation is missing.")
        if not broker:
            blockers.append("Real Zerodha client is not connected.")
        if not results_writable:
            blockers.append("Results folder is not writable.")
        if not settings.get("real_orders_enabled"):
            blockers.append(REAL_EXECUTION_DISABLED_REASON)
        return SafetyDecision(not blockers, "REAL_PREFLIGHT_OK" if not blockers else "BLOCKED_BY_EXECUTION", blockers, warnings)
