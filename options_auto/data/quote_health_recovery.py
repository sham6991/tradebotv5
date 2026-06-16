"""
Quote health and recovery state machine for Options Auto.

Handles resilient quote sourcing:
- Websocket-first (real-time)
- Snapshot fallback (API-based) with rate-limiting
- Automatic recovery when data returns
- State machine tracking (HEALTHY, DEGRADED, PAUSED_FOR_DATA, RECONNECTING, etc.)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

class QuoteRecoveryState(Enum):
    """Recovery states for quote availability."""
    INIT = "INIT"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    PAUSED_FOR_DATA = "PAUSED_FOR_DATA"
    RECONNECTING = "RECONNECTING"
    SNAPSHOT_FALLBACK = "SNAPSHOT_FALLBACK"
    RECOVERED = "RECOVERED"
    RESUMED = "RESUMED"
    CONNECTION_LIMIT_BLOCKED = "CONNECTION_LIMIT_BLOCKED"
    API_RATE_LIMITED = "API_RATE_LIMITED"
    BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
    STOPPED = "STOPPED"
    KILL_SWITCHED = "KILL_SWITCHED"


class NormalizedQuote:
    """
    Normalized quote schema that handles both websocket ticks and snapshot quotes.
    
    All quotes normalize to this schema, ensuring decision pipeline sees consistent data.
    """
    
    def __init__(self, raw_quote: dict[str, Any] | None = None, **kwargs):
        """
        Initialize normalized quote from raw data.
        
        Args:
            raw_quote: Raw quote dict (websocket tick or snapshot API row)
            **kwargs: Additional fields to override
        """
        self.raw_quote = dict(raw_quote or {})
        self.normalized_at_epoch = datetime.now().timestamp()
        
        # Symbol/Key fields
        self.symbol = str(kwargs.get("symbol") or self.raw_quote.get("tradingsymbol") or "").upper()
        self.exchange = str(kwargs.get("exchange") or self.raw_quote.get("exchange") or "NSE").upper()
        self.quote_key = kwargs.get("quote_key") or f"{self.exchange}:{self.symbol}"
        self.instrument_token = int(kwargs.get("instrument_token") or self.raw_quote.get("instrument_token") or self.raw_quote.get("token") or 0)
        self.token = self.instrument_token  # Alias
        
        # Price fields (LTP)
        ltp = float(kwargs.get("ltp") or self.raw_quote.get("ltp") or self.raw_quote.get("last_price") or 0)
        self.ltp = float(ltp) if ltp > 0 else 0
        self.last_price = self.ltp  # Alias
        
        # Bid/Ask from depth
        depth = dict(self.raw_quote.get("depth") or {})
        buy_depth = list(depth.get("buy") or [])
        sell_depth = list(depth.get("sell") or [])
        
        self.bid = float(
            kwargs.get("bid") or
            (buy_depth[0].get("price") if buy_depth else None) or
            self.raw_quote.get("bid") or
            self.raw_quote.get("best_bid") or
            self.raw_quote.get("buy_price") or
            0
        )
        self.bid = float(self.bid) if self.bid > 0 else 0
        
        self.ask = float(
            kwargs.get("ask") or
            (sell_depth[0].get("price") if sell_depth else None) or
            self.raw_quote.get("ask") or
            self.raw_quote.get("best_ask") or
            self.raw_quote.get("sell_price") or
            0
        )
        self.ask = float(self.ask) if self.ask > 0 else 0
        
        # Bid/Ask quantity from depth
        self.bid_qty = int(
            kwargs.get("bid_qty") or
            (buy_depth[0].get("quantity") if buy_depth else None) or
            self.raw_quote.get("bid_qty") or
            self.raw_quote.get("buy_quantity") or
            self.raw_quote.get("total_buy_quantity") or
            0
        )
        
        self.ask_qty = int(
            kwargs.get("ask_qty") or
            (sell_depth[0].get("quantity") if sell_depth else None) or
            self.raw_quote.get("ask_qty") or
            self.raw_quote.get("sell_quantity") or
            self.raw_quote.get("total_sell_quantity") or
            0
        )
        
        # Spread
        if self.bid > 0 and self.ask > 0:
            self.spread = float(self.ask - self.bid)
            self.spread_pct = float((self.spread / self.ltp * 100) if self.ltp > 0 else 0)
        else:
            self.spread = 0.0
            self.spread_pct = 0.0
        
        # Depth analysis
        self.depth_present = bool(buy_depth or sell_depth)
        self.bid_present = bool(self.bid > 0)
        self.ask_present = bool(self.ask > 0)
        self.bid_qty_present = bool(self.bid_qty > 0)
        self.ask_qty_present = bool(self.ask_qty > 0)
        depth_buy_levels = len(buy_depth)
        depth_sell_levels = len(sell_depth)
        self.depth_buy_levels = depth_buy_levels
        self.depth_sell_levels = depth_sell_levels
        
        # Depth imbalance (simplified)
        if depth_buy_levels > 0 and depth_sell_levels > 0:
            total_buy_qty = sum(item.get("quantity", 0) for item in buy_depth[:5])
            total_sell_qty = sum(item.get("quantity", 0) for item in sell_depth[:5])
            total_qty = total_buy_qty + total_sell_qty
            self.depth_imbalance = float((total_buy_qty - total_sell_qty) / total_qty) if total_qty > 0 else 0.0
        else:
            self.depth_imbalance = 0.0
        
        # Volume
        self.volume = int(kwargs.get("volume") or self.raw_quote.get("volume") or self.raw_quote.get("volume_traded") or self.raw_quote.get("volume_traded_today") or 0)
        self.volume_traded = self.volume  # Alias
        
        # Open Interest
        oi = int(kwargs.get("oi") or self.raw_quote.get("oi") or self.raw_quote.get("open_interest") or self.raw_quote.get("openInterest") or 0)
        self.oi = oi
        self.open_interest = oi  # Alias for consistency
        
        # Timestamp handling
        exchange_ts = self.raw_quote.get("exchange_timestamp") or self.raw_quote.get("last_trade_time") or self.raw_quote.get("timestamp")
        if exchange_ts:
            try:
                if isinstance(exchange_ts, str):
                    self.timestamp = str(exchange_ts)
                    self.timestamp_epoch = self._parse_timestamp_epoch(exchange_ts)
                else:
                    self.timestamp_epoch = float(exchange_ts)
                    self.timestamp = datetime.fromtimestamp(self.timestamp_epoch).isoformat(timespec="seconds")
            except (ValueError, TypeError, OSError):
                self.timestamp = ""
                self.timestamp_epoch = 0.0
        else:
            self.timestamp = ""
            self.timestamp_epoch = 0.0
        
        self.last_updated_epoch = self.raw_quote.get("last_updated_epoch", self.timestamp_epoch)
        
        # Received at (for websocket ticks, use local receive time)
        self.received_at = kwargs.get("received_at", "")
        self.received_epoch = float(kwargs.get("received_epoch") or 0.0)
        if not self.received_epoch:
            self.received_epoch = self.normalized_at_epoch
        if not self.received_at:
            try:
                self.received_at = datetime.fromtimestamp(self.received_epoch).isoformat(timespec="seconds")
            except (ValueError, TypeError, OSError):
                self.received_at = ""
        
        # Age calculation
        self.age_seconds = self._calculate_age()
        self.age_source = self._determine_age_source()
        
        # Source tracking
        self.quote_source = kwargs.get("quote_source") or kwargs.get("source") or self.raw_quote.get("quote_source") or self.raw_quote.get("source") or "unknown"
        self.data_mode = kwargs.get("data_mode") or ("WEBSOCKET_TICKS" if "websocket" in str(self.quote_source).lower() else "SNAPSHOT_API" if "snapshot" in str(self.quote_source).lower() else "UNKNOWN")
        self.timestamp_source = kwargs.get("timestamp_source") or ("exchange" if self.timestamp_epoch > 0 else "local_received_at")
        
        # Tick size and contract metadata
        self.tick_size = float(kwargs.get("tick_size") or self.raw_quote.get("tick_size") or 0.05)
        self.role = str(kwargs.get("role") or self.raw_quote.get("role") or "").upper()
        
        # Data quality flags
        self.demo_data = bool(kwargs.get("demo_data") or self.raw_quote.get("demo_data"))
        self.stale = bool(kwargs.get("stale", False))
        self.valid = self._is_valid()
    
    def _parse_timestamp_epoch(self, ts_str: str) -> float:
        """Parse timestamp string to epoch."""
        try:
            if isinstance(ts_str, (int, float)):
                return float(ts_str)
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
            return dt.timestamp()
        except (ValueError, TypeError, AttributeError):
            return 0.0
    
    def _calculate_age(self) -> float:
        """Calculate quote age in seconds."""
        if self.timestamp_epoch > 0:
            return max(0.0, self.normalized_at_epoch - self.timestamp_epoch)
        elif self.received_epoch > 0:
            return max(0.0, self.normalized_at_epoch - self.received_epoch)
        return -1.0  # Unknown age
    
    def _determine_age_source(self) -> str:
        """Determine which timestamp was used for age calculation."""
        if self.timestamp_epoch > 0:
            return "exchange_timestamp"
        elif self.received_epoch > 0:
            return "received_at"
        return "unknown"
    
    def _is_valid(self) -> bool:
        """Check if quote is valid for trading."""
        # Quote must have symbol and token
        if not self.symbol or self.instrument_token <= 0:
            return False
        # LTP must be positive
        if self.ltp <= 0:
            return False
        # Should have bid/ask or at least LTP
        if self.bid <= 0 and self.ask <= 0 and self.ltp <= 0:
            return False
        return True
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "quote_key": self.quote_key,
            "instrument_token": self.instrument_token,
            "token": self.token,
            "ltp": self.ltp,
            "last_price": self.last_price,
            "bid": self.bid,
            "ask": self.ask,
            "bid_qty": self.bid_qty,
            "ask_qty": self.ask_qty,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "depth_present": self.depth_present,
            "bid_present": self.bid_present,
            "ask_present": self.ask_present,
            "bid_qty_present": self.bid_qty_present,
            "ask_qty_present": self.ask_qty_present,
            "depth_buy_levels": self.depth_buy_levels,
            "depth_sell_levels": self.depth_sell_levels,
            "depth_imbalance": self.depth_imbalance,
            "volume": self.volume,
            "volume_traded": self.volume_traded,
            "oi": self.oi,
            "open_interest": self.open_interest,
            "timestamp": self.timestamp,
            "timestamp_epoch": self.timestamp_epoch,
            "received_at": self.received_at,
            "received_epoch": self.received_epoch,
            "age_seconds": self.age_seconds,
            "age_source": self.age_source,
            "timestamp_source": self.timestamp_source,
            "quote_source": self.quote_source,
            "data_mode": self.data_mode,
            "tick_size": self.tick_size,
            "role": self.role,
            "demo_data": self.demo_data,
            "stale": self.stale,
            "valid": self.valid,
        }


class QuoteHealthTracker:
    """Tracks quote health and recovery state for Options Auto data."""
    
    def __init__(self):
        self.recovery_state = QuoteRecoveryState.INIT
        self.last_healthy_epoch = 0.0
        self.recovery_started_epoch = 0.0
        self.last_websocket_tick_epoch = 0.0
        self.last_snapshot_attempt_epoch = 0.0
        self.last_snapshot_success_epoch = 0.0
        self.snapshot_failure_count = 0
        self.websocket_connection_attempts = 0
        self.recovery_reason = ""
        self.paused_new_entries = False
        self.pending_recovery_count = 0
        self.required_recovery_ticks = 1
        self.diagnostics: dict[str, Any] = {}
    
    def mark_websocket_tick(self) -> None:
        """Mark that a websocket tick was received."""
        now = datetime.now().timestamp()
        self.last_websocket_tick_epoch = now
        if self.recovery_state == QuoteRecoveryState.RECONNECTING:
            self.recovery_state = QuoteRecoveryState.RECOVERED
            self.pending_recovery_count = 0
        elif self.recovery_state not in {QuoteRecoveryState.HEALTHY, QuoteRecoveryState.RESUMED}:
            self.recovery_state = QuoteRecoveryState.HEALTHY
        self.last_healthy_epoch = now
    
    def mark_snapshot_success(self) -> None:
        """Mark that snapshot fallback succeeded."""
        now = datetime.now().timestamp()
        self.last_snapshot_success_epoch = now
        self.snapshot_failure_count = 0
        if self.recovery_state == QuoteRecoveryState.API_RATE_LIMITED:
            self.recovery_state = QuoteRecoveryState.SNAPSHOT_FALLBACK
        elif self.recovery_state in {QuoteRecoveryState.PAUSED_FOR_DATA, QuoteRecoveryState.RECONNECTING}:
            self.recovery_state = QuoteRecoveryState.SNAPSHOT_FALLBACK
    
    def mark_snapshot_attempt(self) -> None:
        """Mark that a snapshot API call was attempted."""
        self.last_snapshot_attempt_epoch = datetime.now().timestamp()
    
    def mark_snapshot_failure(self) -> None:
        """Mark that a snapshot API call failed."""
        self.snapshot_failure_count += 1
        if self.snapshot_failure_count >= 3:
            self.recovery_state = QuoteRecoveryState.API_RATE_LIMITED
    
    def mark_websocket_stale(self, reason: str = "") -> None:
        """Mark that websocket data is stale/missing."""
        self.recovery_state = QuoteRecoveryState.RECONNECTING
        self.recovery_started_epoch = datetime.now().timestamp()
        self.recovery_reason = reason
        self.paused_new_entries = True
    
    def mark_connection_limit(self, reason: str = "") -> None:
        """Mark that connection limit has been reached."""
        self.recovery_state = QuoteRecoveryState.CONNECTION_LIMIT_BLOCKED
        self.recovery_reason = reason
        self.paused_new_entries = True
    
    def mark_recovered(self) -> None:
        """Mark that data has recovered after an outage."""
        self.pending_recovery_count += 1
        if self.pending_recovery_count >= self.required_recovery_ticks:
            self.recovery_state = QuoteRecoveryState.RESUMED
            self.paused_new_entries = False
    
    def snapshot(self) -> dict[str, Any]:
        """Return current state snapshot."""
        now = datetime.now().timestamp()
        return {
            "recovery_state": self.recovery_state.value,
            "paused_new_entries": self.paused_new_entries,
            "recovery_reason": self.recovery_reason,
            "last_websocket_tick_epoch": self.last_websocket_tick_epoch,
            "last_snapshot_success_epoch": self.last_snapshot_success_epoch,
            "snapshot_failure_count": self.snapshot_failure_count,
            "pending_recovery_count": self.pending_recovery_count,
            "recovery_elapsed_seconds": now - self.recovery_started_epoch if self.recovery_started_epoch > 0 else 0,
            "diagnostics": dict(self.diagnostics),
        }
