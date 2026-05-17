# ==================================================
# MARKET TREND
# ==================================================

RSI_EARLY_BULL_REMARK = "RSI based early Bull entry"
RSI_EARLY_BEAR_REMARK = "RSI based early bear entry"

DEFAULT_OPTION_SCORING_SETTINGS = {
    "watch_buy_score": 60,
    "min_buy_score": 75,
    "strong_buy_score": 80,
    "min_volume_ratio": 1.2,
    "min_option_volume": 0,
    "aggression_score_cap": 55,
    "compression_range_ratio": 0.7,
    "expansion_range_ratio": 1.8,
    "max_chase_range_ratio": 2.5,
    "failed_breakout_penalty": -15,
    "early_breakout_min_score": 60,
}

BUY_SCORE_REPORT_COLUMNS = (
    "NIFTY Trend",
    "Trend Alignment",
    "Candle Body",
    "Candle Range",
    "Average Range",
    "Range Ratio",
    "Close Position Score",
    "Volume Ratio",
    "Bullish Close Score",
    "Volume Strength Score",
    "Candle Body Strength Score",
    "Breakout Score",
    "Higher Low Score",
    "Compression Score",
    "Expansion Score",
    "Aggression Score",
    "Aggression Score Calculation",
    "Capped Aggression Score",
    "Capped Aggression Score Calculation",
    "Failed Breakout Penalty",
    "Bull Trap penalty",
    "Bear Trap Penalty",
    "Buy Score",
    "Buy Score Calculation",
    "Buy Setup",
    "Buy Entry",
    "Entry Filters Passed",
    "Entry Block Reason",
    "Liquidity Filter",
    "Chase Filter",
    "Momentum Acceleration Score",
    "Upper Wick Shrink Score",
    "Early Breakout Probability Score",
    "Early Breakout Probability Calculation",
    "High Probability Buy",
    "High Probability Buy Calculation",
)


def option_scoring_settings(settings=None):
    values = dict(DEFAULT_OPTION_SCORING_SETTINGS)
    if isinstance(settings, dict):
        values.update({key: settings.get(key, value) for key, value in values.items()})
        if "min_buy_score" not in settings and "entry_buy_score" in settings:
            values["min_buy_score"] = settings["entry_buy_score"]
    return {key: _float_setting(values, key, default) for key, default in DEFAULT_OPTION_SCORING_SETTINGS.items()}


