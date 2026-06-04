from __future__ import annotations

from typing import Any


class EntryTimingEngine:
    def evaluate(self, candle: dict[str, Any], option_quote: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        blockers = []
        warnings = []
        atr = float(candle.get("atr") or candle.get("atr14") or 0)
        candle_range = float(candle.get("high") or 0) - float(candle.get("low") or 0)
        if atr > 0 and candle_range > atr * 2.5:
            blockers.append("Signal candle is abnormal versus ATR.")
        intended = float(option_quote.get("intended_entry") or option_quote.get("ltp") or 0)
        ltp = float(option_quote.get("ltp") or 0)
        max_chase = float(settings.get("max_chase_points") or 3)
        if intended > 0 and ltp - intended > max_chase:
            blockers.append("Option premium moved beyond max chase threshold.")
        close_position = float(candle.get("close_position_pct") or 50)
        if close_position < 55:
            warnings.append("Candle confirmation is not near the strong close zone.")
        return {
            "allowed": not blockers,
            "state": "TIMING_OK" if not blockers else "BLOCKED_BY_TIMING",
            "blockers": blockers,
            "warnings": warnings,
        }

