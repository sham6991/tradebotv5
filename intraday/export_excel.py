from __future__ import annotations

import os
from datetime import datetime

import pandas as pd

from .constants import MODE_BACKTEST, MODE_REAL, MODE_REPLAY


SHEETS = [
    ("Session Summary", "intraday_sessions"),
    ("Mode State", "intraday_audit_log"),
    ("Symbols", "intraday_symbols"),
    ("Market Cue", "intraday_market_cues"),
    ("Stock Snapshots", "intraday_stock_snapshots"),
    ("Signals", "intraday_signals"),
    ("Margin Checks", "intraday_margin_checks"),
    ("Orders", "intraday_orders"),
    ("Order Events", "intraday_order_events"),
    ("Trades", "intraday_trades"),
    ("Trade Health", "intraday_trade_health"),
    ("Trade Management Events", "intraday_trade_management_events"),
    ("News", "intraday_news"),
    ("Post-Trade Learning", "intraday_post_trade_learning"),
    ("Errors", "intraday_errors"),
]


def session_export_path(base_result_folder: str, mode: str, session_id: str) -> str:
    mode = str(mode).upper()
    if mode == MODE_REAL:
        branch = "real"
    elif mode == MODE_BACKTEST:
        branch = "backtest"
    elif mode == MODE_REPLAY:
        branch = "replay"
    else:
        branch = "paper"
    day = datetime.now().strftime("%Y-%m-%d")
    folder = os.path.join(base_result_folder, "intraday", branch, day)
    os.makedirs(folder, exist_ok=True)
    suffix = f"intraday_{branch}"
    return os.path.join(folder, f"{session_id}_{suffix}.xlsx")


def export_session(database, session_id: str, mode: str, base_result_folder: str) -> str:
    path = session_export_path(base_result_folder, mode, session_id)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        settings_rows = database.table_rows("intraday_sessions", session_id=session_id)
        pd.DataFrame(settings_rows).to_excel(writer, sheet_name="Session Summary", index=False)
        pd.DataFrame(settings_rows).to_excel(writer, sheet_name="Locked Settings", index=False)
        for sheet_name, table in SHEETS[1:]:
            pd.DataFrame(database.table_rows(table, session_id=session_id)).to_excel(writer, sheet_name=sheet_name, index=False)
        pd.DataFrame(database.table_rows("intraday_signals", session_id=session_id)).to_excel(writer, sheet_name="Score Breakdown", index=False)
        pd.DataFrame(database.table_rows("intraday_audit_log", session_id=session_id)).to_excel(writer, sheet_name="Risk Events", index=False)
        pd.DataFrame(database.table_rows("intraday_orders", session_id=session_id)).to_excel(writer, sheet_name="Broker Responses", index=False)
        pd.DataFrame(database.table_rows("intraday_trades", session_id=session_id)).to_excel(writer, sheet_name="Daily P&L Curve", index=False)
    return path
