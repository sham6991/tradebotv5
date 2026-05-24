from backtest_runtime import BacktestTradingCore
from engine import TradingEngine
from config import LOT_SIZE
from config_profile import apply_settings_profile, profile_from_settings
from indicators import clean_and_add_indicators
from strategy import OPTION_ENTRY_REPORT_COLUMNS, build_scoring_row, ensure_option_formula_columns
from reporting import format_datetime_value, format_time_columns, write_sqlite, validate_risk_engine_sqlite
from datetime import datetime


APP_STARTED_AT = datetime.now()


def export_time(row):
    import pandas as pd

    value = row.get("datetime", "")
    if value != "" and not pd.isna(value):
        return format_datetime_value(value)
    date = row.get("date", "")
    time = row.get("time", "")
    if date != "" and time != "" and not pd.isna(date) and not pd.isna(time):
        return f"{date} {time}"
    if date != "" and not pd.isna(date):
        return date
    return ""


def trim_to_common_datetime(nifty, options, settings=None):
    import pandas as pd

    frames = [nifty, *options]
    if any("datetime" not in frame.columns or frame["datetime"].isna().all() for frame in frames):
        return nifty, options

    start = max(pd.to_datetime(frame["datetime"], errors="coerce").min() for frame in frames)
    end = min(pd.to_datetime(frame["datetime"], errors="coerce").max() for frame in frames)

    if pd.isna(start) or pd.isna(end) or start > end:
        return nifty, options

    def trim(frame):
        attrs = dict(frame.attrs)
        times = pd.to_datetime(frame["datetime"], errors="coerce")
        trimmed = frame[(times >= start) & (times <= end)].copy().reset_index(drop=True)
        trimmed.attrs.update(attrs)
        return trimmed

    nifty = trim(nifty)
    for col in ("EMA20", "EMA50", "RSI"):
        if col in nifty.columns:
            nifty = nifty.drop(columns=[col])
    nifty = clean_and_add_indicators(nifty)

    trimmed_options = []
    for option in options:
        trimmed = trim(option)
        trimmed.attrs.update(option.attrs)
        trimmed = ensure_option_formula_columns(trimmed, settings)
        trimmed_options.append(trimmed)

    return nifty, trimmed_options


def score_formula_rows(settings):
    return [
        {
            "Field": "Early Score",
            "Formula": "Price Stopped Falling + Green Candle + Previous High Attack + Volume Pickup, maximum 60 points",
            "Active Setting": "",
        },
        {
            "Field": "Main Fast Trigger",
            "Formula": "Early Score >= BuyLimitScoreLow, green candle, High > PreviousHigh, trigger wick, recent low, volume, body, and close-position checks",
            "Active Setting": f"{settings.get('buy_limit_score_low', '')}, {settings.get('trigger_upper_wick_max', '')}",
        },
        {
            "Field": "Market Entry",
            "Formula": "Main Fast Trigger + no rejection + Early Score >= MarketEntryScore + market body/close-position filters",
            "Active Setting": f"{settings.get('market_entry_score', '')}, {settings.get('market_entry_minimum_body_percent', '')}, {settings.get('market_entry_minimum_close_position', '')}",
        },
        {
            "Field": "Buy Limit Entry",
            "Formula": "Main Fast Trigger + no rejection + Early Score >= BuyLimitScoreLow and < MarketEntryScore",
            "Active Setting": f"{settings.get('buy_limit_score_low', '')} to <{settings.get('market_entry_score', '')}",
        },
        {
            "Field": "Buy Limit Price",
            "Formula": "Close - min(max(AvgRange10 * BuyLimitOffsetMultiplier, MinimumOffset), MaximumOffset)",
            "Active Setting": f"{settings.get('buy_limit_offset_multiplier', '')}, {settings.get('minimum_offset', '')}, {settings.get('maximum_offset', '')}",
        },
        {
            "Field": "Rejection Filters",
            "Formula": "Reject weak/red/stretched candles, failed recent low, hard upper wick, weak volume, weak body/close-position, gap spike, optional chop, or wide spread",
            "Active Setting": f"{settings.get('hard_rejection_upper_wick_max', '')}, {settings.get('large_candle_multiplier', '')}, {settings.get('move_from_low_max_multiplier', '')}",
        },
        {
            "Field": "Backtest Limit Fill",
            "Formula": "SIMPLE uses next Low <= limit; CONSERVATIVE also requires next Close >= limit and green next candle; STRICT does not test OHLC limits",
            "Active Setting": settings.get("backtest_limit_fill_mode", ""),
        },
        {
            "Field": "Aggressive Live Entry",
            "Formula": "Only before candle close when enabled, with stricter LTP, wick, move-from-low, body, close-position, volume, and rejection checks",
            "Active Setting": f"{settings.get('aggressive_live_entry_enabled', '')}, {settings.get('aggressive_entry_score', '')}",
        },
    ]


