from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .constants import (
    BROKER_ZERODHA,
    DEFAULT_CANDLE_INTERVAL,
    DEFAULT_MIN_ENTRY_SCORE,
    DEFAULT_MIN_RISK_REWARD,
    DEFAULT_PAPER_BALANCE,
    EXCHANGE_NSE,
    MAX_STOCKS,
    MODE_BACKTEST,
    MODE_PAPER,
    MODE_REAL,
    MODE_REPLAY,
    ORDER_LIMIT_ONLY,
    ORDER_MARKET_ALLOWED,
    SIDE_LONG,
    SIDE_NO_TRADE,
    SIDE_SHORT,
    SUPPORTED_EXCHANGES,
    SUPPORTED_MODES,
)


def _bool(value: Any, default: bool = False) -> bool:
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _list_dicts(value: Any) -> list[dict[str, Any]]:
    if value in ("", None):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _list_strings(value: Any) -> list[str]:
    if value in ("", None):
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    if not isinstance(value, list):
        return []
    return [str(item or "").strip().upper() for item in value if str(item or "").strip()]


@dataclass(frozen=True)
class StockInput:
    symbol: str
    exchange: str = EXCHANGE_NSE

    @classmethod
    def from_value(cls, value: Any) -> "StockInput":
        if isinstance(value, dict):
            symbol = str(value.get("symbol") or value.get("tradingsymbol") or "").strip().upper()
            exchange = str(value.get("exchange") or EXCHANGE_NSE).strip().upper()
        else:
            text = str(value or "").strip().upper()
            if ":" in text:
                exchange, symbol = text.split(":", 1)
            else:
                symbol, exchange = text, EXCHANGE_NSE
        return cls(symbol=symbol, exchange=exchange)

    def validate(self) -> None:
        if not self.symbol:
            raise ValueError("Stock symbol is required.")
        if self.exchange not in SUPPORTED_EXCHANGES:
            raise ValueError(f"{self.symbol}: exchange must be NSE or BSE.")

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.symbol}"


