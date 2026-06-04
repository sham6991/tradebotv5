from __future__ import annotations

from typing import Any

import pandas as pd


def _series(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if frame is None or column not in frame:
        return pd.Series([default] * (0 if frame is None else len(frame)), dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def ema(values: pd.Series, period: int) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").ewm(span=int(period), adjust=False).mean()


def vwap(frame: pd.DataFrame) -> pd.Series:
    close = _series(frame, "close")
    high = _series(frame, "high")
    low = _series(frame, "low")
    volume = _series(frame, "volume", 0.0)
    typical = (high + low + close) / 3.0
    cumulative_volume = volume.cumsum().mask(lambda values: values == 0)
    return ((typical * volume).cumsum() / cumulative_volume).ffill().fillna(close)


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    close = pd.to_numeric(close, errors="coerce").ffill()
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / int(period), adjust=False, min_periods=int(period)).mean()
    avg_loss = loss.ewm(alpha=1 / int(period), adjust=False, min_periods=int(period)).mean()
    rs = avg_gain / avg_loss.mask(lambda values: values == 0)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def wilder_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high = _series(frame, "high")
    low = _series(frame, "low")
    close = _series(frame, "close")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / int(period), adjust=False, min_periods=1).mean().fillna(0.0)


def bollinger_bands(close: pd.Series, period: int = 20, stddev: float = 2.0) -> pd.DataFrame:
    close = pd.to_numeric(close, errors="coerce")
    middle = close.rolling(int(period), min_periods=1).mean()
    deviation = close.rolling(int(period), min_periods=1).std(ddof=0).fillna(0.0)
    return pd.DataFrame({
        "bb_middle": middle,
        "bb_upper": middle + deviation * float(stddev),
        "bb_lower": middle - deviation * float(stddev),
    })


def supertrend(frame: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    high = _series(frame, "high")
    low = _series(frame, "low")
    close = _series(frame, "close")
    atr = wilder_atr(frame, period)
    hl2 = (high + low) / 2.0
    upper = hl2 + float(multiplier) * atr
    lower = hl2 - float(multiplier) * atr
    trend = []
    direction = []
    current = 0.0
    bullish = True
    for index in range(len(frame)):
        if index == 0:
            current = lower.iloc[index]
            bullish = close.iloc[index] >= current
        else:
            if close.iloc[index] > upper.iloc[index - 1]:
                bullish = True
            elif close.iloc[index] < lower.iloc[index - 1]:
                bullish = False
            current = max(lower.iloc[index], current) if bullish else min(upper.iloc[index], current)
        trend.append(current)
        direction.append("BULLISH" if bullish else "BEARISH")
    return pd.DataFrame({"supertrend": trend, "supertrend_direction": direction})


def candle_shape(frame: pd.DataFrame) -> pd.DataFrame:
    open_ = _series(frame, "open")
    high = _series(frame, "high")
    low = _series(frame, "low")
    close = _series(frame, "close")
    candle_range = (high - low).abs().replace(0, pd.NA)
    body = (close - open_).abs()
    upper_wick = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low
    return pd.DataFrame({
        "body_pct": (body / candle_range * 100).fillna(0.0),
        "upper_wick_pct": (upper_wick / candle_range * 100).fillna(0.0),
        "lower_wick_pct": (lower_wick / candle_range * 100).fillna(0.0),
    })


def relative_volume(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    volume = _series(frame, "volume", 0.0)
    average = volume.rolling(int(period), min_periods=1).mean().replace(0, pd.NA)
    return (volume / average).fillna(0.0)


def bid_ask_spread_pct(bid: Any, ask: Any, ltp: Any = None) -> float:
    try:
        bid_value = float(bid)
        ask_value = float(ask)
        basis = float(ltp) if ltp not in ("", None) else (bid_value + ask_value) / 2
    except (TypeError, ValueError):
        return 100.0
    if basis <= 0 or ask_value < bid_value:
        return 100.0
    return round((ask_value - bid_value) / basis * 100, 4)


def market_depth_imbalance(bid_qty: Any, ask_qty: Any) -> float:
    try:
        bid_value = float(bid_qty or 0)
        ask_value = float(ask_qty or 0)
    except (TypeError, ValueError):
        return 0.0
    total = bid_value + ask_value
    if total <= 0:
        return 0.0
    return round((bid_value - ask_value) / total * 100, 4)


def enrich_technicals(frame: pd.DataFrame, rsi_period: int = 14, atr_period: int = 14) -> pd.DataFrame:
    frame = frame.copy() if frame is not None else pd.DataFrame()
    if frame.empty:
        return frame
    close = _series(frame, "close")
    frame["ema9"] = ema(close, 9)
    frame["ema20"] = ema(close, 20)
    frame["ema50"] = ema(close, 50)
    frame["vwap"] = vwap(frame)
    frame["rsi14"] = wilder_rsi(close, rsi_period)
    frame["atr14"] = wilder_atr(frame, atr_period)
    bands = bollinger_bands(close)
    for column in bands:
        frame[column] = bands[column]
    shape = candle_shape(frame)
    for column in shape:
        frame[column] = shape[column]
    frame["volume_ma20"] = _series(frame, "volume", 0.0).rolling(20, min_periods=1).mean()
    frame["relative_volume"] = relative_volume(frame)
    frame["intraday_high"] = _series(frame, "high").cummax()
    frame["intraday_low"] = _series(frame, "low").cummin()
    if "oi" in frame:
        frame["oi_change"] = _series(frame, "oi", 0.0).diff().fillna(0.0)
    return frame
