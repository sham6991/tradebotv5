from __future__ import annotations

from typing import Any

import pandas as pd


def _series(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if frame is None:
        return pd.Series(dtype="float64")
    source = column
    if source not in frame:
        lower_map = {str(name).lower(): name for name in frame.columns}
        source = lower_map.get(column.lower(), column)
    if source not in frame:
        return pd.Series([default] * (0 if frame is None else len(frame)), dtype="float64")
    return pd.to_numeric(frame[source], errors="coerce").fillna(default)


def _datetime_series(frame: pd.DataFrame) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    lower_map = {str(name).lower(): name for name in frame.columns}
    source = lower_map.get("datetime") or lower_map.get("date") or lower_map.get("timestamp")
    if not source:
        if isinstance(frame.index, pd.DatetimeIndex):
            return pd.Series(frame.index, index=frame.index)
        return None
    values = pd.to_datetime(frame[source], errors="coerce")
    if values.notna().any():
        return values
    return None


def ema(values: pd.Series, period: int) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").ewm(span=int(period), adjust=False).mean()


def vwap(frame: pd.DataFrame) -> pd.Series:
    close = _series(frame, "close")
    high = _series(frame, "high")
    low = _series(frame, "low")
    volume = _series(frame, "volume", 0.0)
    typical = (high + low + close) / 3.0

    def session_vwap(indexes: pd.Index) -> pd.Series:
        session_volume = volume.loc[indexes]
        session_typical = typical.loc[indexes]
        session_close = close.loc[indexes]
        cumulative_volume = session_volume.cumsum().mask(lambda values: values == 0)
        values = (session_typical * session_volume).cumsum() / cumulative_volume
        return values.ffill().fillna(session_close)

    datetimes = _datetime_series(frame)
    if datetimes is None:
        return session_vwap(frame.index)

    result = pd.Series(index=frame.index, dtype="float64")
    for _date, indexes in datetimes.groupby(datetimes.dt.date).groups.items():
        result.loc[indexes] = session_vwap(pd.Index(indexes))
    return result.fillna(close)


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    close = pd.to_numeric(close, errors="coerce").ffill()
    delta = close.diff()
    gain = delta.clip(lower=0).fillna(0.0)
    loss = (-delta.clip(upper=0)).fillna(0.0)
    period = int(period)
    avg_gain = pd.Series(index=close.index, dtype="float64")
    avg_loss = pd.Series(index=close.index, dtype="float64")
    if len(close) > period:
        avg_gain.iloc[period] = gain.iloc[1 : period + 1].mean()
        avg_loss.iloc[period] = loss.iloc[1 : period + 1].mean()
        for index in range(period + 1, len(close)):
            avg_gain.iloc[index] = (avg_gain.iloc[index - 1] * (period - 1) + gain.iloc[index]) / period
            avg_loss.iloc[index] = (avg_loss.iloc[index - 1] * (period - 1) + loss.iloc[index]) / period
    rsi = pd.Series(50.0, index=close.index, dtype="float64")
    for index in range(len(close)):
        ag = avg_gain.iloc[index]
        al = avg_loss.iloc[index]
        if pd.isna(ag) or pd.isna(al):
            continue
        if al == 0 and ag > 0:
            rsi.iloc[index] = 100.0
        elif ag == 0 and al > 0:
            rsi.iloc[index] = 0.0
        elif ag == 0 and al == 0:
            rsi.iloc[index] = 50.0
        else:
            rs = ag / al
            rsi.iloc[index] = 100 - (100 / (1 + rs))
    return rsi


def true_range(frame: pd.DataFrame) -> pd.Series:
    high = _series(frame, "high")
    low = _series(frame, "low")
    close = _series(frame, "close")
    previous_close = close.shift(1)
    return pd.concat(
        [
            (high - low).abs(),
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1).fillna((high - low).abs())


def wilder_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(frame)
    period = int(period)
    if len(tr) < period:
        return tr.expanding(min_periods=1).mean().fillna(0.0)
    atr = pd.Series(index=tr.index, dtype="float64")
    atr.iloc[: period - 1] = tr.iloc[: period - 1].expanding(min_periods=1).mean()
    atr.iloc[period - 1] = tr.iloc[:period].mean()
    for index in range(period, len(tr)):
        atr.iloc[index] = (atr.iloc[index - 1] * (period - 1) + tr.iloc[index]) / period
    return atr.fillna(0.0)


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
    candle_range = (high - low).abs().astype("float64").mask(lambda values: values == 0)
    body = (close - open_).abs()
    upper_wick = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low
    return pd.DataFrame({
        "body_pct": pd.to_numeric(body / candle_range * 100, errors="coerce").fillna(0.0).astype("float64"),
        "upper_wick_pct": pd.to_numeric(upper_wick / candle_range * 100, errors="coerce").fillna(0.0).astype("float64"),
        "lower_wick_pct": pd.to_numeric(lower_wick / candle_range * 100, errors="coerce").fillna(0.0).astype("float64"),
    })


def relative_volume(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    volume = _series(frame, "volume", 0.0).astype("float64")
    average = volume.rolling(int(period), min_periods=1).mean().mask(lambda values: values == 0)
    result = volume / average
    return pd.to_numeric(result, errors="coerce").fillna(0.0).astype("float64")


def bid_ask_spread_pct(bid: Any, ask: Any, ltp: Any = None) -> float:
    try:
        bid_value = float(bid)
        ask_value = float(ask)
    except (TypeError, ValueError):
        return 100.0
    mid = (bid_value + ask_value) / 2.0
    if bid_value <= 0 or ask_value <= 0 or ask_value < bid_value or mid <= 0:
        return 100.0
    return round((ask_value - bid_value) / mid * 100, 4)


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
