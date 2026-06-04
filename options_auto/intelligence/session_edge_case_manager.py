from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class EarningsEvent:
    """Earnings announcement event."""
    symbol: str
    announcement_date: str  # YYYY-MM-DD
    expected_move: float = 0.0  # Expected volatility increase %
    is_today: bool = False
    is_tomorrow: bool = False
    days_until: int = 999
    historical_move: float = 0.0  # Historical move %

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ExpiryContext:
    """Options expiry context."""
    current_date: str  # YYYY-MM-DD
    expiry_date: str  # YYYY-MM-DD
    days_to_expiry: int = 999
    time_value_decay_rate: float = 0.0  # % theta decay per day
    theta_critical: bool = False  # < 2 days to expiry
    gamma_risk_high: bool = False  # Near ATM on last day
    liquidity_level: str = "normal"  # low/normal/high


class EarningsEdgeCaseHandler:
    """Handle trading edge cases around earnings announcements."""

    # Historical move mappings (can be updated with real data)
    HISTORICAL_EARNINGS_MOVES = {
        "NIFTY": 1.5,
        "SENSEX": 1.2,
        "BANKNIFTY": 2.0,
        "BANKEX": 1.8,
    }

    def __init__(self):
        self.earnings_calendar: dict[str, EarningsEvent] = {}

    def is_earnings_event(
        self,
        symbol: str,
        current_date: str,
        earnings_dates: dict[str, str] | None = None,
    ) -> EarningsEvent | None:
        """Check if symbol has earnings event nearby."""
        if not earnings_dates or symbol not in earnings_dates:
            return None

        earnings_date_str = earnings_dates.get(symbol)
        if not earnings_date_str:
            return None

        try:
            current = datetime.strptime(current_date, "%Y-%m-%d")
            earnings = datetime.strptime(earnings_date_str, "%Y-%m-%d")
            days_diff = (earnings - current).days

            if days_diff < 0:
                return None  # Earnings already passed

            is_today = days_diff == 0
            is_tomorrow = days_diff == 1

            historical_move = self.HISTORICAL_EARNINGS_MOVES.get(symbol, 1.5)

            event = EarningsEvent(
                symbol=symbol,
                announcement_date=earnings_date_str,
                expected_move=historical_move * 1.5,  # Expect 50% more than historical
                is_today=is_today,
                is_tomorrow=is_tomorrow,
                days_until=days_diff,
                historical_move=historical_move,
            )

            return event
        except ValueError:
            return None

    def earnings_trading_restrictions(
        self,
        earnings_event: EarningsEvent,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get trading restrictions for earnings event."""
        settings = dict(settings or {})
        restrictions = {
            "allowed": True,
            "blockers": [],
            "warnings": [],
            "recommendations": [],
        }

        if earnings_event.is_today:
            restrictions["blockers"].append("Earnings announcement today: no new options entries allowed.")
            restrictions["recommendations"].append("Close existing positions before market close.")

        elif earnings_event.is_tomorrow:
            restrictions["warnings"].append("Earnings announcement tomorrow: consider exiting positions ahead of event.")
            if settings.get("aggressive_earnings_trading"):
                restrictions["recommendations"].append("Use tighter stoploss (2x normal).")
            else:
                restrictions["blockers"].append("Earnings tomorrow: new entries blocked.")

        elif earnings_event.days_until <= 3:
            restrictions["warnings"].append(f"Earnings in {earnings_event.days_until} days: IV will spike.")
            restrictions["recommendations"].append("Consider reduced position size (50% of normal).")
            if not settings.get("allow_earnings_week_trading"):
                restrictions["blockers"].append("Earnings week trading disabled in settings.")

        if earnings_event.expected_move > 3.0:
            restrictions["recommendations"].append("Expected move >3%: consider straddle/strangle instead of directional.")

        restrictions["allowed"] = not restrictions["blockers"]
        return restrictions


class ExpiryEdgeCaseHandler:
    """Handle trading edge cases around options expiry."""

    def __init__(self):
        self.expiry_contexts: dict[str, ExpiryContext] = {}

    def get_expiry_context(
        self,
        symbol: str,
        current_date: str,
        expiry_date: str,
    ) -> ExpiryContext:
        """Get current expiry context for a symbol."""
        try:
            current = datetime.strptime(current_date, "%Y-%m-%d")
            expiry = datetime.strptime(expiry_date, "%Y-%m-%d")
            days_to_exp = (expiry - current).days

            # Theta decay increases dramatically in last week
            if days_to_exp <= 1:
                theta_rate = 0.15  # 15% decay per day
                liquidity = "low"
                gamma_risk = True
            elif days_to_exp <= 3:
                theta_rate = 0.08
                liquidity = "medium"
                gamma_risk = True
            elif days_to_exp <= 7:
                theta_rate = 0.04
                liquidity = "normal"
                gamma_risk = False
            else:
                theta_rate = 0.015
                liquidity = "high"
                gamma_risk = False

            context = ExpiryContext(
                current_date=current_date,
                expiry_date=expiry_date,
                days_to_expiry=days_to_exp,
                time_value_decay_rate=theta_rate,
                theta_critical=days_to_exp <= 2,
                gamma_risk_high=gamma_risk,
                liquidity_level=liquidity,
            )

            return context
        except ValueError:
            return ExpiryContext(current_date=current_date, expiry_date=expiry_date)

    def expiry_trading_restrictions(
        self,
        expiry_context: ExpiryContext,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get trading restrictions for expiry context."""
        settings = dict(settings or {})
        restrictions = {
            "allowed": True,
            "blockers": [],
            "warnings": [],
            "recommendations": [],
            "adjustments": {},
        }

        if expiry_context.days_to_expiry < 0:
            restrictions["blockers"].append("Expiry date has passed.")
            restrictions["allowed"] = False
            return restrictions

        if expiry_context.theta_critical:
            restrictions["blockers"].append(f"Trading not allowed in final {expiry_context.days_to_expiry} day(s) to expiry.")
            restrictions["recommendations"].append("Square-off all positions before market close on expiry day.")
            restrictions["adjustments"]["max_holding_minutes"] = 60
            restrictions["adjustments"]["force_square_off"] = True

        elif expiry_context.days_to_expiry <= 3:
            restrictions["warnings"].append(f"Last 3 days to expiry ({expiry_context.days_to_expiry} days left).")
            restrictions["recommendations"].append("Consider taking profits early (don't hold to expiry).")
            restrictions["adjustments"]["position_size_multiplier"] = 0.5
            restrictions["adjustments"]["max_holding_minutes"] = 120
            restrictions["adjustments"]["profit_target_reduction"] = 0.8  # 80% of normal target

        elif expiry_context.days_to_expiry <= 7:
            restrictions["warnings"].append("Last week before expiry: liquidity and volatility will increase.")
            restrictions["recommendations"].append("Monitor for gamma risk near ATM strikes.")
            restrictions["adjustments"]["position_size_multiplier"] = 0.75
            if expiry_context.gamma_risk_high:
                restrictions["warnings"].append("High gamma risk: delta can change rapidly.")

        if expiry_context.liquidity_level == "low":
            restrictions["warnings"].append("Low liquidity: spreads are wide, slippage is high.")
            restrictions["recommendations"].append("Use limit orders only, increase limit price.")

        if settings.get("no_trading_last_day_expiry") and expiry_context.days_to_expiry <= 1:
            restrictions["blockers"].append("No trading on last day before expiry (setting enabled).")

        restrictions["allowed"] = not restrictions["blockers"]
        return restrictions


class SessionEdgeCaseManager:
    """Unified manager for earnings and expiry edge cases."""

    def __init__(self):
        self.earnings_handler = EarningsEdgeCaseHandler()
        self.expiry_handler = ExpiryEdgeCaseHandler()
        self.session_edge_cases: list[dict[str, Any]] = []

    def evaluate_session_edges(
        self,
        symbol: str,
        current_date: str,
        expiry_date: str,
        earnings_dates: dict[str, str] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate all edge cases for current session."""
        settings = dict(settings or {})

        # Check earnings
        earnings_event = self.earnings_handler.is_earnings_event(symbol, current_date, earnings_dates)
        earnings_restrictions = (
            self.earnings_handler.earnings_trading_restrictions(earnings_event, settings)
            if earnings_event
            else {"allowed": True, "blockers": [], "warnings": [], "recommendations": []}
        )

        # Check expiry
        expiry_context = self.expiry_handler.get_expiry_context(symbol, current_date, expiry_date)
        expiry_restrictions = self.expiry_handler.expiry_trading_restrictions(expiry_context, settings)

        # Combine restrictions
        combined_blockers = earnings_restrictions.get("blockers", []) + expiry_restrictions.get("blockers", [])
        combined_warnings = earnings_restrictions.get("warnings", []) + expiry_restrictions.get("warnings", [])
        combined_recommendations = (
            earnings_restrictions.get("recommendations", []) + expiry_restrictions.get("recommendations", [])
        )

        # Merge adjustments
        adjustments = {}
        adjustments.update(expiry_restrictions.get("adjustments", {}))
        adjustments.update(earnings_restrictions.get("adjustments", {}))

        result = {
            "symbol": symbol,
            "current_date": current_date,
            "allowed": not combined_blockers,
            "blockers": list(dict.fromkeys(combined_blockers)),
            "warnings": list(dict.fromkeys(combined_warnings)),
            "recommendations": list(dict.fromkeys(combined_recommendations)),
            "adjustments": adjustments,
            "earnings_event": earnings_event.to_dict() if earnings_event else None,
            "expiry_context": expiry_context.to_dict(),
        }

        self.session_edge_cases.append(result)
        return result

    def should_trade(self, evaluation: dict[str, Any]) -> bool:
        """Quick check: should we trade given edge case evaluation?"""
        return evaluation.get("allowed", False)

    def get_position_size_adjustment(self, evaluation: dict[str, Any]) -> float:
        """Get position size adjustment factor (0.5 to 1.0)."""
        multiplier = evaluation.get("adjustments", {}).get("position_size_multiplier", 1.0)
        return max(0.5, min(1.0, multiplier))

    def snapshot(self) -> dict[str, Any]:
        """Get recent edge case evaluations."""
        return {
            "recent_evaluations": self.session_edge_cases[-20:],
            "total_evaluated": len(self.session_edge_cases),
        }
