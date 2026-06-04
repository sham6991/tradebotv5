from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from options_auto.indicators.volatility import atr_expansion, realized_volatility


@dataclass
class VolatilityState:
    """Current volatility metrics."""
    atr_expansion: float = 1.0
    realized_vol: float = 0.0
    iv_level: str = "normal"  # low, normal, high, extreme
    vol_rank: float = 0.5  # 0-1 scale
    vol_percentile: float = 50.0  # 0-100 percentile


class DynamicPositionSizer:
    """Position sizing that adapts to volatility conditions.
    
    Key principles:
    - Lower position size in high volatility (protect capital)
    - Slightly higher position size in low volatility (take advantage)
    - Never exceed max lot limit regardless of volatility
    """

    def __init__(self):
        self.volatility_thresholds = {
            "low": {"atr_expansion": (0.0, 0.8), "realized_vol": (0.0, 10.0)},
            "normal": {"atr_expansion": (0.8, 1.5), "realized_vol": (10.0, 25.0)},
            "high": {"atr_expansion": (1.5, 2.2), "realized_vol": (25.0, 40.0)},
            "extreme": {"atr_expansion": (2.2, float("inf")), "realized_vol": (40.0, float("inf"))},
        }

    def calculate_volatility_state(
        self,
        candles: Any,  # pd.DataFrame
        iv_level: str = "normal",
    ) -> VolatilityState:
        """Calculate current volatility state."""
        atr_exp = 1.0
        realized_vol = 0.0

        try:
            if candles is not None and not candles.empty:
                atr_exp = float(atr_expansion(candles))
                close_series = candles.get("close") if hasattr(candles, "get") else candles["close"]
                realized_vol = float(realized_volatility(close_series))
        except Exception:
            pass

        vol_level = self._classify_volatility(atr_exp, realized_vol)
        vol_rank = self._calculate_vol_rank(atr_exp, realized_vol)

        return VolatilityState(
            atr_expansion=atr_exp,
            realized_vol=realized_vol,
            iv_level=iv_level,
            vol_rank=vol_rank,
            vol_percentile=vol_rank * 100.0,
        )

    def calculate_lot_multiplier(
        self,
        volatility_state: VolatilityState,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Calculate lot size multiplier based on volatility (0.5x to 1.5x).
        
        Returns:
            {
                "multiplier": 0.5-1.5,
                "volatility_level": "low/normal/high/extreme",
                "rationale": explanation
            }
        """
        settings = dict(settings or {})

        # Get volatility-based multipliers
        base_multiplier = 1.0
        vol_level = self._classify_volatility(volatility_state.atr_expansion, volatility_state.realized_vol)

        if vol_level == "low":
            # Low volatility: can take slightly more
            multiplier = 1.2
            rationale = "Low volatility: increased lot size (1.2x)"
        elif vol_level == "normal":
            # Normal volatility: baseline
            multiplier = 1.0
            rationale = "Normal volatility: baseline lot size (1.0x)"
        elif vol_level == "high":
            # High volatility: reduce size
            multiplier = 0.75
            rationale = "High volatility: reduced lot size (0.75x)"
        else:  # extreme
            # Extreme volatility: significantly reduce
            multiplier = 0.5
            rationale = "Extreme volatility: heavily reduced lot size (0.5x)"

        # IV crush adjustment
        iv_crush_enabled = settings.get("iv_crush_protection", True)
        if iv_crush_enabled and volatility_state.iv_level == "extreme":
            multiplier *= 0.8
            rationale += " [IV crush protection: 0.8x]"

        # Ensure multiplier respects bounds
        min_mult = float(settings.get("min_lot_multiplier", 0.5))
        max_mult = float(settings.get("max_lot_multiplier", 1.5))
        multiplier = max(min_mult, min(max_mult, multiplier))

        return {
            "multiplier": round(multiplier, 2),
            "volatility_level": vol_level,
            "atr_expansion": round(volatility_state.atr_expansion, 2),
            "realized_volatility": round(volatility_state.realized_vol, 2),
            "vol_percentile": round(volatility_state.vol_percentile, 2),
            "rationale": rationale,
        }

    def calculate_dynamic_quantity(
        self,
        premium: float,
        lot_size: int,
        available_capital: float,
        volatility_state: VolatilityState,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Calculate dynamic position size based on volatility.
        
        Process:
        1. Calculate base quantity (static)
        2. Apply volatility multiplier
        3. Respect max lot constraints
        4. Return final quantity
        """
        settings = dict(settings or {})
        lot_size = int(lot_size or 1)
        premium = float(premium or 0)
        available_capital = float(available_capital or 0)

        if lot_size <= 0 or premium <= 0 or available_capital <= 0:
            return {
                "quantity": 0,
                "lots": 0,
                "reason": "Invalid inputs: lot_size, premium, or capital missing/zero",
                "volatility_adjusted": False,
            }

        # Step 1: Calculate base quantity (static)
        cap_pct = float(settings.get("max_capital_per_trade_pct", 20))
        capital_cap = available_capital * cap_pct / 100.0
        max_lots_base = int(settings.get("max_lots_per_trade", 1))

        affordable_lots = int(capital_cap // (premium * lot_size))
        base_lots = min(max_lots_base, max(1, affordable_lots))

        # Step 2: Apply volatility multiplier
        vol_adjustment = self.calculate_lot_multiplier(volatility_state, settings)
        multiplier = vol_adjustment["multiplier"]

        adjusted_lots = max(1, int(base_lots * multiplier))

        # Step 3: Enforce hard max lot limit
        max_lots_hard = int(settings.get("absolute_max_lots_per_trade", 3))
        final_lots = min(max_lots_hard, adjusted_lots)

        final_quantity = final_lots * lot_size
        required_capital = final_lots * lot_size * premium

        return {
            "quantity": final_quantity,
            "lots": final_lots,
            "base_lots": base_lots,
            "multiplier": multiplier,
            "required_capital": round(required_capital, 2),
            "capital_cap": round(capital_cap, 2),
            "volatility_adjusted": multiplier != 1.0,
            "vol_adjustment": vol_adjustment,
            "reason": vol_adjustment.get("rationale", ""),
        }

    def _classify_volatility(self, atr_expansion: float, realized_vol: float) -> str:
        """Classify volatility level."""
        if atr_expansion >= 2.2 or realized_vol >= 40.0:
            return "extreme"
        if atr_expansion >= 1.5 or realized_vol >= 25.0:
            return "high"
        if atr_expansion >= 0.8 or realized_vol >= 10.0:
            return "normal"
        return "low"

    def _calculate_vol_rank(self, atr_expansion: float, realized_vol: float) -> float:
        """Calculate volatility rank as 0-1 scale."""
        # Normalize ATR expansion (0-3 maps to 0-1)
        atr_rank = min(1.0, atr_expansion / 3.0)

        # Normalize realized volatility (0-50 maps to 0-1)
        vol_rank = min(1.0, realized_vol / 50.0)

        # Average them
        avg_rank = (atr_rank + vol_rank) / 2.0
        return min(1.0, max(0.0, avg_rank))

    def snapshot(self) -> dict[str, Any]:
        """Get current configuration."""
        return {
            "volatility_thresholds": self.volatility_thresholds,
            "multiplier_bounds": {
                "min": 0.5,
                "max": 1.5,
            },
        }
