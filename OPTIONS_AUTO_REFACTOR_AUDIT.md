# Options Auto Refactor Audit

## Phase 0 Audit

Files inspected:

- `options_auto/intelligence/decision_pipeline.py`
- `options_auto/intelligence/market_cue_engine.py`
- `options_auto/intelligence/regime_classifier.py`
- `options_auto/intelligence/simple_ohlcv_entry.py`
- `options_auto/intelligence/trade_score_engine.py`
- `options_auto/intelligence/entry_timing_engine.py`
- `options_auto/intelligence/options_greeks_risk_engine.py`
- `options_auto/intelligence/strike_selector.py`
- `options_auto/intelligence/exit_manager.py`
- `options_auto/intelligence/master_governor.py`
- `options_auto/intelligence/professional_discipline.py`
- `options_auto/intelligence/adaptive_risk_engine.py`
- `options_auto/execution/execution_safety.py`
- `options_auto/execution/paper_broker.py`
- `options_auto/execution/paper_lifecycle.py`
- `options_auto/execution/real_order_lifecycle.py`
- `options_auto/execution/real_execution_controller.py`
- `options_auto/execution/reconciliation.py`
- `options_auto/config/options_auto_defaults.py`
- `options_auto/terminal_service.py`
- `options_auto/web_routes.py`
- `options_auto/core/session_state.py`
- `options_auto/data/options_live_feed.py`
- `options_auto/data/locked_contract_manager.py`
- `options_auto/data/major_strike_selector.py`
- `options_auto/backtest/backtest_engine.py`
- `web_static/options_auto.html`
- `web_static/options_auto.js`
- relevant `tests/test_options_auto_*`

## Current Flow

Live scan cadence:

- `OptionsAutoTerminalService._live_scan_loop` waits on `_live_scan_wake` or the adaptive interval.
- `_run_live_scan_cycle_locked` performs one full evaluation and then paper or real lifecycle action.
- Aggressive, balanced, and conservative scan intervals already map to 1/2/3 second defaults.

Websocket tick wake behavior:

- `_on_options_websocket_ticks_locked` normalizes index and locked CE/PE ticks into `OptionsLiveFeed`.
- Ticks only call `_request_live_scan_wake_locked`.
- `_request_live_scan_wake_locked` respects `event_driven_min_scan_interval_ms`, so raw tick storms do not directly run duplicate decision cycles.

Quote polling fallback:

- `OptionsLiveFeed.quote_candidates` supplies warm websocket quotes.
- `_locked_contract_quote_result` falls back to Zerodha quote snapshots when websocket quote data is missing/stale and `quote_polling_fallback_enabled` is true.
- `quote_error_pause_new_entries` can block new entries on quote snapshot failure.

Side selection:

- `decision_pipeline._selected_side` uses explicit payload side first, then regime, then market cue.
- `simple_ohlcv_entry` could override the side when simple OHLCV mode is active.
- Before this patch, `PROFILE` plus `AGGRESSIVE` could silently enable simple OHLCV behavior.

Contract lock:

- `LockedContractManager` stores both CE and PE contracts.
- `terminal_service._locked_option_market_context` locks major-strike CE/PE pairs, subscribes websocket tokens, and returns the two locked contracts as scan candidates.
- `should_reselect` avoids reselection during active trades.

Selected contract:

- `StrikeSelector.select` previously selected and also rejected contracts for bid/ask, spread, depth, liquidity score, deep OTM, and premium confirmation.
- `decision_pipeline` then added data quality, theta, score, and timing blockers, causing overlapping responsibility.

Paper lifecycle:

- Paper has pending approval, pending entry, active trade, target/SL style OCO state, ledger entries, charges, and closed trade snapshots.
- Reset is blocked while pending or active paper lifecycle state exists.

Real lifecycle:

- Real has preflight, dry-run behavior, final validation, guarded limit entry, protection placement, partial-fill protection, OCO monitoring, broker reconciliation, safe mode, and unprotected-position blocking.
- Broker-open positions are not hidden when reconciliation detects them.

Settings persistence:

- Backend persists normalized settings through `configure`.
- UI loads defaults/status and applies settings to controls.
- Before this patch, real order toggles were not exposed in settings controls and the Start Real payload forced them on.

Backtest path:

- Backtest uses `OptionsAutoBacktestEngine`, which calls the same decision pipeline for signals, with historical quote proxies and a separate fill model.
- Market context is now attached to the pipeline output so backtest can consume the same router output without a live-only dependency.

## Duplicate Blockers Found

- Spread can come from `StrikeSelector`, `DataQualityEngine`, and `EntryTimingEngine`.
- Quote freshness and demo data belong to data quality, but selected-contract validation needed a visible stage.
- Chase/overextension belongs to `EntryTimingEngine`.
- Score threshold appears in selector and pipeline.

## Frontend/Backend Mismatches Found

- UI exposed `PROFILE` entry mode although the desired source of truth is explicit entry mode.
- UI `realEnginePayload` forced `dry_run_real_only=false`, `real_orders_enabled=true`, and `real_auto_entry_enabled=true`.
- Backend `start_real_engine` also forced the same real flags.
- UI and route code checked `stale`, while live feed health exposes `feed_stale`.
- `/api/options-auto/ui-summary` did not expose scan scheduler/stale diagnostics.

## Risk Areas

- Real order safety must not be weakened.
- Report-only market context must not alter legacy strategy outputs.
- Contract-level blockers must be deduped without hiding root causes.
- Stale live decisions must not be shown as current UI state.
- Backtest must not silently fabricate live-only data.

## Rollback Plan

- Revert new router/validator integration first if decision parity fails.
- Keep the real-start safety fix even if later UI/reporting work is rolled back.
- If UI rendering fails, backend JSON additions can remain because they are additive and backward compatible.
