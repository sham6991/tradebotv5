from trading_core import TradingCore
from engine import TradingEngine
from config import LOT_SIZE
from indicators import clean_and_add_indicators
from strategy import BUY_SCORE_REPORT_COLUMNS, build_scoring_row, ensure_option_formula_columns
from reporting import format_datetime_value, format_time_columns, write_sqlite, validate_risk_engine_sqlite
from datetime import datetime


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
            "Field": "Aggression Score",
            "Formula": "Bullish Close Score + Volume Strength Score + Candle Body Strength Score + Breakout Score + Expansion Score",
            "Active Setting": "",
        },
        {
            "Field": "Capped Aggression Score",
            "Formula": "min(Aggression Score, aggression_score_cap). If cap <= 0, Aggression Score is used as-is.",
            "Active Setting": settings.get("aggression_score_cap", ""),
        },
        {
            "Field": "Buy Score",
            "Formula": "Capped Aggression Score + Higher Low Score + Compression Score + Failed Breakout Penalty + Bear Trap Penalty + Bull Trap penalty",
            "Active Setting": "",
        },
        {
            "Field": "Compression Score",
            "Formula": "15 when Range Ratio < compression_range_ratio, else 0",
            "Active Setting": settings.get("compression_range_ratio", ""),
        },
        {
            "Field": "Expansion Score",
            "Formula": "25 when Range Ratio > expansion_range_ratio, else 0",
            "Active Setting": settings.get("expansion_range_ratio", ""),
        },
        {
            "Field": "Failed Breakout Penalty",
            "Formula": "failed_breakout_penalty when any prior 3 candles had high > previous high and Close Position Score < 0.5",
            "Active Setting": settings.get("failed_breakout_penalty", ""),
        },
        {
            "Field": "Liquidity Filter",
            "Formula": "PASS when Volume Ratio >= min_volume_ratio and volume >= min_option_volume",
            "Active Setting": f"{settings.get('min_volume_ratio', '')}, {settings.get('min_option_volume', '')}",
        },
        {
            "Field": "Chase Filter",
            "Formula": "PASS when Average Range is unavailable, Range Ratio <= max_chase_range_ratio, or follow-through is strong",
            "Active Setting": settings.get("max_chase_range_ratio", ""),
        },
        {
            "Field": "Early Breakout Probability Score",
            "Formula": "Compression Score + Volume Strength Score + Higher Low Score + Bullish Close Score + Upper Wick Shrink Score",
            "Active Setting": settings.get("early_breakout_min_score", ""),
        },
        {
            "Field": "High Probability Buy",
            "Formula": "HIGH PROB BUY when Buy Score >= strong_buy_score, Early Breakout Probability Score >= early_breakout_min_score, and Momentum Acceleration Score > 0",
            "Active Setting": f"{settings.get('strong_buy_score', '')}, {settings.get('early_breakout_min_score', '')}",
        },
        {
            "Field": "Buy Entry",
            "Formula": "BUY when Buy Score >= min_buy_score and both Liquidity Filter and Chase Filter pass",
            "Active Setting": settings.get("min_buy_score", ""),
        },
    ]


def run_backtest(nifty, options, settings, save_path):
    nifty, options = trim_to_common_datetime(nifty, options, settings)

    engine = TradingEngine(settings["cooldown"])

    core = TradingCore(engine, mode="BACKTEST")

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
        score = build_scoring_row(
            nifty,
            i,
            bullish_threshold,
            bearish_threshold,
            rsi_bull,
            rsi_bear,
            rsi_reversal_bullish,
            rsi_reversal_bearish,
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
                min_buy_score=settings.get("min_buy_score", 75),
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
                **{column: score_row.get(column, "") for column in BUY_SCORE_REPORT_COLUMNS},
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
    score_formula_df = pd.DataFrame(score_formula_rows(settings))
    risk_df = format_time_columns(df)

    with pd.ExcelWriter(save_path) as writer:
        trades_df.to_excel(writer, sheet_name="Trades", index=False)
        ce_trades.to_excel(writer, sheet_name="CE Trades", index=False)
        pe_trades.to_excel(writer, sheet_name="PE Trades", index=False)
        candles_df.to_excel(writer, sheet_name="Candles", index=False)
        option_scores_df.to_excel(writer, sheet_name="Option Scores", index=False)
        score_formula_df.to_excel(writer, sheet_name="Score Formula", index=False)
        skips_df.to_excel(writer, sheet_name="Skips", index=False)
        pd.DataFrame(settings_rows).to_excel(writer, sheet_name="Settings", index=False)
        pd.DataFrame(option_rows).to_excel(writer, sheet_name="Option Metadata", index=False)
        risk_df.to_excel(writer, sheet_name="Risk Engine Trades", index=False)

    sql_path = save_path.replace(".xlsx", ".db")
    write_sqlite(sql_path, df, table_name="trades")
    write_sqlite(sql_path, option_scores_df, table_name="option_scores")
    write_sqlite(sql_path, score_formula_df, table_name="score_formula")

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
