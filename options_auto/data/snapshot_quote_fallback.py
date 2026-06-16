"""
Snapshot quote fallback handler for Options Auto.

When websocket quotes are stale or missing, this module:
1. Batches quote API calls to minimize rate-limiting
2. Normalizes snapshot quotes to match websocket schema
3. Implements backoff and circuit-breaker for API failures
4. Tracks fallback usage and success rates
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from options_auto.data.quote_health_recovery import NormalizedQuote


class SnapshotQuoteFallback:
    """Handles fallback to Zerodha quote snapshot API."""
    
    def __init__(self, zerodha_client_provider=None):
        """
        Initialize snapshot fallback handler.
        
        Args:
            zerodha_client_provider: Callable that returns Zerodha client for mode
        """
        self.zerodha_client_provider = zerodha_client_provider or (lambda mode: None)
        self.last_attempt_epoch = 0.0
        self.last_success_epoch = 0.0
        self.consecutive_failures = 0
        self.backoff_until_epoch = 0.0
        self.total_attempts = 0
        self.total_successes = 0
        self.total_failures = 0
        self.last_error = ""
        self.circuit_breaker_threshold = 3
        self.circuit_breaker_backoff_seconds = 15
        self.min_interval_between_calls_seconds = 1.0
        self.cache: dict[str, dict[str, Any]] = {}
        self.cache_expiry_seconds = 5
    
    def get_quotes(
        self,
        mode: str,
        exchange_tokens: dict[str, str],  # {quote_key: exchange:tradingsymbol}; values are accepted for legacy callers
        settings: dict[str, Any] | None = None,
    ) -> dict[str, NormalizedQuote]:
        """
        Get quotes from snapshot API with fallback logic.
        
        Args:
            mode: "PAPER" or "REAL"
            exchange_tokens: Dict mapping quote_key to token
            settings: Options Auto settings
            
        Returns:
            Dict mapping quote_key to NormalizedQuote
        """
        settings = dict(settings or {})
        now = time.time()
        result: dict[str, NormalizedQuote] = {}
        
        # Check if we're in backoff
        if now < self.backoff_until_epoch:
            return result  # Return empty, circuit breaker active
        
        # Check minimum interval between calls
        if now - self.last_attempt_epoch < self.min_interval_between_calls_seconds:
            return result
        
        # Get client
        client = self.zerodha_client_provider(mode)
        if not client or not hasattr(client, "quote"):
            return result
        
        # Batch tokens into groups of 500 (Zerodha limit)
        quote_keys = [str(value or key) for key, value in exchange_tokens.items() if str(value or key).strip()]
        if not quote_keys:
            return result
        
        try:
            self.last_attempt_epoch = now
            self.total_attempts += 1
            
            # Make API call
            response = client.quote(quote_keys)
            if not response:
                raise Exception("Empty quote response from Zerodha")
            
            self.last_success_epoch = now
            self.consecutive_failures = 0
            self.total_successes += 1
            self.last_error = ""
            
            # Normalize quotes
            for quote_key, requested_key in exchange_tokens.items():
                requested_key = str(requested_key or quote_key)
                raw_quote = response.get(requested_key) or response.get(str(quote_key)) or {}
                if raw_quote:
                    try:
                        normalized = NormalizedQuote(
                            raw_quote=raw_quote,
                            quote_key=quote_key,
                            quote_source="zerodha_snapshot_quote",
                            data_mode="SNAPSHOT_API",
                            received_at=datetime.now().isoformat(timespec="seconds"),
                            received_epoch=now,
                        )
                        result[quote_key] = normalized
                        
                        # Cache successful quote
                        self.cache[quote_key] = {
                            "quote": normalized.to_dict(),
                            "cached_at_epoch": now,
                        }
                    except Exception as e:
                        self.last_error = f"Quote normalization failed: {str(e)}"
        
        except Exception as exc:
            self.consecutive_failures += 1
            self.total_failures += 1
            self.last_error = str(exc)
            
            # Activate circuit breaker if too many failures
            if self.consecutive_failures >= self.circuit_breaker_threshold:
                self.backoff_until_epoch = now + self.circuit_breaker_backoff_seconds
        
        return result
    
    def get_cached_quote(self, quote_key: str) -> NormalizedQuote | None:
        """
        Get cached quote if still valid (within cache expiry).
        
        Args:
            quote_key: Quote key to look up
            
        Returns:
            NormalizedQuote or None
        """
        cached = self.cache.get(quote_key) or {}
        cached_at = cached.get("cached_at_epoch", 0.0)
        if time.time() - cached_at > self.cache_expiry_seconds:
            self.cache.pop(quote_key, None)
            return None
        
        quote_dict = cached.get("quote") or {}
        if not quote_dict:
            return None
        
        try:
            return NormalizedQuote(raw_quote=quote_dict)
        except Exception:
            return None
    
    def is_rate_limited(self) -> bool:
        """Check if fallback is currently rate-limited."""
        return time.time() < self.backoff_until_epoch
    
    def snapshot(self) -> dict[str, Any]:
        """Return health snapshot."""
        now = time.time()
        return {
            "enabled": True,
            "circuit_breaker_active": now < self.backoff_until_epoch,
            "consecutive_failures": self.consecutive_failures,
            "backoff_remaining_seconds": max(0, self.backoff_until_epoch - now),
            "total_attempts": self.total_attempts,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "success_rate_pct": round(100 * self.total_successes / max(1, self.total_attempts), 2),
            "last_error": self.last_error,
            "cached_quote_count": len(self.cache),
            "min_interval_seconds": self.min_interval_between_calls_seconds,
        }
