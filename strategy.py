# ==================================================
# MARKET TREND
# ==================================================

def market_trend(nifty_row, bullish_threshold=5, bearish_threshold=-5, rsi_bull=55, rsi_bear=45):

    ema_diff = nifty_row["EMA20"] - nifty_row["EMA50"]
    rsi = float(nifty_row.get("RSI", 0) or 0)

    if ema_diff > bullish_threshold and rsi > rsi_bull:
        return "BULLISH"

    elif ema_diff < bearish_threshold and rsi < rsi_bear:
        return "BEARISH"

    return "SIDEWAYS"


# ==================================================
# SCORE SYSTEM
# ==================================================

def calculate_score(df, i):

    if i == 0:
        return 0

    row = df.iloc[i]
    prev = df.iloc[i - 1]

    score = 0

    # EMA TREND
    if row["EMA20"] > row["EMA50"]:
        score += 2

    if row["EMA20"] < row["EMA50"]:
        score += 2

    # RSI
    if 58 <= row["RSI"] <= 75:
        score += 1

    if 25 <= row["RSI"] <= 42:
        score += 1

    # BREAKOUT
    if row["close"] > prev["high"]:
        score += 1

    if row["close"] < prev["low"]:
        score += 1

    # VOLUME
    if row["volume"] > prev["volume"]:
        score += 1

    return score


# ==================================================
# CALL MOMENTUM
# ==================================================

def call_momentum(df, i):

    if i == 0:
        return False

    row = df.iloc[i]
    prev = df.iloc[i - 1]

    return (

        row["EMA20"] > row["EMA50"]

        and row["RSI"] > 58

        and row["close"] > row["EMA20"]

        and row["volume"] > prev["volume"]
    )


# ==================================================
# PUT MOMENTUM
# ==================================================

def put_momentum(df, i):

    if i == 0:
        return False

    row = df.iloc[i]
    prev = df.iloc[i - 1]

    return (

        row["EMA20"] < row["EMA50"]

        and row["RSI"] < 42

        and row["close"] < row["EMA20"]

        and row["volume"] > prev["volume"]
    )


# ==================================================
# NEW SCORING SYSTEM
# ==================================================

