from __future__ import annotations

FORMULA_VERSION = "intraday-formulas-v2-wilder-rsi-atr-vwap-profile"

FORMULAS = {
    "EMA": "EMA_today = close * (2 / (N + 1)) + EMA_yesterday * (1 - (2 / (N + 1)))",
    "RSI": "Wilder RSI with Wilder-smoothed average gain/loss",
    "VWAP": "cumulative(((high + low + close) / 3) * volume) / cumulative(volume), reset per session input",
    "RVOL": "current_volume / average_volume_of_previous_N_candles",
    "VOLUME_PROFILE": "range volume allocated across price bins; 70 percent value area expands around POC",
    "ATR": "Wilder-smoothed true range",
}


def formula_metadata() -> dict:
    return {"formula_version": FORMULA_VERSION, "formulas": dict(FORMULAS)}
