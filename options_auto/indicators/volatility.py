from __future__ import annotations

import pandas as pd

from options_auto.indicators.technicals import wilder_atr


def atr_expansion(frame: pd.DataFrame, period: int = 14, lookback: int = 20) -> float:
    atr = wilder_atr(frame, period)
    if atr.empty:
        return 0.0
    baseline = atr.rolling(int(lookback), min_periods=1).mean().iloc[-1]
    if baseline <= 0:
        return 0.0
    return round(float(atr.iloc[-1] / baseline), 4)


def realized_volatility(close: pd.Series, periods_per_year: int = 252) -> float:
    close = pd.to_numeric(close, errors="coerce").dropna()
    returns = close.pct_change().dropna()
    if returns.empty:
        return 0.0
    return round(float(returns.std(ddof=0) * (periods_per_year ** 0.5) * 100), 4)