def ensure_option_formula_columns(df):
    df = df.copy()

    for canonical, variants in {
        "open": ("Open",),
        "high": ("High",),
        "low": ("Low",),
        "close": ("Close",),
        "volume": ("Volume",),
    }.items():
        if canonical not in df.columns:
            for variant in variants:
                if variant in df.columns:
                    df[canonical] = df[variant]
                    break

    needed = {"open", "high", "low", "close"}
    if not needed.issubset(df.columns):
        return df

    import pandas as pd

    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0
    df["volume"] = df["volume"].fillna(0)

    df["Candle Body"] = (df["close"] - df["open"]).abs()
    df["Candle Range"] = (df["high"] - df["low"]).replace(0, float("nan"))
    df["Upper Wick"] = df["high"] - df[["open", "close"]].max(axis=1)
    df["Lower Wick"] = df[["open", "close"]].min(axis=1) - df["low"]
    df["Close Position Score"] = ((df["close"] - df["low"]) / df["Candle Range"]).fillna(0.5)

    avg_volume = df["volume"].expanding().mean().replace(0, float("nan"))
    df["Volume Ratio"] = (df["volume"] / avg_volume).fillna(1.0)

    df["Bullish Close Score"] = df["Close Position Score"].apply(
        lambda x: 20 if x > 0.8 else (10 if x > 0.6 else (5 if x > 0.5 else 0))
    )
    df["Bearish Close Score"] = df["Close Position Score"].apply(
        lambda x: 20 if x < 0.2 else (10 if x < 0.4 else (5 if x < 0.5 else 0))
    )
    df["Volume Strength Score"] = df["Volume Ratio"].apply(
        lambda x: 30 if x > 3 else (20 if x > 2 else (10 if x > 1.5 else 0))
    )

    body_ratio = (df["Candle Body"] / df["Candle Range"]).fillna(0)
    df["Candle Body Strength Score"] = body_ratio.apply(lambda x: 20 if x > 0.7 else (10 if x > 0.5 else 0))
    df["Breakout Score"] = (df["high"] > df["high"].shift(1)).astype(int) * 20
    df["Breakdown Score"] = (df["low"] < df["low"].shift(1)).astype(int) * 20
    df["Higher Low Score"] = (df["low"] > df["low"].shift(1)).astype(int) * 15
    df["Lower High Score"] = (df["high"] < df["high"].shift(1)).astype(int) * 15

    avg_range = df["Candle Range"].shift(1).rolling(5).mean()
    df["Compression Score"] = (df["Candle Range"] < avg_range * 0.7).astype(int) * 15
    df["Expansion Score"] = (df["Candle Range"] > avg_range * 1.8).astype(int) * 25

    df["Bull Trap penalty"] = (
        (df["Upper Wick"] > df["Candle Body"]) & (df["close"] < df["open"])
    ).astype(int) * -25
    df["Bear Trap Penalty"] = (
        (df["Lower Wick"] > df["Candle Body"]) & (df["close"] > df["open"])
    ).astype(int) * -25

    df["Buy Score"] = (
        df["Bullish Close Score"]
        + df["Volume Strength Score"]
        + df["Candle Body Strength Score"]
        + df["Breakout Score"]
        + df["Higher Low Score"]
        + df["Compression Score"]
        + df["Expansion Score"]
        + df["Bear Trap Penalty"]
        + df["Bull Trap penalty"]
    )
    df["Buy Entry"] = df["Buy Score"].apply(
        lambda x: "BUY" if x > 80 else ("WATCH" if x > 60 else "")
    )
    df["Sell Score"] = (
        df["Bearish Close Score"]
        + df["Volume Strength Score"]
        + df["Candle Body Strength Score"]
        + df["Breakdown Score"]
        + df["Lower High Score"]
        + df["Compression Score"]
        + df["Expansion Score"]
        + df["Bull Trap penalty"]
        + df["Bear Trap Penalty"]
    )
    df["Sell Entry"] = df["Sell Score"].apply(
        lambda x: "SELL" if x > 80 else ("WATCH" if x > 60 else "")
    )

    previous_close = df["close"].shift(1).replace(0, float("nan"))
    df["Momentum Acceleration Score"] = (((df["close"] - df["close"].shift(1)) / previous_close) * 100 * df["Volume Ratio"]).fillna(0)

    df["Early Breakout Probability Score"] = (
        df["Compression Score"]
        + df["Volume Strength Score"]
        + df["Higher Low Score"]
        + df["Bullish Close Score"]
        + (df["Upper Wick"] < df["Upper Wick"].shift(1)).astype(int) * 10
    )

    df["High Probability Buy"] = (
        (df["Buy Score"] > 80)
        & (df["Early Breakout Probability Score"] > 60)
        & (df["Momentum Acceleration Score"] > 0)
    ).map({True: "HIGH PROB BUY", False: ""})

    return df


OPTION_FORMULA_COLUMNS = {
    "Candle Body",
    "Candle Range",
    "Close Position Score",
    "Volume Ratio",
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
    "Buy Entry",
    "Sell Score",
    "Sell Entry",
    "Momentum Acceleration Score",
    "Early Breakout Probability Score",
    "High Probability Buy",
}


def has_option_formula_columns(df):
    return OPTION_FORMULA_COLUMNS.issubset(set(df.columns))


