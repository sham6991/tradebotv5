from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from typing import Any

from .backtest_replay import run_candle_replay
from .historical_data import fetch_zerodha_stock_day
from .paper_account import PaperAccountStore
from .simulated_market_data import generate_stock_day


class PaperBacktester:
    def __init__(self, manager):
        self.manager = manager

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        persistent_before = self.manager.paper_account.snapshot()
        starting_balance = self._starting_balance(payload, persistent_before)
        with tempfile.TemporaryDirectory(prefix="tradebotv5_intraday_backtest_") as temp_dir:
            from .session_manager import IntradaySessionManager

            backtest_manager = IntradaySessionManager(
                self.manager.base_result_folder,
                zerodha_client_provider=self.manager.zerodha_client_provider,
            )
            backtest_manager.paper_account = PaperAccountStore(
                os.path.join(temp_dir, "paper_account.json"),
                default_balance=starting_balance,
            )
            backtest_manager.paper_account.reset(starting_balance)
            result = self._run_isolated(backtest_manager, payload, starting_balance)
        persistent_after = self.manager.paper_account.snapshot()
        result["persistent_paper_account_before"] = persistent_before
        result["persistent_paper_account_after"] = persistent_after
        result["summary"]["persistent_paper_account"] = persistent_after
        result["summary"]["paper_balance_unchanged"] = self._same_account_balance(persistent_before, persistent_after)
        result["stopped"]["funds"] = persistent_after
        result["stopped"]["paper_account"] = persistent_after
        return result

    def _run_isolated(self, manager, payload: dict[str, Any], starting_balance: float) -> dict[str, Any]:
        session_payload = dict(payload)
        session_payload["mode"] = "BACKTEST"
        session_payload["ask_permission_before_entry"] = False
        session_payload["order_mode"] = "LIMIT_ONLY"
        session_payload["paper_starting_balance"] = starting_balance
        session_payload["reset_paper_balance"] = False
        session_payload["change_paper_balance"] = False
        if not session_payload.get("minimum_entry_score"):
            session_payload["minimum_entry_score"] = 55
        started = manager.start_session(session_payload)
        backtest_date = payload.get("backtest_date") or datetime.now().date().isoformat()
        market_data = payload.get("market_data") or self._zerodha_market_data(manager, payload, started, backtest_date)
        market_data = market_data or self.generate_market_data(
            payload.get("stocks") or started["settings"]["stocks"],
            backtest_date,
            interval=session_payload.get("candle_interval"),
        )
        replay = run_candle_replay(manager, market_data, payload)
        evaluated = replay["evaluated"]
        stopped = manager.stop_session()
        backtest_account = stopped.get("paper_account") or {}
        trades = manager.database.table_rows("intraday_trades", manager.session_id)
        return {
            "started": started,
            "evaluated": evaluated,
            "stopped": stopped,
            "replay": replay,
            "summary": {
                "session_id": stopped["session_id"],
                "snapshots": len(evaluated.get("snapshots") or []),
                "active_trade": stopped.get("active_trade"),
                "session_pnl": stopped.get("session_pnl"),
                "backtest_account": backtest_account,
                "candle_count": replay["candle_count"],
                "replay_steps": len(replay["timeline"]),
                "best_signals": replay["best_signals"][:10],
                "best_possible_trades": replay["best_signals"][:10],
                "best_trades": sorted(trades, key=lambda row: float(row.get("pnl_net") or 0), reverse=True),
                "export_path": stopped.get("export_path"),
                "data_source": "zerodha_historical" if any((row or {}).get("source") == "zerodha_historical" for row in market_data.values()) else "simulated",
            },
        }

    def _starting_balance(self, payload: dict[str, Any], persistent_snapshot: dict[str, Any]) -> float:
        requested = payload.get("paper_starting_balance")
        if requested in ("", None):
            requested = payload.get("balance")
        try:
            value = float(requested)
        except (TypeError, ValueError):
            value = float(persistent_snapshot.get("available") or 100000.0)
        return max(1.0, value)

    def _same_account_balance(self, before: dict[str, Any], after: dict[str, Any]) -> bool:
        keys = ("available", "used_margin", "position_value", "realized_pnl", "unrealized_pnl", "charges", "net_pnl")
        return all(round(float(before.get(key) or 0), 6) == round(float(after.get(key) or 0), 6) for key in keys)

    def _zerodha_market_data(self, manager, payload: dict[str, Any], started: dict[str, Any], backtest_date: str) -> dict[str, Any]:
        provider = getattr(manager, "zerodha_client_provider", None)
        client = provider("PAPER") if provider else None
        if not client:
            return {}
        try:
            return fetch_zerodha_stock_day(
                client,
                payload.get("stocks") or started["settings"]["stocks"],
                backtest_date,
                interval=payload.get("candle_interval") or "minute",
            )
        except Exception:
            return {}

    def generate_market_data(self, stocks: list[Any], backtest_date: str, interval: str | None = None) -> dict[str, Any]:
        return generate_stock_day(stocks, backtest_date, interval=interval or "minute")

    def _future_market_data(self, market_data: dict[str, Any]) -> dict[str, Any]:
        replay = {}
        for symbol, row in market_data.items():
            candles = list(row.get("candles") or [])
            future = list(row.get("future_candles") or candles[-3:])
            replay[symbol] = {**row, "candles": candles + future[:3], "ltp": (future[:3] or candles)[-1]["close"]}
        return replay
