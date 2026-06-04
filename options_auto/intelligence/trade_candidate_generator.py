from __future__ import annotations

from typing import Any


def nearby_option_instruments(instruments: list[dict[str, Any]], spot: float, strike_window: int = 6) -> list[dict[str, Any]]:
    if not instruments:
        return []
    parsed = []
    for item in instruments:
        try:
            strike = float(item.get("strike"))
        except (TypeError, ValueError):
            continue
        parsed.append((abs(strike - float(spot)), item))
    parsed.sort(key=lambda value: value[0])
    return [item for _distance, item in parsed[: max(1, int(strike_window) * 2)]]

