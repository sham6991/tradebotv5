from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Any

from .audit_logger import AuditLogger
from .candle_feed import candle_timestamp, depth_from_ltp, market_open_close, market_slice, max_candle_count
from .constants import (
    MODE_BACKTEST,
    MODE_PAPER,
    MODE_REAL,
    MODE_REPLAY,
    ORDER_LIMIT_ONLY,
    SESSION_STATUS_IDLE,
    SESSION_STATUS_KILLED,
    SESSION_STATUS_RUNNING,
    SESSION_STATUS_STOPPED,
    SIDE_LONG,
    SIDE_SHORT,
)
from .database import IntradayDatabase
from .data_source_policy import IntradayDataSource, resolve_intraday_data_source
from .entry_structure import analyse_entry_structure
from .execution_safeguards import real_execution_blockers
from .export_excel import export_session
from .fii_dii_upload import parse_intraday_fii_dii_csv_file, parse_intraday_fii_dii_csv_text, upload_status
from .formula_validator import formula_metadata
from .historical_data import fetch_zerodha_stock_candles
from .indicators import ema, relative_volume, rsi
from .liquidity import score_liquidity
from .market_context import context_alignment
from .mode_manager import SessionModeManager
from .models import IntradaySettings, Signal, StockSnapshot
from .news_engine import NewsEngine, sentiment_score_for_symbol
from .options_bias import calculate_options_bias
from .order_lifecycle import IntradayOrderLifecycle
from .paper_account import PaperAccountStore
from .paper_backtest import PaperBacktester
from .paper_broker import PaperBroker
from .simulated_market_data import generate_stock_day
from .risk_manager import RiskManager
from .scoring import score_snapshot
from .stock_data_readiness import evaluate_stock_data_readiness
from .stock_live_feed import StockLiveFeed
from .stock_selector import select_best_signal
from .traps import detect_trap
from .trade_gates import event_blackout_blockers, signal_eligibility_blockers
from .volume_profile import calculate_volume_profile
from .vwap import calculate_vwap
from .zerodha_broker import ZerodhaBroker
from .order_request import emergency_exit_order


