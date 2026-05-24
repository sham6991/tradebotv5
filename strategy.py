from fast_ohlcv_entry import (
    DEFAULT_FAST_OHLCV_SETTINGS,
    FAST_ENTRY_REPORT_COLUMNS,
    apply_fast_ohlcv_columns,
    decide_entry_type,
    fast_ohlcv_settings,
)


RSI_EARLY_BULL_REMARK = "RSI based early Bull entry"
RSI_EARLY_BEAR_REMARK = "RSI based early bear entry"

DEFAULT_OPTION_SCORING_SETTINGS = DEFAULT_FAST_OHLCV_SETTINGS
OPTION_ENTRY_REPORT_COLUMNS = FAST_ENTRY_REPORT_COLUMNS
OPTION_FORMULA_COLUMNS = set(FAST_ENTRY_REPORT_COLUMNS) | {
    "Buy Entry",
    "Sell Score",
    "Sell Entry",
}


def option_scoring_settings(settings=None):
    return fast_ohlcv_settings(settings)


def market_trend_signal(
    nifty_row,
    bullish_threshold: float = 5,
    bearish_threshold: float = -5,
    rsi_bull: float = 55,
    rsi_bear: float = 45,
    rsi_reversal_bullish: float = 70,
    rsi_reversal_bearish: float = 20,
    bullish_reversal_condition: float = -20,
    bearish_reversal_condition: float = 10,
):
    import pandas as pd

    ema20 = pd.to_numeric(nifty_row.get("EMA20", 0), errors="coerce")
    ema50 = pd.to_numeric(nifty_row.get("EMA50", 0), errors="coerce")
    rsi_value = pd.to_numeric(nifty_row.get("RSI", 0), errors="coerce")
    ema_diff = 0 if pd.isna(ema20) or pd.isna(ema50) else float(ema20) - float(ema50)
    rsi = 0 if pd.isna(rsi_value) else float(rsi_value)

    if ema_diff > bullish_threshold and rsi > rsi_bull:
        return "BULLISH", ""
    if ema_diff < bearish_threshold and rsi < rsi_bear:
        return "BEARISH", ""
    if ema_diff >= bullish_reversal_condition and rsi > rsi_reversal_bullish:
        return "BULLISH", RSI_EARLY_BULL_REMARK
    if ema_diff <= bearish_reversal_condition and rsi < rsi_reversal_bearish:
        return "BEARISH", RSI_EARLY_BEAR_REMARK
    return "SIDEWAYS", ""


def market_trend(
    nifty_row,
    bullish_threshold: float = 5,
    bearish_threshold: float = -5,
    rsi_bull: float = 55,
    rsi_bear: float = 45,
    rsi_reversal_bullish: float = 70,
    rsi_reversal_bearish: float = 20,
    bullish_reversal_condition: float = -20,
    bearish_reversal_condition: float = 10,
):
    trend, _remark = market_trend_signal(
        nifty_row,
        bullish_threshold,
        bearish_threshold,
        rsi_bull,
        rsi_bear,
        rsi_reversal_bullish,
        rsi_reversal_bearish,
        bullish_reversal_condition,
        bearish_reversal_condition,
    )
    return trend


def option_score_calculation_details(score_row, scoring_settings=None):
    score_settings = fast_ohlcv_settings(scoring_settings)
    return {
        "Early Score Calculation": (
            f"Stopped Falling {score_row.get('Price Stopped Falling Points', '')} + "
            f"Green Candle {score_row.get('Green Candle Points', '')} + "
            f"Previous High Attack {score_row.get('Previous High Attack Points', '')} + "
            f"Volume Pickup {score_row.get('Volume Pickup Points', '')} = "
            f"{score_row.get('Early Score', '')}"
        ),
        "Main Fast Trigger Calculation": (
            "PASS when score, green candle, previous-high attack, trigger wick, "
            "recent low, volume, body percent, and close-position filters pass"
        ),
        "Rejection Calculation": score_row.get("Rejection Reason", ""),
        "Active Fast Settings": (
            f"Limit {score_settings['buy_limit_score_low']} to <{score_settings['market_entry_score']}; "
            f"Market >= {score_settings['market_entry_score']}; "
            f"Limit validity {score_settings['buy_limit_validity_seconds']}s"
        ),
    }


def ensure_option_formula_columns(df, scoring_settings=None):
    return apply_fast_ohlcv_columns(df, scoring_settings)


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


def calculate_scores(df, i, entry_score_threshold: float = 40, scoring_settings=None):
    settings = dict(scoring_settings or {})
    score = decide_entry_type(
        df,
        i,
        settings,
        current_candle_closed=settings.get("_fast_current_candle_closed", True) is not False,
        ltp=settings.get("_fast_ltp"),
        spread=settings.get("_fast_spread_points"),
    )
    return {
        "Early Score": score.get("Early Score", 0),
        "Buy Setup": score.get("Setup Status", ""),
        "Buy Entry": score.get("Buy Entry", ""),
        "Entry Filters Passed": "YES" if score.get("Buy Entry") == "BUY" else "NO",
        "Entry Block Reason": score.get("Decision Reason", ""),
        **score,
    }


def build_scoring_row(
    df,
    i,
    bullish_threshold: float = 5,
    bearish_threshold: float = -5,
    rsi_bull: float = 55,
    rsi_bear: float = 45,
    rsi_reversal_bullish: float = 70,
    rsi_reversal_bearish: float = 20,
    bullish_reversal_condition: float = -20,
    bearish_reversal_condition: float = 10,
    data_kind="nifty",
    entry_score_threshold: float = 40,
    scoring_settings=None,
    include_calculations=False,
):
    if data_kind == "nifty":
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
            bullish_reversal_condition,
            bearish_reversal_condition,
        )
        return {
            "Trend": trend,
            "EMA20": row.get("EMA20", 0),
            "EMA50": row.get("EMA50", 0),
            "Crossover": float(row.get("EMA20", 0) or 0) - float(row.get("EMA50", 0) or 0),
            "RSI": row.get("RSI", 0),
        }

    settings = dict(scoring_settings or {})
    score = decide_entry_type(
        df,
        i,
        settings,
        current_candle_closed=settings.get("_fast_current_candle_closed", True) is not False,
        ltp=settings.get("_fast_ltp"),
        spread=settings.get("_fast_spread_points"),
    )
    result = {
        **score,
        "Buy Setup": score.get("Setup Status", ""),
        "Entry Filters Passed": "YES" if score.get("Buy Entry") == "BUY" else "NO",
        "Entry Block Reason": score.get("Decision Reason", ""),
    }
    if include_calculations:
        result.update(option_score_calculation_details(result, scoring_settings))
    return result