def append_option_formula_row(df):
    if df is None or df.empty:
        return ensure_option_formula_columns(df)
    needed = {"open", "high", "low", "close"}
    if not needed.issubset(df.columns):
        return df

    import pandas as pd

    df = df.copy(deep=False)
    attrs = dict(df.attrs)
    index = len(df) - 1
    if index < 0:
        return ensure_option_formula_columns(df)

    if "volume" not in df.columns:
        df["volume"] = 0
    try:
        open_price = float(df.at[index, "open"])
        high = float(df.at[index, "high"])
        low = float(df.at[index, "low"])
        close = float(df.at[index, "close"])
        volume = float(df.at[index, "volume"] or 0)
    except (TypeError, ValueError):
        return df

    candle_body = abs(close - open_price)
    candle_range = high - low
    range_for_ratio = candle_range if candle_range != 0 else float("nan")
    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    close_position_score = ((close - low) / range_for_ratio) if range_for_ratio == range_for_ratio else 0.5

    previous_volume_sum = attrs.get("_volume_sum")
    if previous_volume_sum is None:
        previous_volume_sum = float(pd.to_numeric(df["volume"].iloc[:-1], errors="coerce").fillna(0).sum())
    total_volume = previous_volume_sum + volume
    attrs["_volume_sum"] = total_volume
    average_volume = total_volume / len(df) if len(df) else 0
    volume_ratio = (volume / average_volume) if average_volume else 1.0

    previous = df.iloc[index - 1] if index > 0 else None
    previous_high = float(previous.get("high", 0) or 0) if previous is not None else None
    previous_low = float(previous.get("low", 0) or 0) if previous is not None else None
    previous_close = float(previous.get("close", 0) or 0) if previous is not None else None
    previous_upper_wick = float(previous.get("Upper Wick", 0) or 0) if previous is not None else None

    range_window = attrs.get("_range_window")
    if range_window is None:
        if "Candle Range" in df.columns:
            range_window = [
                float(value)
                for value in pd.to_numeric(
                    df["Candle Range"].iloc[max(0, index - 5):index],
                    errors="coerce",
                ).dropna()
            ]
        else:
            range_window = []
    avg_range = (sum(range_window[-5:]) / 5) if len(range_window) >= 5 else float("nan")
    body_ratio = (candle_body / candle_range) if candle_range else 0

    bullish_close_score = 20 if close_position_score > 0.8 else (10 if close_position_score > 0.6 else (5 if close_position_score > 0.5 else 0))
    bearish_close_score = 20 if close_position_score < 0.2 else (10 if close_position_score < 0.4 else (5 if close_position_score < 0.5 else 0))
    volume_strength_score = 30 if volume_ratio > 3 else (20 if volume_ratio > 2 else (10 if volume_ratio > 1.5 else 0))
    candle_body_strength_score = 20 if body_ratio > 0.7 else (10 if body_ratio > 0.5 else 0)
    breakout_score = 20 if previous_high is not None and high > previous_high else 0
    breakdown_score = 20 if previous_low is not None and low < previous_low else 0
    higher_low_score = 15 if previous_low is not None and low > previous_low else 0
    lower_high_score = 15 if previous_high is not None and high < previous_high else 0
    compression_score = 15 if avg_range == avg_range and candle_range < avg_range * 0.7 else 0
    expansion_score = 25 if avg_range == avg_range and candle_range > avg_range * 1.8 else 0
    bull_trap_penalty = -25 if upper_wick > candle_body and close < open_price else 0
    bear_trap_penalty = -25 if lower_wick > candle_body and close > open_price else 0

    buy_score = (
        bullish_close_score
        + volume_strength_score
        + candle_body_strength_score
        + breakout_score
        + higher_low_score
        + compression_score
        + expansion_score
        + bear_trap_penalty
        + bull_trap_penalty
    )
    sell_score = (
        bearish_close_score
        + volume_strength_score
        + candle_body_strength_score
        + breakdown_score
        + lower_high_score
        + compression_score
        + expansion_score
        + bull_trap_penalty
        + bear_trap_penalty
    )
    momentum = (((close - previous_close) / previous_close) * 100 * volume_ratio) if previous_close else 0
    early_breakout_probability = (
        compression_score
        + volume_strength_score
        + higher_low_score
        + bullish_close_score
        + (10 if previous_upper_wick is not None and upper_wick < previous_upper_wick else 0)
    )

    values = {
        "Candle Body": candle_body,
        "Candle Range": candle_range if candle_range else float("nan"),
        "Upper Wick": upper_wick,
        "Lower Wick": lower_wick,
        "Close Position Score": close_position_score,
        "Volume Ratio": volume_ratio,
        "Bullish Close Score": bullish_close_score,
        "Bearish Close Score": bearish_close_score,
        "Volume Strength Score": volume_strength_score,
        "Candle Body Strength Score": candle_body_strength_score,
        "Breakout Score": breakout_score,
        "Breakdown Score": breakdown_score,
        "Higher Low Score": higher_low_score,
        "Lower High Score": lower_high_score,
        "Compression Score": compression_score,
        "Expansion Score": expansion_score,
        "Bull Trap penalty": bull_trap_penalty,
        "Bear Trap Penalty": bear_trap_penalty,
        "Buy Score": buy_score,
        "Buy Entry": "BUY" if buy_score > 80 else ("WATCH" if buy_score > 60 else ""),
        "Sell Score": sell_score,
        "Sell Entry": "SELL" if sell_score > 80 else ("WATCH" if sell_score > 60 else ""),
        "Momentum Acceleration Score": momentum,
        "Early Breakout Probability Score": early_breakout_probability,
        "High Probability Buy": "HIGH PROB BUY" if buy_score > 80 and early_breakout_probability > 60 and momentum > 0 else "",
    }
    for column, value in values.items():
        df.at[index, column] = value
    if candle_range:
        attrs["_range_window"] = [*range_window, candle_range][-5:]
    else:
        attrs["_range_window"] = range_window[-5:]
    df.attrs.update(attrs)
    return df