def git_commit_hash():
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def max_drawdown_from_trades(trades, initial_balance):
    peak = float(initial_balance or 0)
    max_drawdown = 0.0
    for trade in trades:
        balance = trade.get("Total PnL", peak)
        try:
            balance = float(balance)
        except (TypeError, ValueError):
            continue
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, peak - balance)
    return max_drawdown


def backtest_run_metadata_rows(settings, core, save_path, sheet_names):
    profile = profile_from_settings(settings)
    trades = list(core.trades)
    winning = sum(1 for trade in trades if float(trade.get("PnL", 0) or 0) > 0)
    losing = sum(1 for trade in trades if float(trade.get("PnL", 0) or 0) < 0)
    flat = len(trades) - winning - losing
    initial_balance = float(settings.get("balance", 0) or 0)
    metadata = [
        ("Report Timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("App Start Timestamp", APP_STARTED_AT.strftime("%Y-%m-%d %H:%M:%S")),
        ("Code Version / Git Commit", git_commit_hash()),
        ("Settings Hash", profile.get("settings_hash", "")),
        ("Settings Version", profile.get("settings_version", "")),
        ("Settings Schema Version", profile.get("settings_schema_version", "")),
        ("Profile Name", settings.get("profile_name", "backtest")),
        ("Mode", "BACKTEST"),
        ("Data Source", settings.get("data_source", "uploaded/server file")),
        ("Chart Interval", settings.get("chart_interval", "")),
        ("Broker Connected", "No"),
        ("Backtest Fill Mode", settings.get("backtest_limit_fill_mode", "")),
        ("Slippage Model", settings.get("market_entry_backtest_mode", "SIGNAL_CLOSE")),
        ("Initial Balance", initial_balance),
        ("Final Balance", core.balance),
        ("Net PnL", core.balance - initial_balance),
        ("Trade Count", len(trades)),
        ("Winning Trades", winning),
        ("Losing Trades", losing),
        ("Flat Trades", flat),
        ("Max Drawdown", max_drawdown_from_trades(trades, initial_balance)),
        ("Export Path", save_path),
        ("Sheet Guide", ", ".join(sheet_names)),
    ]
    return [{"Field": field, "Value": value} for field, value in metadata]


def run_backtest(nifty, options, settings, save_path):
    apply_settings_profile(settings)
    nifty, options = trim_to_common_datetime(nifty, options, settings)

    engine = TradingEngine(settings["cooldown"])

    core = BacktestTradingCore(engine)

    core.balance = settings["balance"]
    core.lot_size = settings["lot_size"] * LOT_SIZE
    core.max_trades = settings["max_trades"]

    candle_rows = []
    skip_rows = []

    for i in range(6, len(nifty) - 1):
        before_trade_count = core.trade_count
        core.process(nifty, options, i, settings)
        nifty_row = nifty.iloc[i]
        bullish_threshold = float(settings.get("bullish_threshold", 16))
        bearish_threshold = float(settings.get("bearish_threshold", -15))
        rsi_bull = float(settings.get("rsi_bull", 55))
        rsi_bear = float(settings.get("rsi_bear", 45))
        rsi_reversal_bullish = float(settings.get("rsi_reversal_bullish", 70))
        rsi_reversal_bearish = float(settings.get("rsi_reversal_bearish", 20))
        bullish_reversal_condition = float(settings.get("bullish_reversal_condition", -20))
        bearish_reversal_condition = float(settings.get("bearish_reversal_condition", 10))
        score = build_scoring_row(
            nifty,
            i,
            bullish_threshold,
            bearish_threshold,
            rsi_bull,
            rsi_bear,
            rsi_reversal_bullish,
            rsi_reversal_bearish,
            bullish_reversal_condition,
            bearish_reversal_condition,
        )
        candle_rows.append({
            "Datetime": export_time(nifty_row),
            "Open": nifty_row.get("open", ""),
            "High": nifty_row.get("high", ""),
            "Low": nifty_row.get("low", ""),
            "Close": nifty_row.get("close", ""),
            **score,
        })
        if core.trade_count == before_trade_count:
            skip_rows.append({
                "Index": i,
                "Datetime": export_time(nifty_row),
                "EMA20": nifty_row.get("EMA20", ""),
                "EMA50": nifty_row.get("EMA50", ""),
                "TrendDiff": (
                    float(nifty_row.get("EMA20", 0) or 0)
                    - float(nifty_row.get("EMA50", 0) or 0)
                ),
                "Skip Reason": core.engine.last_skip_reason or "no_trade",
            })

    import pandas as pd

    def normalize_trade(trade):
        return {
            "trade_id": trade.get("trade_id") or f"BACKTEST_{trade.get('Trade No', 0)}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "mode": "BACKTEST",
            "strategy_name": settings.get("strategy_name", "tradebotV3_backtest"),
            "strategy_version": settings.get("strategy_version", "1.0"),
            "entry_time": trade.get("Entry Time"),
            "exit_time": trade.get("Exit Time"),
            "instrument": trade.get("Instrument", ""),
            "option_symbol": trade.get("Instrument", ""),
            "option_type": trade.get("Type", ""),
            "strike": trade.get("Strike"),
            "expiry": trade.get("Expiry"),
            "entry_price": trade.get("Entry"),
            "exit_price": trade.get("Exit"),
            "quantity": trade.get("Quantity", 0),
            "lot_size": trade.get("Contract Lot Size", 0),
            "pnl_points": trade.get("PnL", 0),
            "pnl_amount": trade.get("PnL", 0),
            "pnl_percent": trade.get("PnL %") if trade.get("PnL %") is not None else None,
            "charges": trade.get("Charges", 0.0),
            "net_pnl": trade.get("Total PnL", core.balance),
            "exit_reason": trade.get("Reason", ""),
            "trade_duration_minutes": trade.get("Duration", None),
            "market_regime_at_entry": trade.get("Market Regime", ""),
            "market_regime_at_exit": trade.get("Market Regime", ""),
            "risk_profile_id": None,
        }

    settings_rows = [
        {"Parameter": key, "Value": value}
        for key, value in sorted(settings.items())
    ]
    option_rows = []
    for index, option in enumerate(options, start=1):
        option_rows.append({
            "Option No": index,
            "Instrument": option.attrs.get("instrument", ""),
            "Tradingsymbol": option.attrs.get("tradingsymbol", ""),
            "Strike": option.attrs.get("strike", ""),
            "Expiry": option.attrs.get("expiry", ""),
        })

    option_score_rows = []
    for option_no, option in enumerate(options, start=1):
        scored = ensure_option_formula_columns(option, settings)
        metadata = {
            "Option No": option_no,
            "Instrument": scored.attrs.get("instrument", ""),
            "Tradingsymbol": scored.attrs.get("tradingsymbol", ""),
            "Type": scored.attrs.get("option_type", ""),
            "Strike": scored.attrs.get("strike", ""),
            "Expiry": scored.attrs.get("expiry", ""),
        }
        for row_index, row in scored.iterrows():
            score_row = build_scoring_row(
                scored,
                row_index,
                data_kind="option",
                entry_score_threshold=settings.get("buy_limit_score_low", 40),
                scoring_settings=settings,
                include_calculations=True,
            )
            option_score_rows.append({
                **metadata,
                "Index": row_index,
                "Datetime": export_time(row),
                "Open": row.get("open", ""),
                "High": row.get("high", ""),
                "Low": row.get("low", ""),
                "Close": row.get("close", ""),
                "Volume": row.get("volume", ""),
                **{column: score_row.get(column, "") for column in OPTION_ENTRY_REPORT_COLUMNS},
                "Buy Setup": score_row.get("Buy Setup", ""),
                "Entry Filters Passed": score_row.get("Entry Filters Passed", ""),
                "Entry Block Reason": score_row.get("Entry Block Reason", ""),
                "Early Score Calculation": score_row.get("Early Score Calculation", ""),
                "Main Fast Trigger Calculation": score_row.get("Main Fast Trigger Calculation", ""),
                "Rejection Calculation": score_row.get("Rejection Calculation", ""),
                "Active Fast Settings": score_row.get("Active Fast Settings", ""),
                "Sell Score": score_row.get("Sell Score", ""),
                "Sell Entry": score_row.get("Sell Entry", ""),
            })

    normalized_trades = [normalize_trade(t) for t in core.trades]
    df = pd.DataFrame(normalized_trades)

    trades_df = format_time_columns(pd.DataFrame(core.trades))
    ce_trades = trades_df[trades_df.get("Type", pd.Series(dtype=str)).astype(str).str.upper() == "CE"].copy()
    pe_trades = trades_df[trades_df.get("Type", pd.Series(dtype=str)).astype(str).str.upper() == "PE"].copy()
    candles_df = format_time_columns(pd.DataFrame(candle_rows))
    skips_df = format_time_columns(pd.DataFrame(skip_rows))
    option_scores_df = format_time_columns(pd.DataFrame(option_score_rows))
    entry_attempts_df = format_time_columns(pd.DataFrame(core.entry_attempts))
    score_formula_df = pd.DataFrame(score_formula_rows(settings))
    risk_df = format_time_columns(df)

    sheet_names = [
        "Run Metadata",
        "Trades",
        "CE Trades",
        "PE Trades",
        "Candles",
        "Option Scores",
        "Entry Attempts",
        "Score Formula",
        "Skips",
        "Settings",
        "Option Metadata",
        "Risk Engine Trades",
    ]
    run_metadata_df = pd.DataFrame(backtest_run_metadata_rows(settings, core, save_path, sheet_names))

    with pd.ExcelWriter(save_path) as writer:
        run_metadata_df.to_excel(writer, sheet_name="Run Metadata", index=False)
        trades_df.to_excel(writer, sheet_name="Trades", index=False)
        ce_trades.to_excel(writer, sheet_name="CE Trades", index=False)
        pe_trades.to_excel(writer, sheet_name="PE Trades", index=False)
        candles_df.to_excel(writer, sheet_name="Candles", index=False)
        option_scores_df.to_excel(writer, sheet_name="Option Scores", index=False)
        entry_attempts_df.to_excel(writer, sheet_name="Entry Attempts", index=False)
        score_formula_df.to_excel(writer, sheet_name="Score Formula", index=False)
        skips_df.to_excel(writer, sheet_name="Skips", index=False)
        pd.DataFrame(settings_rows).to_excel(writer, sheet_name="Settings", index=False)
        pd.DataFrame(option_rows).to_excel(writer, sheet_name="Option Metadata", index=False)
        risk_df.to_excel(writer, sheet_name="Risk Engine Trades", index=False)

    sql_path = save_path.replace(".xlsx", ".db")
    write_sqlite(sql_path, df, table_name="trades")
    write_sqlite(sql_path, option_scores_df, table_name="option_scores")
    write_sqlite(sql_path, entry_attempts_df, table_name="entry_attempts")
    write_sqlite(sql_path, score_formula_df, table_name="score_formula")
    write_sqlite(sql_path, run_metadata_df, table_name="run_metadata")

    backtest_run = [{
        "run_id": f"BACKTEST_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "started_at": settings.get("start_date", ""),
        "ended_at": settings.get("end_date", ""),
        "strategy_name": settings.get("strategy_name", "tradebotV3_backtest"),
        "strategy_version": settings.get("strategy_version", "1.0"),
        "data_start_date": settings.get("start_date", ""),
        "data_end_date": settings.get("end_date", ""),
        "initial_capital": settings.get("balance", 0),
        "final_capital": core.balance,
        "total_trades": len(core.trades),
        "net_pnl": core.balance - settings.get("balance", 0),
        "max_drawdown": None,
        "notes": "Backtest export for risk engine"
    }]
    write_sqlite(sql_path, backtest_run, table_name="backtest_runs")

    if not validate_risk_engine_sqlite(sql_path):
        raise RuntimeError(f"SQLite export {sql_path} does not conform to risk engine schema")

    return core.balance, core.trades