def _float_setting(values, key, default):
    try:
        return float(values.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def option_score_calculation_details(score_row, scoring_settings=None):
    score_settings = option_scoring_settings(scoring_settings)

    def value(key):
        try:
            import pandas as pd

            parsed = pd.to_numeric(score_row.get(key, 0), errors="coerce")
            return 0.0 if pd.isna(parsed) else float(parsed)
        except (TypeError, ValueError):
            return 0.0

    def fmt(number):
        number = float(number)
        return str(int(number)) if number.is_integer() else f"{number:.2f}"

    bullish_close = value("Bullish Close Score")
    volume_strength = value("Volume Strength Score")
    body_strength = value("Candle Body Strength Score")
    breakout = value("Breakout Score")
    expansion = value("Expansion Score")
    aggression = value("Aggression Score")
    cap = score_settings["aggression_score_cap"]
    capped_aggression = value("Capped Aggression Score")
    higher_low = value("Higher Low Score")
    compression = value("Compression Score")
    failed_breakout = value("Failed Breakout Penalty")
    bear_trap = value("Bear Trap Penalty")
    bull_trap = value("Bull Trap penalty")
    buy_score = value("Buy Score")
    upper_wick_shrink = value("Upper Wick Shrink Score")
    early_breakout = value("Early Breakout Probability Score")
    momentum = value("Momentum Acceleration Score")

    if cap > 0:
        cap_text = f"min(Aggression {fmt(aggression)}, Cap {fmt(cap)}) = {fmt(capped_aggression)}"
    else:
        cap_text = f"Aggression {fmt(aggression)}; cap disabled = {fmt(capped_aggression)}"

    return {
        "Aggression Score Calculation": (
            f"Bullish Close {fmt(bullish_close)} + Volume Strength {fmt(volume_strength)} "
            f"+ Candle Body Strength {fmt(body_strength)} + Breakout {fmt(breakout)} "
            f"+ Expansion {fmt(expansion)} = {fmt(aggression)}"
        ),
        "Capped Aggression Score Calculation": cap_text,
        "Buy Score Calculation": (
            f"Capped Aggression {fmt(capped_aggression)} + Higher Low {fmt(higher_low)} "
            f"+ Compression {fmt(compression)} + Failed Breakout {fmt(failed_breakout)} "
            f"+ Bear Trap {fmt(bear_trap)} + Bull Trap {fmt(bull_trap)} = {fmt(buy_score)}"
        ),
        "Early Breakout Probability Calculation": (
            f"Compression {fmt(compression)} + Volume Strength {fmt(volume_strength)} "
            f"+ Higher Low {fmt(higher_low)} + Bullish Close {fmt(bullish_close)} "
            f"+ Upper Wick Shrink {fmt(upper_wick_shrink)} = {fmt(early_breakout)}"
        ),
        "High Probability Buy Calculation": (
            f"Buy Score {fmt(buy_score)} >= Strong Buy {fmt(score_settings['strong_buy_score'])}; "
            f"Early Breakout {fmt(early_breakout)} >= Min {fmt(score_settings['early_breakout_min_score'])}; "
            f"Momentum {fmt(momentum)} > 0"
        ),
    }


def market_trend_signal(
    nifty_row,
    bullish_threshold: float = 5,
    bearish_threshold: float = -5,
    rsi_bull: float = 55,
    rsi_bear: float = 45,
    rsi_reversal_bullish: float = 70,
    rsi_reversal_bearish: float = 20,
):

    import pandas as pd

    ema20 = pd.to_numeric(nifty_row.get("EMA20", 0), errors="coerce")
    ema50 = pd.to_numeric(nifty_row.get("EMA50", 0), errors="coerce")
    rsi_value = pd.to_numeric(nifty_row.get("RSI", 0), errors="coerce")
    ema_diff = 0 if pd.isna(ema20) or pd.isna(ema50) else float(ema20) - float(ema50)
    rsi = 0 if pd.isna(rsi_value) else float(rsi_value)

    if rsi > rsi_reversal_bullish:
        return "BULLISH", RSI_EARLY_BULL_REMARK

    if rsi < rsi_reversal_bearish:
        return "BEARISH", RSI_EARLY_BEAR_REMARK

    if ema_diff > bullish_threshold and rsi > rsi_bull:
        return "BULLISH", ""

    if ema_diff < bearish_threshold and rsi < rsi_bear:
        return "BEARISH", ""

    return "SIDEWAYS", ""


def market_trend(
    nifty_row,
    bullish_threshold: float = 5,
    bearish_threshold: float = -5,
    rsi_bull: float = 55,
    rsi_bear: float = 45,
    rsi_reversal_bullish: float = 70,
    rsi_reversal_bearish: float = 20,
):

    trend, _remark = market_trend_signal(
        nifty_row,
        bullish_threshold,
        bearish_threshold,
        rsi_bull,
        rsi_bear,
        rsi_reversal_bullish,
        rsi_reversal_bearish,
    )
    return trend


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

def ensure_option_formula_columns(df, scoring_settings=None):
    df = df.copy()
    score_settings = option_scoring_settings(scoring_settings)

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
    df["Average Range"] = avg_range
    df["Range Ratio"] = (df["Candle Range"] / avg_range.replace(0, float("nan"))).fillna(0)
    df["Compression Score"] = (
        df["Range Ratio"] < score_settings["compression_range_ratio"]
    ).astype(int) * 15
    df["Compression Score"] = df["Compression Score"].where(avg_range.notna(), 0)
    df["Expansion Score"] = (
        df["Range Ratio"] > score_settings["expansion_range_ratio"]
    ).astype(int) * 25
    df["Expansion Score"] = df["Expansion Score"].where(avg_range.notna(), 0)

    df["Bull Trap penalty"] = (
        (df["Upper Wick"] > df["Candle Body"]) & (df["close"] < df["open"])
    ).astype(int) * -25
    df["Bear Trap Penalty"] = (
        (df["Lower Wick"] > df["Candle Body"]) & (df["close"] > df["open"])
    ).astype(int) * -25

    failed_breakout = (
        (df["high"] > df["high"].shift(1))
        & (df["Close Position Score"] < 0.5)
    )
    recent_failed_breakout = (
        failed_breakout.astype(int).shift(1)
        .rolling(3, min_periods=1)
        .max()
        .fillna(0)
        .astype(bool)
    )
    df["Failed Breakout Penalty"] = recent_failed_breakout.astype(int) * score_settings["failed_breakout_penalty"]
    df["Aggression Score"] = (
        df["Bullish Close Score"]
        + df["Volume Strength Score"]
        + df["Candle Body Strength Score"]
        + df["Breakout Score"]
        + df["Expansion Score"]
    )
    if score_settings["aggression_score_cap"] > 0:
        df["Capped Aggression Score"] = df["Aggression Score"].clip(upper=score_settings["aggression_score_cap"])
    else:
        df["Capped Aggression Score"] = df["Aggression Score"]

    df["Buy Score"] = (
        df["Capped Aggression Score"]
        + df["Higher Low Score"]
        + df["Compression Score"]
        + df["Failed Breakout Penalty"]
        + df["Bear Trap Penalty"]
        + df["Bull Trap penalty"]
    )
    df["Buy Setup"] = df["Buy Score"].apply(lambda x: "WATCH" if x >= score_settings["watch_buy_score"] else "")
    df["Buy Entry"] = df["Buy Score"].apply(lambda x: "BUY" if x >= score_settings["min_buy_score"] else "")
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
    df["Upper Wick Shrink Score"] = (df["Upper Wick"] < df["Upper Wick"].shift(1)).astype(int) * 10

    df["Early Breakout Probability Score"] = (
        df["Compression Score"]
        + df["Volume Strength Score"]
        + df["Higher Low Score"]
        + df["Bullish Close Score"]
        + df["Upper Wick Shrink Score"]
    )

    df["High Probability Buy"] = (
        (df["Buy Score"] >= score_settings["strong_buy_score"])
        & (df["Early Breakout Probability Score"] >= score_settings["early_breakout_min_score"])
        & (df["Momentum Acceleration Score"] > 0)
    ).map({True: "HIGH PROB BUY", False: ""})

    liquidity_ok = (
        (df["Volume Ratio"] >= score_settings["min_volume_ratio"])
        & (df["volume"] >= score_settings["min_option_volume"])
    )
    follow_through = (
        (df["Momentum Acceleration Score"] > 0)
        & (df["Breakout Score"] > 0)
        & (df["Close Position Score"] > 0.8)
        & (df["Volume Ratio"] >= score_settings["min_volume_ratio"])
    )
    range_ready = (df["Average Range"].isna()) | (df["Range Ratio"] <= score_settings["max_chase_range_ratio"])
    chase_ok = range_ready | follow_through
    df["Liquidity Filter"] = liquidity_ok.map({True: "PASS", False: "FAIL"})
    df["Chase Filter"] = chase_ok.map({True: "PASS", False: "FAIL"})
    df["Entry Filters Passed"] = (liquidity_ok & chase_ok).map({True: "YES", False: "NO"})
    df["Entry Block Reason"] = ""
    df.attrs["_option_scoring_settings"] = score_settings

    return df


OPTION_FORMULA_COLUMNS = {
    "Candle Body",
    "Candle Range",
    "Average Range",
    "Range Ratio",
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
    "Aggression Score",
    "Capped Aggression Score",
    "Failed Breakout Penalty",
    "Bull Trap penalty",
    "Bear Trap Penalty",
    "Buy Score",
    "Buy Setup",
    "Buy Entry",
    "Sell Score",
    "Sell Entry",
    "Momentum Acceleration Score",
    "Upper Wick Shrink Score",
    "Early Breakout Probability Score",
    "High Probability Buy",
    "Entry Filters Passed",
    "Entry Block Reason",
    "Liquidity Filter",
    "Chase Filter",
}


def has_option_formula_columns(df):
    return OPTION_FORMULA_COLUMNS.issubset(set(df.columns))


def append_option_formula_row(df, scoring_settings=None):
    if df is None or df.empty:
        return ensure_option_formula_columns(df, scoring_settings)
    needed = {"open", "high", "low", "close"}
    if not needed.issubset(df.columns):
        return df

    attrs = dict(df.attrs)
    scored = ensure_option_formula_columns(df, scoring_settings)
    scored.attrs.update(attrs)
    return scored


def calculate_scores(df, i, min_buy_score: float = 60, scoring_settings=None):
    if i == 0:
        return {
            "Buy Score": 0,
            "Buy Setup": "",
            "Buy Entry": "",
            "Entry Filters Passed": "NO",
            "Entry Block Reason": "first_candle",
        }

    if not has_option_formula_columns(df):
        df = ensure_option_formula_columns(df, scoring_settings)
    row = df.iloc[i]
    buy_score = float(row.get("Buy Score", 0) or 0)
    score_settings = option_scoring_settings(scoring_settings)
    threshold = float(min_buy_score or score_settings["min_buy_score"])
    block_reasons = []
    if buy_score < threshold:
        block_reasons.append(f"buy_score_below_{threshold:g}")
    if row.get("Liquidity Filter", "PASS") != "PASS":
        block_reasons.append("liquidity_filter_failed")
    if row.get("Chase Filter", "PASS") != "PASS":
        block_reasons.append("chase_filter_failed")
    buy_entry = "BUY" if not block_reasons else ""
    buy_setup = row.get("Buy Setup", "")
    entry_filters_passed = "YES" if not block_reasons else "NO"

    return {
        "Buy Score": buy_score,
        "Buy Setup": buy_setup,
        "Buy Entry": buy_entry,
        "Entry Filters Passed": entry_filters_passed,
        "Entry Block Reason": "; ".join(block_reasons),
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
    rsi_reversal_bullish: float = 70,
    rsi_reversal_bearish: float = 20,
    data_kind="nifty",
    min_buy_score: float = 60,
    scoring_settings=None,
    include_calculations=False,
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
        trend = market_trend(
            row,
            bullish_threshold,
            bearish_threshold,
            rsi_bull,
            rsi_bear,
            rsi_reversal_bullish,
            rsi_reversal_bearish,
        )
        return {
            "Trend": trend,
            "EMA20": row.get("EMA20", 0),
            "EMA50": row.get("EMA50", 0),
            "Crossover": float(row.get("EMA20", 0) or 0) - float(row.get("EMA50", 0) or 0),
            "RSI": row.get("RSI", 0),
        }
    else:
        # For options, only Buy Score for entry
        expected_settings = option_scoring_settings(scoring_settings)
        if has_option_formula_columns(df) and df.attrs.get("_option_scoring_settings") == expected_settings:
            scored = df
        else:
            scored = ensure_option_formula_columns(df, scoring_settings)
        scores = calculate_scores(scored, i, min_buy_score, scoring_settings)
        row = scored.iloc[i]
        result = {
            "Average Range": row.get("Average Range", ""),
            "Range Ratio": row.get("Range Ratio", ""),
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
            "Aggression Score": row.get("Aggression Score", ""),
            "Capped Aggression Score": row.get("Capped Aggression Score", ""),
            "Failed Breakout Penalty": row.get("Failed Breakout Penalty", ""),
            "Bull Trap penalty": row.get("Bull Trap penalty", ""),
            "Bear Trap Penalty": row.get("Bear Trap Penalty", ""),
            "Buy Score": scores["Buy Score"],
            "Buy Setup": scores["Buy Setup"],
            "Buy Entry": scores["Buy Entry"],
            "Entry Filters Passed": scores["Entry Filters Passed"],
            "Entry Block Reason": scores["Entry Block Reason"],
            "Liquidity Filter": row.get("Liquidity Filter", ""),
            "Chase Filter": row.get("Chase Filter", ""),
            "Sell Score": row.get("Sell Score", ""),
            "Sell Entry": row.get("Sell Entry", ""),
            "Momentum Acceleration Score": row.get("Momentum Acceleration Score", ""),
            "Upper Wick Shrink Score": row.get("Upper Wick Shrink Score", ""),
            "Early Breakout Probability Score": row.get("Early Breakout Probability Score", ""),
            "High Probability Buy": row.get("High Probability Buy", ""),
        }
        if include_calculations:
            result.update(option_score_calculation_details(result, scoring_settings))
        return result
