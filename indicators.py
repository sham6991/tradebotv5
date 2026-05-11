import pandas as pd
import re
from datetime import datetime


def parse_market_datetime(values):
    cleaned = (
        values.astype(str)
        .str.strip()
        .str.replace(r"\s*\([^)]*\)$", "", regex=True)
        .str.replace("GMT", "", regex=False)
        .replace({"": None, "nan": None, "NaT": None, "None": None})
    )
    parsed = pd.to_datetime(cleaned, errors="coerce")
    try:
        if getattr(parsed.dt, "tz", None) is not None:
            parsed = parsed.dt.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    return parsed

def clean_and_add_indicators(df):

    df = df.copy()

    # -----------------------------
    # CLEAN COLUMN NAMES
    # -----------------------------
    df.columns = df.columns.str.strip()

    rename_map = {
        "MA â€Œmaâ€Œ (20,ema,0)": "EMA20",
        "MA â€Œmaâ€Œ (50,ema,0)": "EMA50",
        "RSI â€Œrsiâ€Œ (14)": "RSI",
        "Close": "close",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Volume": "volume",
        "Date": "date",
        "Time": "time",
        "Datetime": "datetime",
        "DateTime": "datetime",
        "Timestamp": "datetime",
    }

    df = df.rename(columns=rename_map)

    for col in df.columns:
        lower = col.strip().lower()
        if lower in ("date", "tradingdate"):
            df = df.rename(columns={col: "date"})
        elif lower in ("time", "tradetime"):
            df = df.rename(columns={col: "time"})
        elif lower in ("datetime", "timestamp", "date time"):
            df = df.rename(columns={col: "datetime"})

    # Handle unicode/format variants from exported sheets (eg "MA ‌ma‌ (20,ema,0)")
    dynamic_rename = {}
    for col in df.columns:
        key = "".join(ch for ch in str(col).lower() if ch.isalnum())
        if "rsi" in key and "14" in key and col != "RSI":
            dynamic_rename[col] = "RSI"
        elif "ma" in key and "ema" in key and "20" in key and col != "EMA20":
            dynamic_rename[col] = "EMA20"
        elif "ma" in key and "ema" in key and "50" in key and col != "EMA50":
            dynamic_rename[col] = "EMA50"
    if dynamic_rename:
        df = df.rename(columns=dynamic_rename)

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "% Change",
        "% Change vs Average",
        "Candle Body",
        "Candle Range",
        "Upper Wick",
        "Lower wick",
        "Close Position Score",
        "Volume ratio",
        "Bullish Close Score",
        "Bearish Close Score",
        "Volume Strength Score",
        "Candle Body Strength Score",
        "Breakout Score",
        "Breakdown Score",
        "Higher Low Score",
        "Lower High Score",
        "Compression Score",
        "Expansion Score",
        "Bull Trap penalty",
        "Bear Trap Penalty",
        "Buy Score",
        "Sell Score",
        "Momentum Acceleration Score",
        "Early Breakout Probability Score",
    ]

    for col in numeric_cols:
        if col in df.columns:
            cleaned = (
                df[col]
                .astype(str)
                .str.replace(",", "", regex=False)
                .str.strip()
                .replace({"": None, "nan": None, "None": None})
            )
            df[col] = pd.to_numeric(cleaned, errors="coerce")

    if "date" in df.columns and "time" in df.columns:
        combined = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()
        parsed = parse_market_datetime(combined)
        if "datetime" not in df.columns:
            df["datetime"] = parsed
        else:
            existing = parse_market_datetime(df["datetime"])
            df["datetime"] = existing.fillna(parsed)
    elif "datetime" in df.columns:
        df["datetime"] = parse_market_datetime(df["datetime"])
    elif "date" in df.columns:
        df["datetime"] = parse_market_datetime(df["date"])

    if "datetime" in df.columns and not df["datetime"].isna().all():
        df = df.sort_values("datetime", kind="stable")

    base_needed = {"open", "high", "low", "close"}
    if base_needed.issubset(set(df.columns)):
        df = df.dropna(subset=list(base_needed))
    df = df.reset_index(drop=True)

    # -----------------------------
    # INDICATORS (SAFE CHECK)
    # -----------------------------
    if "close" in df.columns:
        if "EMA20" not in df.columns or df["EMA20"].isna().all():
            df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
        if "EMA50" not in df.columns or df["EMA50"].isna().all():
            df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
        if "RSI" not in df.columns or df["RSI"].isna().all():
            delta = df["close"].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss
            df["RSI"] = 100 - (100 / (1 + rs))

    return df


def append_clean_candle(df, row):
    if df is None or df.empty:
        return clean_and_add_indicators(pd.DataFrame([row]))

    needed = {"datetime", "open", "high", "low", "close", "volume"}
    if not needed.issubset(set(df.columns)):
        return clean_and_add_indicators(pd.concat([df, pd.DataFrame([row])], ignore_index=True))

    df = df.copy(deep=False)
    attrs = dict(df.attrs)
    index = len(df)
    timestamp = _coerce_market_datetime(row.get("datetime"))
    try:
        open_price = float(row.get("open"))
        high = float(row.get("high"))
        low = float(row.get("low"))
        close = float(row.get("close"))
    except (TypeError, ValueError):
        return df
    try:
        volume = float(row.get("volume", 0) or 0)
    except (TypeError, ValueError):
        volume = 0

    df.loc[index, ["datetime", "open", "high", "low", "close", "volume"]] = [
        timestamp,
        open_price,
        high,
        low,
        close,
        volume,
    ]
    _append_ema(df, index, close, "EMA20", 20)
    _append_ema(df, index, close, "EMA50", 50)
    _append_rsi(df, index)
    df.attrs.update(attrs)
    return df


def _coerce_market_datetime(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    try:
        parsed = pd.to_datetime(value, errors="coerce")
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    if getattr(parsed, "tzinfo", None) is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed


def _append_ema(df, index, close, column, span):
    alpha = 2 / (span + 1)
    if column not in df.columns:
        df[column] = pd.NA
    if index == 0 or pd.isna(df.iloc[index - 1].get(column)):
        df.loc[index, column] = close
        return
    previous = float(df.iloc[index - 1][column])
    df.loc[index, column] = (close * alpha) + (previous * (1 - alpha))


def _append_rsi(df, index):
    if "RSI" not in df.columns:
        df["RSI"] = pd.NA
    if index < 14:
        df.loc[index, "RSI"] = pd.NA
        return

    closes = pd.to_numeric(df["close"].iloc[index - 14:index + 1], errors="coerce")
    delta = closes.diff()
    gain = delta.clip(lower=0).iloc[1:].mean()
    loss = (-delta.clip(upper=0)).iloc[1:].mean()
    if pd.isna(gain) or pd.isna(loss):
        df.loc[index, "RSI"] = pd.NA
    elif loss == 0:
        df.loc[index, "RSI"] = 100
    else:
        rs = gain / loss
        df.loc[index, "RSI"] = 100 - (100 / (1 + rs))