@dataclass
class IntradaySettings:
    mode: str = MODE_PAPER
    broker: str = BROKER_ZERODHA
    stocks: list[StockInput] = field(default_factory=list)
    ask_permission_before_entry: bool = True
    order_mode: str = ORDER_LIMIT_ONLY
    allow_long: bool = True
    allow_short: bool = True
    paper_starting_balance: float = DEFAULT_PAPER_BALANCE
    max_daily_loss: float = 2500.0
    max_daily_profit: float = 5000.0
    max_trades_per_day: int = 5
    max_trades_per_stock: int = 1
    max_open_positions: int = 1
    max_capital_per_trade: float = 25000.0
    max_capital_allocation_pct: float = 25.0
    estimated_leverage: float = 5.0
    max_quantity_per_trade: int = 0
    max_loss_per_trade: float = 1000.0
    risk_per_trade_pct: float = 1.0
    minimum_risk_reward: float = DEFAULT_MIN_RISK_REWARD
    minimum_entry_score: float = DEFAULT_MIN_ENTRY_SCORE
    cooldown_after_trade_seconds: int = 300
    cooldown_after_loss_seconds: int = 600
    no_trade_first_minutes: int = 5
    stop_after_consecutive_losses: int = 2
    ema20_period: int = 20
    ema50_period: int = 50
    rsi_period: int = 14
    rsi_bullish_threshold: float = 55.0
    rsi_bearish_threshold: float = 45.0
    volume_lookback: int = 20
    relative_volume_threshold: float = 1.5
    vwap_enabled: bool = True
    volume_profile_enabled: bool = True
    candle_interval: str = DEFAULT_CANDLE_INTERVAL
    higher_timeframe_confirmation: bool = False
    entry_limit_offset: float = 0.0
    stoploss_buffer: float = 0.05
    target_buffer: float = 0.05
    limit_order_timeout_seconds: int = 30
    retry_limit_order: bool = False
    max_retries: int = 0
    chase_limit_order: bool = False
    max_chase_ticks: int = 0
    trailing_stop_enabled: bool = False
    active_trade_management_enabled: bool = True
    breakeven_sl_enabled: bool = True
    breakeven_trigger_r: float = 1.0
    breakeven_buffer: float = 0.05
    active_trailing_sl_enabled: bool = True
    trailing_method: str = "HYBRID"
    trail_activation_r: float = 1.2
    min_sl_modification_gap: float = 0.05
    min_seconds_between_sl_modifications: int = 15
    partial_exit_enabled: bool = False
    partial_exit_trigger_r: float = 1.0
    partial_exit_qty_pct: float = 50.0
    condition_exit_enabled: bool = True
    ask_confirmation_before_early_exit: bool = False
    early_exit_health_threshold: float = 35.0
    opposite_signal_exit_threshold: float = 82.0
    tighten_sl_health_threshold: float = 55.0
    dynamic_target_enabled: bool = True
    dynamic_target_health_threshold: float = 78.0
    target_extension_r: float = 0.5
    time_exit_enabled: bool = True
    max_minutes_in_trade: float = 0.0
    max_candles_without_progress: int = 0
    news_enabled: bool = True
    live_news_enabled: bool = False
    news_score_cap: float = 5.0
    event_blackout_enabled: bool = True
    event_blackout_windows: list[dict[str, Any]] = field(default_factory=list)
    blocked_symbols: list[str] = field(default_factory=list)
    require_mis_allowed: bool = True
    block_asm_gsm_t2t: bool = True
    min_liquidity_score: float = 35.0
    max_allowed_spread_pct: float = 0.35
    max_trap_score_for_entry: float = 80.0
    minimum_relative_volume_for_entry: float = 0.0
    options_bias_enabled: bool = False
    confirm_real_mode: bool = False
    auto_real_orders_confirmed: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "IntradaySettings":
        payload = payload or {}
        stocks = payload.get("stocks") or payload.get("symbols") or []
        if isinstance(stocks, str):
            stocks = [part.strip() for part in stocks.replace("\n", ",").split(",") if part.strip()]
        mode = str(payload.get("mode") or MODE_PAPER).strip().upper()
        if mode in {"INTRADAY STOCKS PAPER", "PAPER_TRADING"}:
            mode = MODE_PAPER
        if mode in {"INTRADAY STOCKS REAL", "LIVE", "REAL_TRADING"}:
            mode = MODE_REAL
        if mode in {"PAPER_BACKTEST", "BACKTEST_REPLAY", "BACKTEST / REPLAY"}:
            mode = MODE_BACKTEST
        order_mode = str(payload.get("order_mode") or ORDER_LIMIT_ONLY).strip().upper().replace("-", "_")
        if order_mode in {"MARKET", "MARKET_ALLOWED", "ALLOW_MARKET"}:
            order_mode = ORDER_MARKET_ALLOWED
        else:
            order_mode = ORDER_LIMIT_ONLY
        side = str(payload.get("side_permission") or payload.get("trading_side") or "BOTH").strip().upper()
        allow_long = _bool(payload.get("allow_long"), default=side in {"BOTH", "LONG", "LONG_ONLY"})
        allow_short = _bool(payload.get("allow_short"), default=side in {"BOTH", "SHORT", "SHORT_ONLY"})
        settings = cls(
            mode=mode,
            broker=str(payload.get("broker") or BROKER_ZERODHA).strip() or BROKER_ZERODHA,
            stocks=[StockInput.from_value(item) for item in stocks],
            ask_permission_before_entry=_bool(payload.get("ask_permission_before_entry"), True),
            order_mode=ORDER_LIMIT_ONLY,
            allow_long=allow_long,
            allow_short=allow_short,
            paper_starting_balance=_float(payload.get("paper_starting_balance") or payload.get("balance"), DEFAULT_PAPER_BALANCE),
            max_daily_loss=_float(payload.get("max_daily_loss"), 2500.0),
            max_daily_profit=_float(payload.get("max_daily_profit"), 5000.0),
            max_trades_per_day=_int(payload.get("max_trades_per_day"), 5),
            max_trades_per_stock=_int(payload.get("max_trades_per_stock"), 1),
            max_open_positions=_int(payload.get("max_open_positions"), 1),
            max_capital_per_trade=_float(payload.get("max_capital_per_trade"), 25000.0),
            max_capital_allocation_pct=_float(payload.get("max_capital_allocation_pct"), 25.0),
            estimated_leverage=_float(payload.get("estimated_leverage"), 5.0),
            max_quantity_per_trade=_int(payload.get("max_quantity_per_trade"), 0),
            max_loss_per_trade=_float(payload.get("max_loss_per_trade"), 1000.0),
            risk_per_trade_pct=_float(payload.get("risk_per_trade_pct"), 1.0),
            minimum_risk_reward=_float(payload.get("minimum_risk_reward") or payload.get("min_risk_reward"), DEFAULT_MIN_RISK_REWARD),
            minimum_entry_score=_float(payload.get("minimum_entry_score") or payload.get("min_entry_score"), DEFAULT_MIN_ENTRY_SCORE),
            cooldown_after_trade_seconds=_int(payload.get("cooldown_after_trade_seconds"), 300),
            cooldown_after_loss_seconds=_int(payload.get("cooldown_after_loss_seconds"), 600),
            no_trade_first_minutes=_int(payload.get("no_trade_first_minutes"), 5),
            stop_after_consecutive_losses=_int(payload.get("stop_after_consecutive_losses"), 2),
            ema20_period=_int(payload.get("ema20_period"), 20),
            ema50_period=_int(payload.get("ema50_period"), 50),
            rsi_period=_int(payload.get("rsi_period"), 14),
            rsi_bullish_threshold=_float(payload.get("rsi_bullish_threshold"), 55.0),
            rsi_bearish_threshold=_float(payload.get("rsi_bearish_threshold"), 45.0),
            volume_lookback=_int(payload.get("volume_lookback"), 20),
            relative_volume_threshold=_float(payload.get("relative_volume_threshold"), 1.5),
            vwap_enabled=_bool(payload.get("vwap_enabled"), True),
            volume_profile_enabled=_bool(payload.get("volume_profile_enabled"), True),
            candle_interval=str(payload.get("candle_interval") or DEFAULT_CANDLE_INTERVAL).strip(),
            higher_timeframe_confirmation=_bool(payload.get("higher_timeframe_confirmation"), False),
            entry_limit_offset=_float(payload.get("entry_limit_offset"), 0.0),
            stoploss_buffer=_float(payload.get("stoploss_buffer"), 0.05),
            target_buffer=_float(payload.get("target_buffer"), 0.05),
            limit_order_timeout_seconds=_int(payload.get("limit_order_timeout_seconds"), 60),
            retry_limit_order=_bool(payload.get("retry_limit_order"), False),
            max_retries=_int(payload.get("max_retries"), 0),
            chase_limit_order=_bool(payload.get("chase_limit_order"), False),
            max_chase_ticks=_int(payload.get("max_chase_ticks"), 0),
            trailing_stop_enabled=_bool(payload.get("trailing_stop_enabled"), False),
            active_trade_management_enabled=_bool(payload.get("active_trade_management_enabled"), True),
            breakeven_sl_enabled=_bool(payload.get("breakeven_sl_enabled"), True),
            breakeven_trigger_r=_float(payload.get("breakeven_trigger_r"), 1.0),
            breakeven_buffer=_float(payload.get("breakeven_buffer"), 0.05),
            active_trailing_sl_enabled=_bool(payload.get("active_trailing_sl_enabled"), True),
            trailing_method=str(payload.get("trailing_method") or "HYBRID").strip().upper(),
            trail_activation_r=_float(payload.get("trail_activation_r"), 1.2),
            min_sl_modification_gap=_float(payload.get("min_sl_modification_gap"), 0.05),
            min_seconds_between_sl_modifications=_int(payload.get("min_seconds_between_sl_modifications"), 15),
            partial_exit_enabled=_bool(payload.get("partial_exit_enabled"), False),
            partial_exit_trigger_r=_float(payload.get("partial_exit_trigger_r"), 1.0),
            partial_exit_qty_pct=_float(payload.get("partial_exit_qty_pct"), 50.0),
            condition_exit_enabled=_bool(payload.get("condition_exit_enabled"), True),
            ask_confirmation_before_early_exit=_bool(payload.get("ask_confirmation_before_early_exit"), False),
            early_exit_health_threshold=_float(payload.get("early_exit_health_threshold"), 35.0),
            opposite_signal_exit_threshold=_float(payload.get("opposite_signal_exit_threshold"), 82.0),
            tighten_sl_health_threshold=_float(payload.get("tighten_sl_health_threshold"), 55.0),
            dynamic_target_enabled=_bool(payload.get("dynamic_target_enabled"), True),
            dynamic_target_health_threshold=_float(payload.get("dynamic_target_health_threshold"), 78.0),
            target_extension_r=_float(payload.get("target_extension_r"), 0.5),
            time_exit_enabled=_bool(payload.get("time_exit_enabled"), True),
            max_minutes_in_trade=_float(payload.get("max_minutes_in_trade"), 0.0),
            max_candles_without_progress=_int(payload.get("max_candles_without_progress"), 0),
            news_enabled=_bool(payload.get("news_enabled"), True),
            live_news_enabled=_bool(payload.get("live_news_enabled") or payload.get("fetch_live_news"), False),
            news_score_cap=_float(payload.get("news_score_cap"), 5.0),
            event_blackout_enabled=_bool(payload.get("event_blackout_enabled"), True),
            event_blackout_windows=_list_dicts(payload.get("event_blackout_windows") or payload.get("blackout_windows")),
            blocked_symbols=_list_strings(payload.get("blocked_symbols")),
            require_mis_allowed=_bool(payload.get("require_mis_allowed"), True),
            block_asm_gsm_t2t=_bool(payload.get("block_asm_gsm_t2t"), True),
            min_liquidity_score=_float(payload.get("min_liquidity_score"), 35.0),
            max_allowed_spread_pct=_float(payload.get("max_allowed_spread_pct"), 0.35),
            max_trap_score_for_entry=_float(payload.get("max_trap_score_for_entry"), 80.0),
            minimum_relative_volume_for_entry=_float(payload.get("minimum_relative_volume_for_entry"), 0.0),
            options_bias_enabled=_bool(payload.get("options_bias_enabled"), False),
            confirm_real_mode=_bool(payload.get("confirm_real_mode"), False),
            auto_real_orders_confirmed=_bool(payload.get("auto_real_orders_confirmed"), False),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.mode not in SUPPORTED_MODES:
            raise ValueError("Mode must be PAPER, REAL, BACKTEST, or REPLAY.")
        if len(self.stocks) != MAX_STOCKS:
            raise ValueError(f"Intraday Stocks Terminal requires exactly {MAX_STOCKS} stocks.")
        seen = set()
        for stock in self.stocks:
            stock.validate()
            if stock.key in seen:
                raise ValueError(f"Duplicate stock is not allowed: {stock.key}.")
            seen.add(stock.key)
        if not self.allow_long and not self.allow_short:
            raise ValueError("At least one of long or short trading must be allowed.")
        if self.mode == MODE_REAL and not self.confirm_real_mode:
            raise ValueError("Real mode requires explicit confirmation before starting.")
        if self.order_mode != ORDER_LIMIT_ONLY:
            raise ValueError("Intraday stock trading is LIMIT_ONLY for paper and real money sessions.")
        if self.minimum_risk_reward <= 0:
            raise ValueError("Minimum risk reward must be greater than zero.")
        if self.minimum_entry_score < 0 or self.minimum_entry_score > 100:
            raise ValueError("Minimum entry score must be between 0 and 100.")
        if self.max_daily_loss <= 0 or self.max_loss_per_trade <= 0:
            raise ValueError("Loss limits must be greater than zero.")
        if self.estimated_leverage < 1:
            raise ValueError("Estimated MIS leverage must be at least 1x.")
        if self.max_open_positions < 1:
            raise ValueError("Max open positions must be at least 1.")
        if self.breakeven_trigger_r <= 0 or self.trail_activation_r <= 0:
            raise ValueError("Active management R triggers must be greater than zero.")
        if self.min_liquidity_score < 0 or self.max_allowed_spread_pct < 0:
            raise ValueError("Eligibility thresholds cannot be negative.")

    @property
    def market_orders_enabled(self) -> bool:
        return False

    def locked_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stocks"] = [asdict(stock) for stock in self.stocks]
        return data


@dataclass
class StockSnapshot:
    symbol: str
    exchange: str = EXCHANGE_NSE
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    ltp: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    candle_interval: str = DEFAULT_CANDLE_INTERVAL
    candles_available: int = 0
    last_candle_time: str = ""
    data_source: str = ""
    ema20: float = 0.0
    ema50: float = 0.0
    rsi: float = 50.0
    vwap: float = 0.0
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0
    relative_volume: float = 0.0
    spread: float = 0.0
    spread_pct: float = 0.0
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    depth_imbalance: float = 0.0
    liquidity_score: float = 0.0
    trap_score: float = 0.0
    trap_warning: str = "NONE"
    news_score: float = 0.0
    news_sentiment: str = "Unavailable"
    options_bias_score: float = 0.0
    options_bias: str = "Unavailable"
    final_long_score: float = 0.0
    final_short_score: float = 0.0
    selected_side: str = SIDE_NO_TRADE
    reason: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.exchange}:{self.symbol}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Signal:
    session_id: str
    symbol: str
    exchange: str
    side: str
    setup_name: str
    score: float
    score_breakdown: dict[str, Any]
    entry_price: float
    stoploss: float
    target: float
    risk_reward: float
    confidence: float
    explanation: str
    blockers: list[str] = field(default_factory=list)
    margin: dict[str, Any] = field(default_factory=dict)
    approved_by_user: bool = False
    final_decision: str = "PENDING"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def is_trade(self) -> bool:
        return self.side in {SIDE_LONG, SIDE_SHORT} and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
