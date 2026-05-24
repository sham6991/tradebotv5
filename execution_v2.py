"""Compatibility facade for the live/paper execution runtime."""

from candle_builder import CandleBuilder
from config import LOT_SIZE
from config_profile import apply_settings_profile
from engine import TradingEngine, append_datetime_index_key, attach_datetime_index_map, timestamp_key
from event_logger import (
    ENTRY_FILLED,
    KILL_SWITCH_ACTIVATED,
    LIVE_LATENCY_MEASURED,
    ORDER_CANCELLED,
    ORDER_COMPLETE,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    ORDER_REJECTED,
    PARTIAL_EXIT_DETECTED,
    PROTECTIVE_ORDER_VERIFICATION_FAILED,
    PROTECTIVE_ORDER_VERIFICATION_PASSED,
    PROTECTIVE_ORDER_PLACED,
    RECONCILIATION_ERROR,
    RECONCILIATION_WARNING,
    StructuredEventLogger,
)
from feed_runtime import Executor
from indicators import append_clean_candle, clean_and_add_indicators
from live_session import LivePaperSession, SessionEngine
from order_manager import ZerodhaOrderManager
from order_state import classify_order_state, normalize_order_status
from position_reconciler import PositionReconciler
from preflight import validate_live_preflight
from reporting import BufferedExcelWriter, format_datetime_value
from risk_guard import LiveRiskGuard
from session_audit import write_session_audit
from sqlite_store import AsyncTradingStore, TradingStore
from strategy import OPTION_ENTRY_REPORT_COLUMNS, append_option_formula_row, build_scoring_row, ensure_option_formula_columns
from zerodha_client import ZerodhaClient

__all__ = [
    "Executor",
    "LivePaperSession",
    "SessionEngine",
    "PositionReconciler",
]