def calculate_scores(df, i, min_buy_score=60):
    if i == 0:
        return {
            "Buy Score": 0,
            "Buy Entry": "",
        }

    if not has_option_formula_columns(df):
        df = ensure_option_formula_columns(df)
    row = df.iloc[i]
    buy_score = float(row.get("Buy Score", 0) or 0)
    buy_entry = "BUY" if buy_score >= float(min_buy_score) else ""

    return {
        "Buy Score": buy_score,
        "Buy Entry": buy_entry,
    }


# ==================================================
# BUILD SCORING ROW
# ==================================================

def build_scoring_row(
    df,
    i,
    bullish_threshold: float = 5,
    bearish_threshold: float = -5,
    rsi_bull: float = 55,
    rsi_bear: float = 45,
    data_kind="nifty",
    min_buy_score: float = 60,
):
    if data_kind == "nifty":
        # For Nifty, only trend and indicators
        if i == 0:
            return {
                "Trend": "SIDEWAYS",
                "EMA20": df.iloc[i].get("EMA20", 0),
                "EMA50": df.iloc[i].get("EMA50", 0),
                "Crossover": float(df.iloc[i].get("EMA20", 0) or 0) - float(df.iloc[i].get("EMA50", 0) or 0),
                "RSI": df.iloc[i].get("RSI", 0),
            }
        row = df.iloc[i]
        trend = market_trend(row, bullish_threshold, bearish_threshold, rsi_bull, rsi_bear)
        return {
            "Trend": trend,
            "EMA20": row.get("EMA20", 0),
            "EMA50": row.get("EMA50", 0),
            "Crossover": float(row.get("EMA20", 0) or 0) - float(row.get("EMA50", 0) or 0),
            "RSI": row.get("RSI", 0),
        }
    else:
        # For options, only Buy Score for entry
        scored = df if has_option_formula_columns(df) else ensure_option_formula_columns(df)
        scores = calculate_scores(scored, i, min_buy_score)
        row = scored.iloc[i]
        return {
            "Candle Body": row.get("Candle Body", ""),
            "Candle Range": row.get("Candle Range", ""),
            "Close Position Score": row.get("Close Position Score", ""),
            "Volume Ratio": row.get("Volume Ratio", ""),
            "Bullish Close Score": row.get("Bullish Close Score", ""),
            "Volume Strength Score": row.get("Volume Strength Score", ""),
            "Candle Body Strength Score": row.get("Candle Body Strength Score", ""),
            "Breakout Score": row.get("Breakout Score", ""),
            "Higher Low Score": row.get("Higher Low Score", ""),
            "Compression Score": row.get("Compression Score", ""),
            "Expansion Score": row.get("Expansion Score", ""),
            "Bull Trap penalty": row.get("Bull Trap penalty", ""),
            "Bear Trap Penalty": row.get("Bear Trap Penalty", ""),
            "Buy Score": scores["Buy Score"],
            "Buy Entry": scores["Buy Entry"],
            "Sell Score": row.get("Sell Score", ""),
            "Sell Entry": row.get("Sell Entry", ""),
            "Momentum Acceleration Score": row.get("Momentum Acceleration Score", ""),
            "Early Breakout Probability Score": row.get("Early Breakout Probability Score", ""),
            "High Probability Buy": row.get("High Probability Buy", ""),
        }