class IntradaySessionManager:
    def __init__(self, base_result_folder: str, zerodha_client_provider=None):
        self.base_result_folder = base_result_folder
        self.db_path = os.path.join(base_result_folder, "intraday", "intraday_terminal.sqlite")
        self.database = IntradayDatabase(self.db_path)
        self.zerodha_client_provider = zerodha_client_provider or (lambda _mode: None)
        self.news_engine = NewsEngine()
        self.paper_account = PaperAccountStore(os.path.join(base_result_folder, "intraday", "paper_account.json"))
        self.status = SESSION_STATUS_IDLE
        self.session_id = ""
        self.settings: IntradaySettings | None = None
        self.risk: RiskManager | None = None
        self.broker = None
        self.audit: AuditLogger | None = None
        self.snapshots: list[StockSnapshot] = []
        self.last_market_data: dict[str, Any] = {}
        self.live_candle_cursor = 79
        self.latest_news: list[dict[str, Any]] = []
        self.latest_news_status: dict[str, Any] = {}
        self.pending_signal: Signal | None = None
        self.last_signal: Signal | None = None
        self.last_export_path = ""
        self.lifecycle: IntradayOrderLifecycle | None = None
        self.mode_manager = SessionModeManager()
        self.uploaded_fii_dii: dict[str, Any] | None = None
        self.instrument_rows: dict[str, dict[str, Any]] = {}
        self.last_context: dict[str, Any] = {}
        self.last_event_blackout_blockers: list[str] = []
        self.last_kill_switch_report: dict[str, Any] = {}
        self.current_data_source_policy: dict[str, Any] = {
            "source": IntradayDataSource.UNAVAILABLE,
            "allowed": False,
            "status": "IDLE",
            "reason": "No intraday session is running.",
            "requires_fetch": False,
            "allow_simulated": False,
            "blockers": [],
            "warnings": [],
            "order_execution": "Paper Simulation",
            "data_mode": "websocket_tick_candles_preferred",
        }
        self.last_data_source_status: dict[str, Any] = dict(self.current_data_source_policy)
        self.last_data_fetch_error = ""
        self.cached_funds: dict[str, Any] | None = None
        self.cached_funds_at = ""
        self.cached_broker_health: dict[str, Any] = {}
        self.last_broker_error = ""
        self.stock_live_feed = StockLiveFeed()
        self._stock_ws_name = ""
        self._stock_ws_mode = ""
        self._stock_ws_tokens: tuple[int, ...] = ()
        self._stock_ws_last_error = ""
        self.last_stock_data_health: dict[str, Any] = {
            "status": "IDLE",
            "new_entries_allowed": False,
            "blockers": ["No intraday session is running."],
            "warnings": [],
            "symbols": [],
        }

    def default_settings(self) -> dict[str, Any]:
        sample = IntradaySettings(
            stocks=[
                self._stock("INFY"),
                self._stock("RELIANCE"),
                self._stock("TCS"),
                self._stock("HDFCBANK"),
                self._stock("ICICIBANK"),
            ]
        )
        return sample.locked_dict()

    def _stock(self, symbol: str):
        from .models import StockInput

        return StockInput(symbol=symbol, exchange="NSE")

    def start_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = IntradaySettings.from_payload(payload)
        blocker = self.mode_manager.blocker_for(settings.mode)
        if blocker:
            raise ValueError(blocker)
        self._require_fii_dii_upload_for_live_mode(settings.mode)
        data_policy = self._data_policy_for_payload(settings, payload)
        if not data_policy.get("allowed"):
            raise ValueError("; ".join(data_policy.get("blockers") or [data_policy.get("reason") or "Intraday market data source is unavailable."]))
        self.current_data_source_policy = data_policy
        self.last_data_source_status = dict(data_policy)
        self.last_data_fetch_error = ""
        if settings.mode == MODE_PAPER:
            if payload.get("reset_paper_balance"):
                self.paper_account.reset(settings.paper_starting_balance)
            elif payload.get("paper_starting_balance") not in ("", None) and payload.get("change_paper_balance"):
                self.paper_account.set_balance(float(payload["paper_starting_balance"]))
            account = self.paper_account.snapshot()
            settings.paper_starting_balance = float(account["available"])
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
        self.settings = settings
        self.risk = RiskManager(settings)
        self.broker = self._build_broker(settings)
        instrument_rows = self._validate_stocks_against_broker(settings)
        self.instrument_rows = instrument_rows
        self._refresh_cached_funds()
        self.mode_manager.start_session(settings.mode)
        self.audit = AuditLogger(self.database, self.session_id)
        self.lifecycle = IntradayOrderLifecycle(
            self.broker,
            self.database,
            settings,
            self.session_id,
            audit=self.audit,
            instrument_rows=instrument_rows,
        )
        self.status = SESSION_STATUS_RUNNING
        self.stock_live_feed = StockLiveFeed(settings.candle_interval)
        self.snapshots = []
        self.last_market_data = {}
        self.live_candle_cursor = 79
        self.latest_news = []
        self.latest_news_status = {}
        self.pending_signal = None
        self.last_signal = None
        selected = [stock.key for stock in settings.stocks]
        if settings.mode in {MODE_PAPER, MODE_REAL}:
            self.stock_live_feed.start([stock.symbol for stock in settings.stocks])
            self.stock_live_feed.configure_instruments(self._stock_instruments_for_live_feed(settings))
            self._start_stock_websocket(settings)
        self.database.create_session({
            "session_id": self.session_id,
            "mode": settings.mode,
            "broker": settings.broker,
            "start_time": datetime.now().isoformat(timespec="seconds"),
            "selected_symbols": selected,
            "locked_settings": {
                **settings.locked_dict(),
                **formula_metadata(),
                "fii_dii_upload": upload_status(self.uploaded_fii_dii),
                "data_source_policy": self.current_data_source_policy,
            },
            "starting_balance": (self.cached_funds or {}).get("available", settings.paper_starting_balance),
            "status": self.status,
        })
        for stock in settings.stocks:
            instrument = instrument_rows.get(stock.key, {})
            self.database.save_symbol(self.session_id, {
                "symbol": stock.symbol,
                "exchange": stock.exchange,
                "validation_status": "VALIDATED",
                "company_name": instrument.get("name") or instrument.get("company_name") or "",
                "sector": instrument.get("sector") or "",
                "instrument_token": instrument.get("instrument_token") or "",
                "tick_size": instrument.get("tick_size") or "",
                "lot_size": instrument.get("lot_size") or "",
                "segment": instrument.get("segment") or "",
                "mis_allowed": 1,
                "data_available": 1,
                "suggestions": [],
            })
        self.audit.log("INFO", "session", "session_started", {"symbols": selected, "mode": settings.mode})
        return self.status_payload()

    def _build_broker(self, settings: IntradaySettings):
        if settings.mode in {MODE_PAPER, MODE_BACKTEST, MODE_REPLAY}:
            return PaperBroker(settings.paper_starting_balance, account_store=self.paper_account, estimated_leverage=settings.estimated_leverage)
        client = self.zerodha_client_provider(MODE_REAL) or self.zerodha_client_provider("LIVE")
        if not client:
            raise ValueError("Real intraday mode requires a connected Zerodha client.")
        return ZerodhaBroker(client)

    def evaluate(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_running()
        payload = payload or {}
        settings = self.settings
        assert settings is not None
        payload = self._with_uploaded_fii_dii(payload)
        symbols = [stock.symbol for stock in settings.stocks]
        news_payload = {**payload, "live_news_enabled": settings.live_news_enabled}
        news_items = self.news_engine.collect(symbols, news_payload) if settings.news_enabled else []
        self.latest_news = [item.to_dict() for item in news_items]
        self.latest_news_status = self.news_engine.last_status if settings.news_enabled else {
            "status": "DISABLED",
            "message": "News engine is disabled; news score is treated as neutral.",
            "adapter_status": [],
        }
        for item in news_items:
            self.database.save_news(self.session_id, item.to_dict())
        context = context_alignment(payload)
        self.last_context = context
        now = self._payload_time(payload) or datetime.now()
        self.last_event_blackout_blockers = event_blackout_blockers(settings, payload, now=now)
        self.database.save_market_cue(self.session_id, {
            "timestamp": now.isoformat(timespec="seconds"),
            "phase": context.get("phase", ""),
            "cue_state": context.get("state") or context.get("trend") or "",
            "market_regime": context.get("regime", ""),
            "nifty_trend": context.get("nifty_trend", context.get("trend", "")),
            "sector_trend": context.get("sector_trend", ""),
            "global_cue": context.get("global_cue", ""),
            "fii_dii_used": 1 if context.get("fii_dii_used") else 0,
            "source_breakdown": context,
            "algo_adjustment": context.get("algo_adjustment", ""),
        })
        snapshots = []
        market_data = self._market_data_for_payload(payload)
        self.last_market_data = market_data
        self.last_stock_data_health = evaluate_stock_data_readiness(settings, market_data, now=now)
        for stock in settings.stocks:
            row = market_data.get(stock.symbol) if isinstance(market_data, dict) else None
            if row is None:
                row = {}
            candles = row.get("candles") or []
            snapshot = self._snapshot_from_candles(stock.symbol, stock.exchange, candles, row, settings, news_items, payload)
            snapshot = score_snapshot(snapshot, settings, context)
            snapshots.append(snapshot)
            self.database.save_snapshot(self.session_id, snapshot.to_dict())
        self.snapshots = snapshots
        if self.lifecycle:
            self.lifecycle.process_market_data(market_data, now=self._payload_time(payload), snapshots=snapshots)
            self._sync_risk_from_lifecycle()
        if self.pending_signal:
            if self._pending_signal_expired(payload):
                self.pending_signal.final_decision = "EXPIRED_NO_USER_RESPONSE"
                self.pending_signal.blockers = _dedupe_signal_blockers(
                    list(self.pending_signal.blockers) + ["User approval timed out after 1 minute."]
                )
                self.database.save_signal(self.pending_signal.to_dict())
                if self.audit:
                    self.audit.log("WARNING", "approval", "entry_expired_no_user_response", self.pending_signal.to_dict())
                self.last_signal = self.pending_signal
                self.pending_signal = None
                return self.status_payload()
            else:
                self.last_signal = self.pending_signal
                if self.audit:
                    self.audit.log("INFO", "evaluation", "approval_waiting", {"signal": self.pending_signal.to_dict()})
                return self.status_payload()
        signal = select_best_signal(snapshots, settings, self.session_id)
        if signal:
            single_trade_blockers = self._single_trade_blockers() if signal.side in {SIDE_LONG, SIDE_SHORT} else []
            data_readiness_blockers = list(self.last_stock_data_health.get("blockers") or []) if signal.side in {SIDE_LONG, SIDE_SHORT} else []
            hard_gate_blockers = self._trade_allowed_blockers(signal, payload, now=now) if signal.side in {SIDE_LONG, SIDE_SHORT} else []
            real_execution_blockers = self._real_execution_blockers(signal) if signal.side in {SIDE_LONG, SIDE_SHORT} else []
            signal.blockers = _dedupe_signal_blockers(list(signal.blockers) + single_trade_blockers + data_readiness_blockers + hard_gate_blockers + real_execution_blockers)
            signal.blockers = self.risk.pre_trade_blockers(signal) if self.risk else signal.blockers
            if signal.blockers:
                signal.final_decision = "BLOCKED"
            elif settings.ask_permission_before_entry:
                signal.final_decision = "PENDING_APPROVAL"
                self.pending_signal = signal
            else:
                signal.final_decision = "ELIGIBLE"
                if settings.mode == MODE_REAL and not settings.auto_real_orders_confirmed:
                    signal.blockers.append("Auto real orders are not separately confirmed.")
                    signal.final_decision = "BLOCKED"
                elif signal.side in {SIDE_LONG, SIDE_SHORT}:
                    self._place_entry(signal)
            self.last_signal = signal
            self.database.save_signal(signal.to_dict())
        if self.audit:
            self.audit.log("INFO", "evaluation", "cycle_completed", {"snapshots": len(snapshots), "signal": signal.to_dict() if signal else None})
        return self.status_payload()

    def process_orders(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_running()
        payload = payload or {}
        if self.lifecycle:
            when = self._payload_time(payload) or datetime.now()
            if payload.get("force_entry_timeout"):
                when = when + timedelta(seconds=61)
            market_data = self._market_data_for_payload(payload)
            self.last_market_data = market_data
            self.lifecycle.process_market_data(market_data, now=when, snapshots=self.snapshots)
            self._sync_risk_from_lifecycle()
        return self.status_payload()

    def _snapshot_from_candles(
        self,
        symbol: str,
        exchange: str,
        candles: list[dict],
        row: dict[str, Any],
        settings: IntradaySettings,
        news_items,
        payload: dict[str, Any],
    ) -> StockSnapshot:
        close_values = [float(candle.get("close") or 0) for candle in candles if candle.get("close") not in ("", None)]
        latest = candles[-1] if candles else row
        ltp = float(row.get("ltp") or latest.get("close") or 0)
        source = str(row.get("source") or "").strip()
        if not source and settings.mode in {MODE_PAPER, MODE_REAL}:
            source = "unknown"
        elif not source:
            source = IntradayDataSource.PROVIDED
        source_error = str(row.get("source_error") or row.get("quote_error") or "")
        source_status = str(row.get("source_status") or ("WARNING" if source_error else "OK")).upper()
        ema20_values = ema(close_values or [ltp], settings.ema20_period)
        ema50_values = ema(close_values or [ltp], settings.ema50_period)
        rsi_values = rsi(close_values or [ltp], settings.rsi_period)
        profile = calculate_volume_profile(candles) if settings.volume_profile_enabled else {"poc": 0, "vah": 0, "val": 0}
        liquidity = score_liquidity(ltp, row.get("depth") or {})
        news = sentiment_score_for_symbol(news_items, symbol)
        options = calculate_options_bias(symbol, payload) if settings.options_bias_enabled else {"bias": "Unavailable", "score": 0.0}
        rvol = relative_volume(candles, settings.volume_lookback)
        long_trap = detect_trap(candles, SIDE_LONG, rvol, liquidity["spread_pct"])
        short_trap = detect_trap(candles, SIDE_SHORT, rvol, liquidity["spread_pct"])
        trap_probe = long_trap if float(long_trap.get("trap_score") or 0) >= float(short_trap.get("trap_score") or 0) else short_trap
        price_structure = _price_structure(candles)
        snapshot = StockSnapshot(
            symbol=symbol,
            exchange=exchange,
            ltp=ltp,
            open=float(latest.get("open") or ltp),
            high=float(latest.get("high") or ltp),
            low=float(latest.get("low") or ltp),
            close=float(latest.get("close") or ltp),
            volume=float(latest.get("volume") or row.get("volume") or 0),
            candle_interval=settings.candle_interval,
            candles_available=len(candles),
            last_candle_time=row.get("last_candle_time") or candle_timestamp(latest),
            data_source=source,
            source_status=source_status,
            source_error=source_error,
            fetched_at=str(row.get("fetched_at") or ""),
            quote_timestamp=str(row.get("quote_timestamp") or row.get("last_tick_time") or ""),
            data_mode=str(row.get("data_mode") or "candle_polling"),
            ema20=ema20_values[-1] if ema20_values else ltp,
            ema50=ema50_values[-1] if ema50_values else ltp,
            rsi=rsi_values[-1] if rsi_values else 50.0,
            vwap=calculate_vwap(candles) if settings.vwap_enabled else 0.0,
            poc=profile.get("poc") or 0.0,
            vah=profile.get("vah") or 0.0,
            val=profile.get("val") or 0.0,
            relative_volume=rvol,
            spread=liquidity["spread"],
            spread_pct=liquidity["spread_pct"],
            bid_qty=liquidity["bid_qty"],
            ask_qty=liquidity["ask_qty"],
            depth_imbalance=liquidity["depth_imbalance"],
            liquidity_score=liquidity["liquidity_score"],
            trap_score=trap_probe["trap_score"],
            trap_warning=trap_probe["trap_warning"],
            news_score=news["score"],
            news_sentiment=news["sentiment"],
            options_bias_score=options["score"],
            options_bias=options["bias"],
        )
        snapshot.reason = {
            "news": news,
            "options": options,
            "trap": trap_probe,
            "traps": {"long": long_trap, "short": short_trap},
            "market_context": context_alignment(payload),
            "data_source": {
                "source": source,
                "source_status": source_status,
                "source_error": source_error,
                "fetched_at": row.get("fetched_at") or "",
                "quote_timestamp": row.get("quote_timestamp") or row.get("last_tick_time") or "",
                "data_mode": row.get("data_mode") or "candle_polling",
                "depth_source": row.get("depth_source") or "",
            },
            "price_structure": price_structure,
        }
        snapshot.reason["entry_structure"] = analyse_entry_structure(
            candles,
            snapshot,
            settings,
            ema20_values=ema20_values,
            ema50_values=ema50_values,
            rsi_values=rsi_values,
            traps={"long": long_trap, "short": short_trap},
        )
        return snapshot

    def approve_entry(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_running()
        payload = payload or {}
        if not self.pending_signal:
            raise ValueError("No pending intraday entry is waiting for approval.")
        signal = self.pending_signal
        signal.approved_by_user = True
        signal.final_decision = "APPROVED"
        if payload.get("quantity") not in ("", None):
            signal.score_breakdown["user_quantity_override"] = int(float(payload["quantity"]))
        self._place_entry(signal)
        self.pending_signal = None
        self.last_signal = signal
        self.database.save_signal(signal.to_dict())
        return self.status_payload()

    def reject_entry(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        if not self.pending_signal:
            raise ValueError("No pending intraday entry is waiting for rejection.")
        self.pending_signal.final_decision = "REJECTED"
        self.pending_signal.score_breakdown["rejected_reason"] = payload.get("reason") or "Rejected by user"
        self.database.save_signal(self.pending_signal.to_dict())
        if self.audit:
            self.audit.log("INFO", "approval", "entry_rejected", self.pending_signal.to_dict())
        self.last_signal = self.pending_signal
        self.pending_signal = None
        return self.status_payload()

    def _place_entry(self, signal: Signal) -> None:
        settings = self.settings
        assert settings is not None
        try:
            self.mode_manager.assert_order_allowed(settings.mode)
        except ValueError as exc:
            signal.blockers = _dedupe_signal_blockers(list(signal.blockers) + [str(exc)])
            signal.final_decision = "BLOCKED"
            return
        single_trade_blockers = self._single_trade_blockers()
        if single_trade_blockers:
            signal.blockers = _dedupe_signal_blockers(list(signal.blockers) + single_trade_blockers)
            signal.final_decision = "BLOCKED"
            return
        hard_gate_blockers = self._trade_allowed_blockers(signal, {}, now=datetime.now())
        if hard_gate_blockers:
            signal.blockers = _dedupe_signal_blockers(list(signal.blockers) + hard_gate_blockers)
            signal.final_decision = "BLOCKED"
            return
        real_blockers = self._real_execution_blockers(signal)
        if real_blockers:
            signal.blockers = _dedupe_signal_blockers(list(signal.blockers) + real_blockers)
            signal.final_decision = "BLOCKED"
            return
        blockers = self.risk.pre_trade_blockers(signal) if self.risk else []
        if blockers:
            signal.blockers = blockers
            signal.final_decision = "BLOCKED"
            return
        quantity = signal.score_breakdown.get("user_quantity_override") or None
        quantity = int(quantity) if quantity not in ("", None) else None
        result = self.lifecycle.submit_entry(signal, quantity) if self.lifecycle else {"ok": False, "message": "Order lifecycle unavailable."}
        signal.margin = result.get("margin") or {}
        if not result.get("ok"):
            signal.blockers.append(result.get("message") or "Order blocked before broker send.")
            signal.final_decision = "BLOCKED"
            if self.audit:
                self.audit.log("WARNING", "orders", "entry_order_blocked", result)
            return
        if self.risk:
            self.risk.mark_order_attempt(signal.symbol)
        signal.final_decision = "ORDER_SENT"
        if self.audit:
            self.audit.log("INFO", "orders", "entry_order_sent", result)

    def _single_trade_blockers(self) -> list[str]:
        blockers = []
        settings = self.settings
        max_open = int(getattr(settings, "max_open_positions", 1) or 1)
        open_count = self.lifecycle.open_trade_count() if self.lifecycle and hasattr(self.lifecycle, "open_trade_count") else 0
        pending_count = self.lifecycle.pending_entry_count() if self.lifecycle and hasattr(self.lifecycle, "pending_entry_count") else 0
        if open_count and max_open <= 1:
            blockers.append("Another trade is already active.")
        elif open_count >= max_open:
            blockers.append("Max open positions reached.")
        if open_count + pending_count >= max_open:
            blockers.append("An entry LIMIT order is already pending or max open slots are full.")
        return blockers

    def _position_size(self, signal: Signal) -> int:
        settings = self.settings
        assert settings is not None
        risk_per_share = max(abs(signal.entry_price - signal.stoploss), 0.05)
        funds = self.broker.get_funds() if self.broker else {"available": settings.paper_starting_balance}
        available = float(funds.get("available") or settings.paper_starting_balance)
        risk_budget = min(settings.max_loss_per_trade, available * settings.risk_per_trade_pct / 100)
        quantity_by_risk = max(1, int(risk_budget // risk_per_share))
        quantity_by_capital = max(1, int(settings.max_capital_per_trade // max(signal.entry_price, 0.05)))
        return max(1, min(quantity_by_risk, quantity_by_capital))

    def _sync_risk_from_lifecycle(self) -> None:
        if not self.risk or not self.lifecycle:
            return
        self.risk.state.open_positions = self.lifecycle.open_trade_count() if hasattr(self.lifecycle, "open_trade_count") else 0
        self.risk.state.realized_pnl = float(self.lifecycle.session_realized_pnl or 0)
        self.risk.state.unrealized_pnl = float(self.lifecycle.session_unrealized_pnl or 0)

    def _trade_allowed_blockers(self, signal: Signal, payload: dict[str, Any] | None, now: datetime | None = None) -> list[str]:
        settings = self.settings
        if not settings:
            return []
        now = now or datetime.now()
        blackout = event_blackout_blockers(settings, payload or {}, now=now)
        if not blackout and not payload:
            blackout = list(self.last_event_blackout_blockers)
        snapshot = self._snapshot_for_signal(signal)
        instrument = self.instrument_rows.get(f"{str(signal.exchange or 'NSE').upper()}:{str(signal.symbol or '').upper()}") or {}
        blockers = blackout + signal_eligibility_blockers(settings, signal, snapshot, instrument)
        if blockers:
            signal.score_breakdown["hard_gates"] = {
                "event_blackout": blackout,
                "eligibility": [item for item in blockers if item not in blackout],
                "trade_allowed": False,
            }
        else:
            signal.score_breakdown["hard_gates"] = {"event_blackout": [], "eligibility": [], "trade_allowed": True}
        return _dedupe_signal_blockers(blockers)

    def _snapshot_for_signal(self, signal: Signal):
        for snapshot in self.snapshots:
            if snapshot.symbol == signal.symbol and snapshot.exchange == signal.exchange:
                return snapshot
        return None

    def kill_switch(self) -> dict[str, Any]:
        if self.risk:
            self.risk.kill()
        report = self._real_emergency_kill_switch() if self.settings and self.settings.mode == MODE_REAL else {"attempted": False, "mode": getattr(self.settings, "mode", "")}
        self.last_kill_switch_report = report
        self.status = SESSION_STATUS_KILLED
        if report.get("attempted") and (report.get("errors") or not report.get("flat_verified")):
            self.mode_manager.error_lock("Kill switch activated; real account flat verification is uncertain.")
        else:
            self.mode_manager.error_lock("Kill switch activated; trading is blocked until a new session is started.")
        if self.audit:
            self.audit.log("CRITICAL", "risk", "kill_switch", {"message": "Intraday kill switch activated", "report": report})
        if self.session_id:
            self.database.update_session(self.session_id, {"status": self.status})
        self._stop_stock_websocket("Kill switch activated.")
        self.stock_live_feed.stop("Kill switch activated.")
        return self.status_payload()

    def _real_emergency_kill_switch(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "attempted": True,
            "mode": MODE_REAL,
            "cancelled_orders": [],
            "square_off_orders": [],
            "open_orders_after": [],
            "positions_after": [],
            "flat_verified": False,
            "errors": [],
        }
        selected = {stock.symbol.upper() for stock in (self.settings.stocks if self.settings else [])}
        try:
            orders = self.broker.get_orders() if self.broker else []
        except Exception as exc:
            report["errors"].append(f"Could not fetch real order book: {exc}")
            orders = []
        for order in orders or []:
            if not _is_selected_intraday_order(order, selected):
                continue
            try:
                response = self.broker.cancel_order(str(order.get("order_id") or order.get("id") or ""))
                report["cancelled_orders"].append({"order": order, "response": response})
            except Exception as exc:
                report["errors"].append(f"Cancel failed for {order.get('order_id') or order.get('id')}: {exc}")
        positions = self._real_positions_rows(report)
        for position in positions:
            symbol = str(position.get("tradingsymbol") or position.get("symbol") or "").upper()
            qty = _position_quantity(position)
            if symbol not in selected or qty == 0:
                continue
            if str(position.get("product") or "MIS").upper() != "MIS":
                continue
            try:
                request = emergency_exit_order(
                    symbol,
                    qty,
                    exchange=str(position.get("exchange") or "NSE").upper(),
                    session_id=self.session_id,
                    settings=self.settings,
                    ltp=position.get("last_price") or position.get("average_price") or position.get("close"),
                    lower_circuit_limit=position.get("lower_circuit_limit"),
                    upper_circuit_limit=position.get("upper_circuit_limit"),
                )
                if hasattr(self.broker, "place_emergency_order"):
                    response = self.broker.place_emergency_order(request)
                else:
                    response = self.broker.place_order(request)
                report["square_off_orders"].append({"position": position, "request": request.to_dict(), "response": response})
            except Exception as exc:
                report["errors"].append(f"Square-off failed for {symbol}: {exc}")
        after_positions = self._real_positions_rows(report)
        report["positions_after"] = after_positions
        try:
            after_orders = self.broker.get_orders() if self.broker else []
            report["open_orders_after"] = [order for order in after_orders if _is_selected_intraday_order(order, selected)]
        except Exception as exc:
            report["errors"].append(f"Could not verify order book after kill switch: {exc}")
        report["flat_verified"] = not report["open_orders_after"] and all(
            _position_quantity(row) == 0
            or str(row.get("tradingsymbol") or row.get("symbol") or "").upper() not in selected
            or str(row.get("product") or "MIS").upper() != "MIS"
            for row in after_positions
        )
        if self.broker and hasattr(self.broker, "real_order_pause_reason"):
            self.broker.real_order_pause_reason = (
                "Kill switch active; account flat verified."
                if report["flat_verified"] and not report["errors"]
                else "Kill switch active; real account flat verification is uncertain."
            )
        return report

    def _real_positions_rows(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            positions = self.broker.get_positions() if self.broker else {}
        except Exception as exc:
            report["errors"].append(f"Could not fetch real positions: {exc}")
            return []
        if isinstance(positions, dict):
            rows = list(positions.get("net") or []) + list(positions.get("day") or [])
        elif isinstance(positions, list):
            rows = positions
        else:
            rows = []
        deduped = {}
        for row in rows:
            key = (row.get("exchange") or "NSE", row.get("tradingsymbol") or row.get("symbol") or "", row.get("product") or "MIS")
            deduped[key] = row
        return list(deduped.values())

    def stop_session(self) -> dict[str, Any]:
        self._require_session()
        settings = self.settings
        assert settings is not None
        self.status = SESSION_STATUS_STOPPED
        if settings.mode in {MODE_PAPER, MODE_BACKTEST, MODE_REPLAY} and hasattr(self.paper_account, "settle_pending_session_profit"):
            self.paper_account.settle_pending_session_profit()
        self.last_export_path = export_session(self.database, self.session_id, settings.mode, self.base_result_folder)
        self.database.update_session(self.session_id, {
            "status": self.status,
            "end_time": datetime.now().isoformat(timespec="seconds"),
            "ending_balance": self.broker.get_funds().get("available") if self.broker else 0,
        })
        if self.audit:
            self.audit.log("INFO", "session", "session_stopped", {"export_path": self.last_export_path})
        self.mode_manager.stop()
        self._stop_stock_websocket("Session stopped.")
        self.stock_live_feed.stop("Session stopped.")
        return self.status_payload()

    def status_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "session_id": self.session_id,
            "db_path": self.db_path,
            "settings_locked": bool(self.settings and self.status == SESSION_STATUS_RUNNING),
            "mode_state": self.mode_manager.to_dict(),
            "settings": self.settings.locked_dict() if self.settings else None,
            "snapshots": [snapshot.to_dict() for snapshot in self.snapshots],
            "last_market_data_symbols": sorted(self.last_market_data.keys()),
            "data_source_policy": dict(self.current_data_source_policy),
            "data_source_status": dict(self.last_data_source_status),
            "stock_data_health": dict(self.last_stock_data_health),
            "stock_live_feed": self.stock_live_feed.snapshot(),
            "profile_policy": dict(getattr(self.settings, "profile_policy", {}) if self.settings else {}),
            "last_data_fetch_error": self.last_data_fetch_error,
            "latest_news": list(self.latest_news),
            "latest_news_status": dict(self.latest_news_status),
            "event_blackout_blockers": list(self.last_event_blackout_blockers),
            "pending_signal": self.pending_signal.to_dict() if self.pending_signal else None,
            "last_signal": self.last_signal.to_dict() if self.last_signal else None,
            "risk_state": self.risk.state.__dict__ if self.risk else None,
            "funds": dict(self.cached_funds or {}) if self.cached_funds is not None else None,
            "cached_funds_at": self.cached_funds_at,
            "cached_broker_health": dict(self.cached_broker_health),
            "last_broker_error": self.last_broker_error,
            "paper_account": self.paper_account.snapshot(),
            "active_trade": self.lifecycle.active_trade if self.lifecycle else None,
            "active_trades": list(self.lifecycle.active_trades.values()) if self.lifecycle else [],
            "order_history": self.lifecycle.order_history if self.lifecycle else [],
            "session_pnl": {
                "realized": self.lifecycle.session_realized_pnl if self.lifecycle else 0.0,
                "unrealized": self.lifecycle.session_unrealized_pnl if self.lifecycle else 0.0,
                "total": (self.lifecycle.session_realized_pnl + self.lifecycle.session_unrealized_pnl) if self.lifecycle else 0.0,
            },
            "export_path": self.last_export_path,
            "fii_dii_upload": upload_status(self.uploaded_fii_dii),
            "kill_switch_report": dict(self.last_kill_switch_report),
        }

    def paper_account_status(self) -> dict[str, Any]:
        return self.paper_account.snapshot()

    def _refresh_cached_funds(self) -> dict[str, Any]:
        if not self.broker:
            self.cached_funds = None
            self.cached_funds_at = ""
            return {}
        try:
            funds = dict(self.broker.get_funds() or {})
            self.cached_funds = funds
            self.cached_funds_at = datetime.now().isoformat(timespec="seconds")
            self.cached_broker_health = {"ok": True, "updated_at": self.cached_funds_at}
            self.last_broker_error = ""
            return funds
        except Exception as exc:
            self.last_broker_error = str(exc)
            self.cached_broker_health = {"ok": False, "error": self.last_broker_error, "updated_at": datetime.now().isoformat(timespec="seconds")}
            return dict(self.cached_funds or {})

    def update_paper_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.status == SESSION_STATUS_RUNNING:
            raise ValueError("Stop the intraday session before changing the paper account balance.")
        balance = float(payload.get("balance") or payload.get("paper_starting_balance") or 0)
        if balance <= 0:
            raise ValueError("Paper balance must be greater than zero.")
        if payload.get("reset", True):
            snapshot = self.paper_account.reset(balance)
        else:
            snapshot = self.paper_account.set_balance(balance)
        return {"paper_account": self.paper_account.snapshot(), "raw": snapshot}

    def upload_fii_dii_csv(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = str(payload.get("csv_file") or payload.get("file") or payload.get("fii_dii_file") or "").strip()
        scope_hint = str(payload.get("scope_hint") or "").strip()
        if path:
            parsed = parse_intraday_fii_dii_csv_file(path, scope_hint=scope_hint)
        else:
            csv_text = str(payload.get("csv_text") or "")
            parsed = parse_intraday_fii_dii_csv_text(csv_text, file_name=str(payload.get("file_name") or "fii_dii.csv"), scope_hint=scope_hint)
        self.uploaded_fii_dii = parsed
        return {"fii_dii_upload": upload_status(parsed), "parsed": parsed}

    def run_paper_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        return PaperBacktester(self).run(payload)

    def _require_fii_dii_upload_for_live_mode(self, mode: str) -> None:
        if str(mode).upper() not in {MODE_PAPER, MODE_REAL}:
            return
        status = upload_status(self.uploaded_fii_dii)
        if not status["valid"]:
            raise ValueError(
                "Upload valid NSE FII/DII CSV before starting PAPER or REAL intraday session. "
                "Python/NSE auto-fetch is disabled; manual CSV upload is required."
            )

    def _with_uploaded_fii_dii(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.uploaded_fii_dii or payload.get("fii_dii") or payload.get("fii_dii_cue"):
            return payload
        return {
            **payload,
            "fii_dii": {
                "fii_net": self.uploaded_fii_dii.get("fii_net"),
                "dii_net": self.uploaded_fii_dii.get("dii_net"),
                "data_date": self.uploaded_fii_dii.get("data_date"),
                "source": self.uploaded_fii_dii.get("source"),
                "fetch_mode": self.uploaded_fii_dii.get("fetch_mode"),
                "scope": self.uploaded_fii_dii.get("scope"),
            },
        }

    def _validate_stocks_against_broker(self, settings: IntradaySettings) -> dict[str, dict[str, Any]]:
        client = self._client_for_mode(settings.mode)
        try:
            if settings.mode == MODE_PAPER and client:
                instruments = _client_instruments(client)
            elif settings.mode == MODE_REAL and client:
                instruments = _client_instruments(client)
            else:
                instruments = self.broker.get_instruments() if self.broker else []
        except Exception as exc:
            if settings.mode == MODE_REAL:
                raise ValueError(f"Real intraday stock validation failed before session start: {exc}") from exc
            if settings.mode == MODE_PAPER and not settings.allow_simulated_fallback:
                raise ValueError(f"Paper Data stock validation failed before session start: {exc}") from exc
            instruments = []
        if settings.mode == MODE_REAL and not instruments:
            raise ValueError("Real intraday stock validation failed before session start: instrument list is unavailable.")
        if settings.mode == MODE_PAPER and client and not instruments and not settings.allow_simulated_fallback:
            raise ValueError("Paper Data stock validation failed before session start: instrument list is unavailable.")
        if not instruments:
            return {}
        by_key = {}
        symbols = {}
        for row in instruments:
            exchange = str(row.get("exchange") or row.get("segment") or "").split(":")[0].upper()
            symbol = str(row.get("tradingsymbol") or row.get("symbol") or "").upper()
            if not exchange or not symbol:
                continue
            by_key[f"{exchange}:{symbol}"] = row
            symbols.setdefault(symbol, []).append(f"{exchange}:{symbol}")
        invalid = []
        for stock in settings.stocks:
            if stock.key not in by_key:
                suggestions = symbols.get(stock.symbol) or _closest_symbols(stock.symbol, by_key.keys())
                invalid.append(f"{stock.key} is invalid or unavailable. Suggestions: {', '.join(suggestions[:5]) or 'none'}")
                continue
            if settings.mode in {MODE_PAPER, MODE_REAL} and self.current_data_source_policy.get("requires_fetch") and by_key[stock.key].get("instrument_token") in ("", None):
                invalid.append(f"Instrument token unavailable for {stock.key}. Cannot fetch Zerodha {'Paper' if settings.mode == MODE_PAPER else 'Real'} Data.")
        if invalid:
            raise ValueError("Stock validation failed before session start: " + " | ".join(invalid))
        return by_key

    def _real_execution_blockers(self, signal: Signal) -> list[str]:
        settings = self.settings
        if not settings or settings.mode != MODE_REAL:
            return []
        row = self.last_market_data.get(signal.symbol) if isinstance(self.last_market_data, dict) else {}
        return real_execution_blockers(signal, row or {}, settings, broker=self.broker, now=datetime.now())

    def _client_for_mode(self, mode: str):
        mode = str(mode or "").upper()
        if mode == MODE_REAL:
            return self.zerodha_client_provider(MODE_REAL) or self.zerodha_client_provider("LIVE")
        if mode == MODE_PAPER:
            return self.zerodha_client_provider(MODE_PAPER) or self.zerodha_client_provider("PAPER")
        return None

    def _stock_instruments_for_live_feed(self, settings: IntradaySettings) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for stock in settings.stocks:
            row = dict(self.instrument_rows.get(stock.key) or {})
            if not row:
                continue
            row.setdefault("symbol", stock.symbol)
            row.setdefault("tradingsymbol", stock.symbol)
            row.setdefault("exchange", stock.exchange)
            rows.append(row)
        return rows

    def _start_stock_websocket(self, settings: IntradaySettings) -> None:
        if settings.mode not in {MODE_PAPER, MODE_REAL} or not bool(getattr(settings, "websocket_primary_enabled", True)):
            return
        client = self._client_for_mode(settings.mode)
        if not client:
            self.stock_live_feed.mark_websocket_error("Zerodha websocket client is not connected.")
            return
        tokens = [
            int(_float(row.get("instrument_token") or row.get("token") or 0))
            for row in self._stock_instruments_for_live_feed(settings)
            if int(_float(row.get("instrument_token") or row.get("token") or 0)) > 0
        ]
        tokens = list(dict.fromkeys(tokens))
        if not tokens:
            self.stock_live_feed.mark_websocket_error("No selected stock instrument tokens are available for websocket subscription.")
            return
        if len(tokens) > 3000:
            self.stock_live_feed.mark_websocket_error("Intraday stock websocket token count exceeds Zerodha 3000-instrument limit.")
            return
        if not hasattr(client, "start_named_ticker") and not hasattr(client, "start_ticker"):
            self.stock_live_feed.mark_websocket_error("Connected Zerodha client does not expose websocket ticker; candle polling fallback remains active.")
            return

        name = f"intraday_{settings.mode.lower()}"
        if self._stock_ws_name == name and self._stock_ws_tokens == tuple(tokens):
            return
        self._stop_stock_websocket("resubscribe")

        def on_ticks(ticks):
            self._on_stock_websocket_ticks(ticks)

        def on_connect(_response=None):
            self.stock_live_feed.mark_websocket_connected(True)
            self._stock_ws_last_error = ""

        def on_close(code=None, reason=""):
            message = f"Stock websocket closed: {code or ''} {reason or ''}".strip()
            self._stock_ws_last_error = message
            self.stock_live_feed.mark_websocket_connected(False, message)

        def on_error(code=None, reason=""):
            message = f"Stock websocket error: {code or ''} {reason or ''}".strip()
            self._stock_ws_last_error = message
            self.stock_live_feed.mark_websocket_error(message)

        try:
            if hasattr(client, "start_named_ticker"):
                client.start_named_ticker(
                    name,
                    tokens,
                    on_ticks=on_ticks,
                    on_connect=on_connect,
                    on_close=on_close,
                    on_error=on_error,
                )
            else:
                client.start_ticker(
                    tokens,
                    on_ticks=on_ticks,
                    on_connect=on_connect,
                    on_close=on_close,
                    on_error=on_error,
                )
        except Exception as exc:
            message = f"Intraday stock websocket startup failed: {exc}"
            self._stock_ws_last_error = message
            self.stock_live_feed.mark_websocket_error(message)
            return

        self._stock_ws_name = name
        self._stock_ws_mode = settings.mode
        self._stock_ws_tokens = tuple(tokens)
        if not self.stock_live_feed.websocket_connected:
            self.stock_live_feed.mark_websocket_connected(True)

    def _stop_stock_websocket(self, reason: str = "") -> None:
        if not self._stock_ws_name and not self._stock_ws_tokens:
            return
        client = self._client_for_mode(self._stock_ws_mode)
        try:
            if client and self._stock_ws_name and hasattr(client, "stop_named_ticker"):
                client.stop_named_ticker(self._stock_ws_name)
            elif client and hasattr(client, "stop_ticker"):
                client.stop_ticker()
        except Exception as exc:
            self._stock_ws_last_error = f"Intraday stock websocket stop failed: {exc}"
        finally:
            self._stock_ws_name = ""
            self._stock_ws_mode = ""
            self._stock_ws_tokens = ()
            self.stock_live_feed.mark_websocket_connected(False, reason)

    def _on_stock_websocket_ticks(self, ticks: Any) -> None:
        rows = list(ticks or []) if isinstance(ticks, (list, tuple)) else [ticks]
        if not rows:
            return
        self.stock_live_feed.mark_websocket_connected(True)
        for tick in rows:
            if isinstance(tick, dict):
                self.stock_live_feed.on_tick_by_token(tick)

    def _data_policy_for_payload(self, settings: IntradaySettings, payload: dict[str, Any]) -> dict[str, Any]:
        return resolve_intraday_data_source(
            settings.mode,
            payload,
            paper_connected=bool(self._client_for_mode(MODE_PAPER)),
            live_connected=bool(self._client_for_mode(MODE_REAL)),
            settings=settings,
        )

    def _market_data_for_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings
        if not settings:
            return {}
        if payload.get("market_data"):
            policy = self._data_policy_for_payload(settings, payload)
            if not policy.get("allowed"):
                raise ValueError("; ".join(policy.get("blockers") or [policy.get("reason") or "Provided market data is not allowed."]))
            self.last_data_source_status = dict(policy)
            return self._label_market_data(payload.get("market_data") or {}, policy)
        return self._load_session_candles(payload)

    def _load_session_candles(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings
        if not settings:
            return {}
        policy = self._data_policy_for_payload(settings, payload)
        if not policy.get("allowed"):
            self.last_data_source_status = dict(policy)
            raise ValueError("; ".join(policy.get("blockers") or [policy.get("reason") or "Intraday market data source is unavailable."]))
        now = datetime.now()
        from_time, close_time = market_open_close(now.date().isoformat())
        to_time = min(max(now, from_time), close_time)
        client = self._client_for_mode(settings.mode)
        stocks = [stock.__dict__ for stock in settings.stocks]
        websocket_data, websocket_warnings = self._stock_websocket_market_data(policy, now)
        if self._websocket_market_data_complete(websocket_data):
            status = {
                **policy,
                "status": "OK",
                "data_mode": "websocket_tick_candles",
                "source_error": "",
                "warnings": list(dict.fromkeys(list(policy.get("warnings") or []) + websocket_warnings)),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
            self.last_data_fetch_error = ""
            self.last_data_source_status = status
            return self._label_market_data(websocket_data, status)
        if policy.get("requires_fetch"):
            if not client:
                self.last_data_source_status = {**policy, "status": "ERROR", "source_error": policy.get("reason") or "Zerodha client is not connected."}
                raise ValueError(policy.get("reason") or "Zerodha client is not connected.")
            if (
                bool(getattr(settings, "websocket_primary_enabled", True))
                and not bool(getattr(settings, "historical_bootstrap_on_start", True))
                and not self._websocket_market_data_complete(websocket_data)
            ):
                error = "; ".join(websocket_warnings) or "Websocket tick candles are not ready and historical bootstrap is disabled."
                self.last_data_fetch_error = error
                self.last_data_source_status = {
                    **policy,
                    "status": "ERROR",
                    "data_mode": "websocket_tick_candles_pending",
                    "source_error": error,
                    "warnings": list(dict.fromkeys(list(policy.get("warnings") or []) + websocket_warnings)),
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                }
                raise ValueError(error)
            data = fetch_zerodha_stock_candles(
                client,
                stocks,
                from_time,
                to_time,
                interval=settings.candle_interval,
                source=policy["source"],
                include_live_quote=self._should_use_quote_snapshot_fallback(websocket_data),
            )
            usable, errors = self._usable_fetch_data(data)
            if usable:
                self._seed_stock_live_feed_from_fetch(usable)
                websocket_data, websocket_warnings = self._stock_websocket_market_data(policy, now)
                if self._websocket_market_data_complete(websocket_data):
                    status = {
                        **policy,
                        "status": "OK",
                        "data_mode": "websocket_tick_candles",
                        "source_error": "",
                        "warnings": list(dict.fromkeys(list(policy.get("warnings") or []) + websocket_warnings)),
                        "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    self.last_data_fetch_error = ""
                    self.last_data_source_status = status
                    return self._label_market_data(websocket_data, status)
                status = "WARNING" if errors else "OK"
                source_errors = list(errors)
                if bool(getattr(settings, "websocket_primary_enabled", True)) and websocket_warnings:
                    source_errors.extend(websocket_warnings)
                    status = "WARNING"
                self.last_data_fetch_error = "; ".join(source_errors)
                self.last_data_source_status = {
                    **policy,
                    "status": status,
                    "data_mode": "candle_polling_bootstrap_or_fallback",
                    "source_error": self.last_data_fetch_error,
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "warnings": list(dict.fromkeys(list(policy.get("warnings") or []) + websocket_warnings)),
                }
                return self._label_market_data(usable, self.last_data_source_status)
            error = "; ".join(errors) or f"Could not fetch Zerodha {'Real' if settings.mode == MODE_REAL else 'Paper'} Data."
            self.last_data_fetch_error = error
            if settings.mode == MODE_PAPER and settings.allow_simulated_fallback:
                policy = {
                    **policy,
                    "source": IntradayDataSource.SIMULATED_FALLBACK,
                    "status": "WARNING",
                    "reason": "Zerodha Paper Data fetch failed; simulated fallback is explicitly enabled.",
                    "source_error": error,
                    "allow_simulated": True,
                    "warnings": list(policy.get("warnings") or []) + [error],
                }
                self.last_data_source_status = policy
                return self._simulated_market_data(stocks, now, policy)
            self.last_data_source_status = {**policy, "status": "ERROR", "source_error": error}
            if settings.mode == MODE_REAL:
                raise ValueError(f"Could not fetch Zerodha Real Data. {error}")
            raise ValueError(f"Could not fetch Zerodha Paper Data for selected symbols. Simulated fallback is disabled. {error}")
        if settings.mode in {MODE_PAPER, MODE_BACKTEST, MODE_REPLAY}:
            if settings.mode == MODE_PAPER and not policy.get("allow_simulated"):
                self.last_data_source_status = {**policy, "status": "ERROR"}
                raise ValueError("Paper Data Zerodha is not connected. Connect Paper Data Zerodha or enable simulated fallback for testing.")
            self.last_data_source_status = dict(policy)
            return self._simulated_market_data(stocks, now, policy)
        return {}

    def _stock_websocket_market_data(self, policy: dict[str, Any], now: datetime) -> tuple[dict[str, Any], list[str]]:
        settings = self.settings
        if not settings or settings.mode not in {MODE_PAPER, MODE_REAL}:
            return {}, []
        if not bool(getattr(settings, "websocket_primary_enabled", True)):
            return {}, []
        if not self.stock_live_feed.running:
            return {}, ["Stock websocket feed is not running."]
        include_current = bool(getattr(settings, "allow_forming_candle_entry", False))
        max_age = max(0.1, float(getattr(settings, "max_tick_age_seconds", 3.0) or 3.0))
        rows: dict[str, Any] = {}
        warnings: list[str] = []
        now_epoch = now.timestamp()
        for stock in settings.stocks:
            symbol = stock.symbol.upper()
            tick = self.stock_live_feed.latest_tick(symbol)
            if not tick:
                warnings.append(f"{symbol} websocket tick is not ready.")
                continue
            age = self.stock_live_feed.tick_age_seconds(symbol, now_epoch=now_epoch)
            if age > max_age:
                warnings.append(f"{symbol} websocket tick is stale ({age:.1f}s).")
                continue
            ltp = _float(tick.get("last_price"), tick.get("ltp") or tick.get("close"))
            if ltp <= 0:
                warnings.append(f"{symbol} websocket tick has no valid LTP.")
                continue
            candles = self.stock_live_feed.candles(symbol, include_current=include_current)
            if not candles:
                warnings.append(f"{symbol} websocket tick is live but completed tick-built candles are not ready.")
            depth = tick.get("depth") if isinstance(tick.get("depth"), dict) else {}
            timestamp = str(
                tick.get("timestamp")
                or tick.get("exchange_timestamp")
                or tick.get("last_trade_time")
                or tick.get("received_at")
                or now.isoformat(timespec="seconds")
            )
            rows[symbol] = {
                "ltp": ltp,
                "candles": candles,
                "full_candles": candles,
                "future_candles": [],
                "depth": depth or depth_from_ltp(ltp),
                "depth_source": "zerodha_websocket_full_tick" if depth else "synthetic_from_websocket_ltp",
                "source": policy.get("source") or IntradayDataSource.UNAVAILABLE,
                "source_status": "OK",
                "source_error": "",
                "data_mode": "websocket_tick_candles",
                "interval": settings.candle_interval,
                "instrument_token": tick.get("instrument_token") or self.stock_live_feed.token_by_symbol.get(symbol),
                "ohlc": tick.get("ohlc") or {},
                "lower_circuit_limit": tick.get("lower_circuit_limit"),
                "upper_circuit_limit": tick.get("upper_circuit_limit"),
                "last_tick_time": timestamp,
                "quote_timestamp": timestamp,
                "quote_error": "",
                "quote_snapshot_used": False,
                "last_candle_time": candle_timestamp(candles[-1]) if candles else "",
                "candles_available": len(candles),
                "fetched_at": now.isoformat(timespec="seconds"),
                "exchange": stock.exchange,
                "symbol": symbol,
                "age_seconds": age,
            }
        return rows, list(dict.fromkeys(warnings))

    def _websocket_market_data_complete(self, market_data: dict[str, Any]) -> bool:
        settings = self.settings
        if not settings or settings.mode not in {MODE_PAPER, MODE_REAL}:
            return False
        selected = {stock.symbol.upper() for stock in settings.stocks}
        if not selected or not selected.issubset(set(dict(market_data or {}).keys())):
            return False
        for symbol in selected:
            row = dict((market_data or {}).get(symbol) or {})
            if str(row.get("source_status") or "").upper() == "ERROR":
                return False
            if not row.get("candles"):
                return False
        return True

    def _should_use_quote_snapshot_fallback(self, websocket_data: dict[str, Any]) -> bool:
        settings = self.settings
        if not settings or not bool(getattr(settings, "quote_snapshot_fallback_enabled", True)):
            return False
        if not bool(getattr(settings, "websocket_primary_enabled", True)):
            return True
        selected = {stock.symbol.upper() for stock in settings.stocks}
        if selected and selected.issubset(set(dict(websocket_data or {}).keys())):
            return False
        return True

    def _seed_stock_live_feed_from_fetch(self, market_data: dict[str, Any]) -> None:
        if not self.stock_live_feed.running:
            return
        for symbol, row in dict(market_data or {}).items():
            candles = list((row or {}).get("candles") or [])
            if candles:
                self.stock_live_feed.seed_candles(symbol, candles)

    def _usable_fetch_data(self, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        usable = {}
        errors = []
        settings = self.settings
        selected = {stock.symbol for stock in (settings.stocks if settings else [])}
        for symbol in selected:
            row = dict((data or {}).get(symbol) or {})
            if row.get("source_status") == "ERROR" or not row.get("candles"):
                errors.append(row.get("source_error") or f"No candles returned for {symbol}.")
                continue
            usable[symbol] = row
            if row.get("quote_error"):
                errors.append(f"{symbol} quote warning: {row.get('quote_error')}")
        for symbol in selected - set((data or {}).keys()):
            errors.append(f"No data returned for {symbol}.")
        return usable, errors

    def _simulated_market_data(self, stocks: list[dict[str, Any]], now: datetime, policy: dict[str, Any]) -> dict[str, Any]:
        # Simulated fallback is never used silently for live paper trading.
        simulated = generate_stock_day(stocks, now.date().isoformat(), interval=self.settings.candle_interval if self.settings else "minute")
        cursor = min(max_candle_count(simulated) - 1, self.live_candle_cursor)
        self.live_candle_cursor = max(0, min(max_candle_count(simulated) - 1, cursor + 1))
        return self._label_market_data(market_slice(simulated, max(0, cursor), lookback=0), policy)

    def _label_market_data(self, market_data: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
        labeled = {}
        fetched_at = policy.get("fetched_at") or datetime.now().isoformat(timespec="seconds")
        for symbol, row in dict(market_data or {}).items():
            row = dict(row or {})
            source = policy.get("source") or IntradayDataSource.PROVIDED
            if source not in {IntradayDataSource.PROVIDED, IntradayDataSource.PROVIDED_TEST_DATA}:
                row["source"] = source
            else:
                row.setdefault("source", source)
            row.setdefault("source_status", policy.get("status") or "OK")
            row.setdefault("source_error", policy.get("source_error") or "")
            row.setdefault("data_mode", policy.get("data_mode") or "candle_polling")
            row.setdefault("fetched_at", fetched_at)
            if row.get("candles") and not row.get("last_candle_time"):
                row["last_candle_time"] = candle_timestamp(row["candles"][-1])
            row.setdefault("candles_available", len(row.get("candles") or []))
            labeled[str(symbol).upper()] = row
        return labeled

    def _payload_time(self, payload: dict[str, Any]) -> datetime | None:
        value = payload.get("replay_time") or payload.get("current_time")
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    def _pending_signal_expired(self, payload: dict[str, Any]) -> bool:
        if not self.pending_signal:
            return False
        now = self._payload_time(payload) or datetime.now()
        try:
            created = datetime.fromisoformat(str(self.pending_signal.created_at))
        except ValueError:
            return False
        return now - created >= timedelta(seconds=60)

    def _require_session(self) -> None:
        if not self.session_id or not self.settings:
            raise ValueError("Start an intraday session first.")

    def _require_running(self) -> None:
        self._require_session()
        if self.status != SESSION_STATUS_RUNNING:
            raise ValueError("Intraday session is not running.")


def _dedupe_signal_blockers(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _closest_symbols(symbol: str, keys) -> list[str]:
    symbol = str(symbol or "").upper()
    candidates = sorted(str(key).split(":", 1)[-1] for key in keys)
    prefix = [candidate for candidate in candidates if candidate.startswith(symbol[:3])]
    contains = [candidate for candidate in candidates if symbol and symbol in candidate and candidate not in prefix]
    return prefix[:5] + contains[: max(0, 5 - len(prefix))]


def _client_instruments(client) -> list[dict[str, Any]]:
    rows = []
    if not client:
        return rows
    for exchange in ("NSE", "BSE"):
        try:
            part = client.instruments(exchange)
        except TypeError:
            try:
                part = client.instruments()
            except Exception:
                part = []
        except Exception:
            part = []
        rows.extend(list(part or []))
    return rows


def _price_structure(candles: list[dict[str, Any]]) -> dict[str, Any]:
    completed = list(candles or [])
    if len(completed) > 1:
        completed = completed[:-1]
    recent = completed[-5:]
    ranges = [
        max(0.0, _float(row.get("high")) - _float(row.get("low")))
        for row in completed[-14:]
        if row.get("high") not in ("", None) and row.get("low") not in ("", None)
    ]
    return {
        "average_range_14": sum(ranges) / len(ranges) if ranges else 0.0,
        "previous_swing_low": min((_float(row.get("low")) for row in recent), default=0.0),
        "previous_swing_high": max((_float(row.get("high")) for row in recent), default=0.0),
    }


def _float(value: Any, default: Any = 0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        try:
            return float(default or 0)
        except (TypeError, ValueError):
            return 0.0


def _is_selected_intraday_order(order: dict[str, Any], selected_symbols: set[str]) -> bool:
    symbol = str(order.get("tradingsymbol") or order.get("symbol") or "").upper()
    if selected_symbols and symbol not in selected_symbols:
        return False
    status = str(order.get("status") or "").upper()
    if status in {"COMPLETE", "FILLED", "CANCELLED", "REJECTED"}:
        return False
    product = str(order.get("product") or "MIS").upper()
    return product == "MIS"


def _position_quantity(position: dict[str, Any]) -> int:
    for key in ("quantity", "net_quantity", "net_qty"):
        if position.get(key) not in ("", None):
            try:
                return int(float(position.get(key) or 0))
            except (TypeError, ValueError):
                pass
    buy_qty = position.get("buy_quantity")
    sell_qty = position.get("sell_quantity")
    try:
        return int(float(buy_qty or 0) - float(sell_qty or 0))
    except (TypeError, ValueError):
        return 0
