"""
Data recovery and fallback integration for Options Auto terminal service.

This module provides methods to:
1. Detect when data should trigger fallback
2. Attempt snapshot quote fallback
3. Track recovery state and resume conditions
4. Distinguish DATA blockers from other blockers
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from options_auto.data.quote_health_recovery import QuoteRecoveryState, QuoteHealthTracker, NormalizedQuote
from options_auto.data.snapshot_quote_fallback import SnapshotQuoteFallback


class DataRecoveryHelper:
    """Helper for integrating data recovery and fallback into decision pipeline."""
    
    def __init__(self, zerodha_client_provider=None):
        self.zerodha_client_provider = zerodha_client_provider or (lambda mode: None)
        self.health = QuoteHealthTracker()
        self.fallback = SnapshotQuoteFallback(zerodha_client_provider)
        self.last_decision_data_blockers: list[str] = []
        self.recovery_attempts = 0
        self.max_recovery_attempts_before_pause = 10
    
    def should_attempt_fallback(self, settings: dict[str, Any]) -> bool:
        """
        Determine if snapshot fallback should be attempted.
        
        Args:
            settings: Options Auto settings
            
        Returns:
            True if fallback is enabled and not rate-limited
        """
        if not bool(settings.get("quote_polling_fallback_enabled", True)):
            return False
        if self.fallback.is_rate_limited():
            return False
        return True
    
    def get_fallback_quotes(
        self,
        mode: str,
        quote_keys_and_tokens: dict[str, str],
        settings: dict[str, Any],
    ) -> dict[str, NormalizedQuote]:
        """
        Attempt to get quotes from snapshot fallback.
        
        Args:
            mode: "PAPER" or "REAL"
            quote_keys_and_tokens: Dict mapping quote_key to token
            settings: Options Auto settings
            
        Returns:
            Dict mapping quote_key to NormalizedQuote (may be empty if failed)
        """
        if not self.should_attempt_fallback(settings):
            return {}
        
        try:
            self.health.mark_snapshot_attempt()
            quotes = self.fallback.get_quotes(mode, quote_keys_and_tokens, settings)
            if quotes:
                self.health.mark_snapshot_success()
                self.recovery_attempts = 0
            else:
                self.health.mark_snapshot_failure()
                self.recovery_attempts += 1
            return quotes
        except Exception as exc:
            self.health.mark_snapshot_failure()
            self.recovery_attempts += 1
            return {}
    
    def evaluate_data_health(
        self,
        data_blockers: list[str],
        websocket_connected: bool,
        websocket_fresh: bool,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Evaluate overall data health and recovery state.
        
        Args:
            data_blockers: List of DATA-stage blockers from decision
            websocket_connected: Whether websocket is marked connected
            websocket_fresh: Whether latest quotes are fresh
            settings: Options Auto settings
            
        Returns:
            Dict with recovery_state, should_pause_entries, should_resume, etc.
        """
        should_pause = False
        should_resume = False
        pause_reason = ""
        
        # Track data blockers
        if data_blockers:
            self.last_decision_data_blockers = list(data_blockers)
            
            # Check if we can recover with fallback
            if self.should_attempt_fallback(settings):
                if self.recovery_attempts < self.max_recovery_attempts_before_pause:
                    # Try fallback
                    self.health.mark_websocket_stale("; ".join(data_blockers))
                    pause_reason = f"Data blockers detected, attempting snapshot fallback: {data_blockers[0]}"
                    should_pause = True
                else:
                    # Too many fallback attempts
                    self.health.mark_connection_limit("Too many fallback attempts without recovery")
                    pause_reason = "Recovery attempts exhausted, pausing entries"
                    should_pause = True
            else:
                # Fallback disabled
                self.health.mark_websocket_stale("; ".join(data_blockers))
                should_pause = True
                pause_reason = f"Data blockers and fallback disabled: {data_blockers[0]}"
        
        elif websocket_connected and websocket_fresh:
            # Data is good
            if self.health.recovery_state == QuoteRecoveryState.PAUSED_FOR_DATA:
                self.health.mark_recovered()
                if self.health.recovery_state == QuoteRecoveryState.RESUMED:
                    should_resume = True
            elif self.health.recovery_state != QuoteRecoveryState.HEALTHY:
                self.health.mark_websocket_tick()
        
        return {
            "recovery_state": self.health.recovery_state.value,
            "should_pause_entries": should_pause,
            "should_resume": should_resume,
            "pause_reason": pause_reason,
            "recovery_attempts": self.recovery_attempts,
            "health_snapshot": self.health.snapshot(),
            "fallback_snapshot": self.fallback.snapshot(),
        }
    
    def snapshot(self) -> dict[str, Any]:
        """Return complete data recovery snapshot."""
        return {
            "recovery_state": self.health.recovery_state.value,
            "last_decision_data_blockers": self.last_decision_data_blockers,
            "recovery_attempts": self.recovery_attempts,
            "health": self.health.snapshot(),
            "fallback": self.fallback.snapshot(),
        }
