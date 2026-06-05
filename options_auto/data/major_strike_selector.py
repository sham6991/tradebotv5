from __future__ import annotations

import math
from typing import Any


def floor_to_major_strike(spot: float, major_step: int) -> int:
    step = _positive_step(major_step)
    return int(math.floor(float(spot) / step) * step)


def ceil_to_major_strike(spot: float, major_step: int) -> int:
    step = _positive_step(major_step)
    return int(math.ceil(float(spot) / step) * step)


def select_major_strikes_for_spot(spot: float, major_step: int) -> dict[str, Any]:
    step = _positive_step(major_step)
    spot_value = float(spot)
    floor_major = floor_to_major_strike(spot_value, step)
    ceil_major = ceil_to_major_strike(spot_value, step)
    if floor_major == ceil_major:
        ce_strike = ceil_major + step
        pe_strike = floor_major - step
        reason = "Major-strike selection enabled; spot is exactly on a major strike so CE/PE were moved one major step apart."
    else:
        ce_strike = ceil_major
        pe_strike = floor_major
        reason = "Major-strike selection enabled; selected nearest upper major CE and nearest lower major PE."
    if ce_strike == pe_strike:
        ce_strike += step
    return {
        "spot": spot_value,
        "major_step": step,
        "floor_major": floor_major,
        "ceil_major": ceil_major,
        "ce_strike": int(ce_strike),
        "pe_strike": int(pe_strike),
        "reason": reason,
    }


def _positive_step(value: int | float | str) -> int:
    try:
        step = int(float(value))
    except (TypeError, ValueError):
        step = 100
    if step <= 0:
        raise ValueError("Major strike step must be greater than zero.")
    return step
