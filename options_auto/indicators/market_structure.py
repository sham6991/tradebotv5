from __future__ import annotations

from typing import Any

import pandas as pd


def opening_range(frame: pd.DataFrame, minutes: int = 15) -> dict[str, Any]:
    if frame is None or frame.empty:
        return {"high": 0.0, "low": 0.0}
    rows = frame.head(max(1, int(minutes // 3)))
    return {"high": float(rows["high"].max()), "low": float(rows["low"].min())}


def breakout_state(frame: pd.DataFrame, level_high: float, level_low: float) -> str:
    if frame is None or frame.empty:
        return "UNKNOWN"
    close = float(frame.iloc[-1].get("close") or 0)
    if close > float(level_high):
        return "BREAKOUT"
    if close < float(level_low):
        return "BREAKDOWN"
    return "INSIDE_RANGE"

