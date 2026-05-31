# TradeBotV4 Planner

Date created: 2026-05-18

`BLUEPRINT.md` remains the base document for project structure, architecture, safety rules, and current behavior. This planner is only for dated work queues and should be updated as planned jobs are completed or moved.

## 2026-05-27 Next Improvement Queue

### Priority 1 - Clean Up Legacy Backtest Connection Surface - Completed 2026-05-27

Primary goal:

Remove remaining user-facing and API-level confusion from the old third Zerodha connection mode now that Virtual/Paper is the shared data connection for paper trading, Backtest Live optimizer, and Market Cue Analyzer.

Scope:

- Keep only two visible Zerodha connection concepts:
  - Virtual/Paper Data
  - Real Money
- Remove or deprecate the legacy `BACKTEST` connection object from `/api/status` if no compatibility caller still needs it.
- Keep Backtest Live optimizer using the Virtual/Paper Zerodha client.
- Keep Market Cue Analyzer using the Virtual/Paper Zerodha client.
- Update tests that still name `BACKTEST` as a separate connection, while preserving backward-compatible request aliases only where needed.
- Update README/BLUEPRINT wording if the public API/status shape changes.

Guardrails:

- Do not change strategy signals, Fast OHLCV scoring, entry/exit logic, live order placement, paper simulation behavior, or Zerodha credential handling.
- Do not add any new Zerodha login flow.
- Do not break existing Paper or Real Money start flows.
- Do not connect Market Cue output to automatic order placement.

Verification required:

```powershell
python -B -m py_compile web_app.py web_static\app.js
python -B -m unittest tests.test_backtest_live_connection tests.test_web_app_feed_inputs tests.test_live_start_safety
python -B -m unittest discover -s tests
node --check web_static\app.js
```

Completion notes:

- Removed the legacy `BACKTEST` connection object from `/api/status` and account margin payloads.
- Kept `BACKTEST` and `VIRTUAL` as backward-compatible request aliases for the shared `PAPER` connection.
- Updated Backtest Live optimizer usage to require the Virtual/Paper Zerodha connection.
- Updated user-facing Zerodha labels to show two concepts only: Virtual/Paper Data and Real Money.
- Preserved Market Cue Analyzer and Backtest Live optimizer routing through the Virtual/Paper Zerodha client.
- No strategy signals, Fast OHLCV scoring, entry/exit logic, paper simulation behavior, live order placement, or credential flow were changed.
- Verified with:
  - `python -m unittest tests.test_backtest_live_connection tests.test_web_app_feed_inputs tests.test_live_start_safety`
  - `python -m py_compile web_app.py`
  - `node --check web_static\app.js`
  - `python -m unittest discover -s tests`
  - project-wide `python -m py_compile`
  - `RUN_CANDLE_BUILDER_STRESS=1 python -m unittest tests.test_candle_builder_stress`
  - local web restart and HTTP smoke checks for `/`, `/api/status`, `/api/settings`, `/api/market-cue/history`, and `/api/market-cue/latest-bias`

### Priority 2 - Add Pre-Trade Readiness Panel

Primary goal:

Add one compact readiness panel before Paper/Real start that shows whether the system is ready to trade.

Checklist items:

- Zerodha connection status.
- Fresh network health.
- Recovery state safe.
- Settings profile valid.
- Market Cue report freshness/reliability.
- Paper-first guard status for real-money trading.

Guardrails:

- Read-only panel only.
- Must not place, modify, cancel, or square off orders.
- Must not change strategy decisions or live start behavior unless explicitly wired as a later blocking rule.

### Priority 3 - Separate Runtime Settings From Committed Settings

Primary goal:

Reduce noisy git diffs from runtime values such as paper balance and temporary UI/session fields.

Scope:

- Move mutable runtime values to an ignored runtime file or SQLite runtime table.
- Keep stable strategy/profile settings in `data/settings_profiles.json`.
- Migrate current values carefully without deleting user settings.

### Priority 4 - Add Real NSE CSV Fixtures For Market Cue

Primary goal:

Make the NSE FII/DII parser resistant to official CSV format changes.

Scope:

- Add sample fixture CSVs for:
  - NSE only FII/DII report.
  - Combined NSE+BSE+MSEI FII/DII report.
- Cover header/footer variations and renamed buy/sell/net columns.
- Keep samples free of secrets and generated report data.

### Priority 5 - Add Frontend Smoke Tests

Primary goal:

Catch UI regressions before manual testing.

Scope:

- Load `/`.
- Switch between dashboard, market cue, backtest, paper, live, replay, and Zerodha views.
- Verify required buttons/forms exist.
- Verify there are exactly two Zerodha auth forms.
- Verify Market Cue controls render without missing IDs.

### Priority 6 - Add One-Command Release Check Script

Primary goal:

Create one command that runs the normal hard-test path and prints a short pass/fail summary.

Candidate command:

```powershell
python tools\release_check.py
```

Checks:

- Full unit test discovery.
- Optional stress test flag support.
- Project-wide Python compile.
- Frontend JS syntax check.
- Frontend selector integrity check.
- Local web/API smoke checks.

### Priority 7 - Improve Market Cue Freshness Badges

Primary goal:

Make it visually impossible to confuse fresh, partial, stale, cached, or manual data.

Scope:

- Add top-level freshness badge:
  - Fresh
  - Partial
  - Stale
  - Manual
- Add per-row source badges for fresh/stale/cached/manual/fallback.
- Surface confidence penalties in the UI.

## 2026-05-19 Planned Job - Completed 2026-05-22

### Primary Goal

Reduce the size and responsibility overlap in `execution_v2.py` without changing strategy behavior, entry rules, broker semantics, or risk controls.

### Scope

- Review current `execution_v2.py` responsibilities and map natural ownership boundaries.
- Split only where the boundaries are obvious and testable.
- Keep `LivePaperSession` public behavior stable for web, desktop, tests, and benchmarks.
- Preserve the current entry-offset flow:
  - market entry uses signal candle close basis when `entry_offset = 0`.
  - limit entry uses signal candle close plus offset when `entry_offset != 0`.
  - unfilled limit entries cancel after 30 seconds and do not convert to market.
- Keep `BLUEPRINT.md` updated if module boundaries change.

### Candidate Module Boundaries

- live session orchestration
- order lifecycle and pending entry handling
- feed runtime, dispatcher, reconnect, watchdog, and health metrics
- recovery state persistence
- session/audit/export helpers

### Guardrails

- No strategy changes.
- No entry/exit rule changes.
- No changes to Zerodha order side/order type semantics.
- No changes to risk guard limits or kill-switch behavior.
- Do not remove legacy paths during this job unless separately planned.

### Verification Required

```powershell
python -m unittest discover -s tests
python -B process_flow_benchmark.py
python -B tick_storm_benchmark.py
```

Run dataset replay if suitable NIFTY/CE/PE CSV inputs are available.

### Completion Notes

- Split `execution_v2.py` into a compatibility facade.
- Moved `LivePaperSession` and session behavior into `live_session.py`.
- Moved `Executor`, feed dispatcher, reconnect/watchdog, and live-history fetch behavior into `feed_runtime.py`.
- Preserved `from execution_v2 import Executor, LivePaperSession` for existing UI, web, tests, and benchmarks.
- No strategy, entry/exit, order side/order type, risk, kill-switch, or UI flow changes were made.
- Verified with:
  - `python -m py_compile execution_v2.py live_session.py feed_runtime.py`
  - `python -m unittest tests.test_alert_hooks tests.test_feed_status tests.test_live_history_fetch tests.test_preflight tests.test_paper_balance_check`
  - `python -m unittest discover -s tests`
  - `python -B process_flow_benchmark.py`
  - `python -B tick_storm_benchmark.py`

## 2026-05-20 Planned Job - Completed 2026-05-23

### Primary Goal

Tighten real-money live-entry safety without changing strategy signals, paper-trading simulation, or Backtest Live optimizer behavior.

### Scope

- Change LIVE margin-check failure handling so a broker margin lookup exception blocks the entry instead of allowing it.
- Keep PAPER balance checking isolated in `_validate_paper_balance()`.
- Keep LIVE margin checking isolated in `_validate_live_margin()`.
- Do not change Zerodha order placement, protective order behavior, target/stoploss/time-exit rules, or entry signal rules.
- Add tests proving:
  - LIVE rejects entry when broker margin lookup fails.
  - LIVE still allows entry when broker margin is available and sufficient.
  - PAPER continues to use simulated balance only and never broker margin.

### Guardrails

- No strategy changes.
- No Backtest Live optimizer changes.
- No paper/live mode mixing.
- Do not alter real-money order side, order type, product, target, stoploss, or square-off behavior.

### Verification Required

```powershell
python -m unittest tests.test_paper_balance_check
python -m unittest discover -s tests
```

### Completion Notes

- LIVE margin lookup exceptions now reject the entry instead of allowing it.
- LIVE unavailable or invalid margin values now reject the entry.
- PAPER balance checking remains isolated from broker margin.
- Margin-rejected entries still do not consume trade slots, open positions, or stop later entry scans.
- Verified with:
  - `python -B -m unittest tests.test_paper_balance_check`
  - `python -B -m unittest discover -s tests`

## 2026-05-22 Completed Job

### Primary Goal

Isolate backtesting execution logic from paper/real-money execution and add conservative same-candle exit behavior for BACKTEST mode only.

### Completion Notes

- Added `backtest_runtime.py` with `BacktestTradingCore` as the backtesting-only execution simulator.
- Reduced `trading_core.py` to a compatibility import for old callers.
- Updated `backtest.py` and parity replay to use `BacktestTradingCore` directly.
- Added strict backtest exit sequencing:
  - entry candle checks stoploss only and ignores target.
  - next/future candles update trailing stoploss, check low first, then high.
  - same-candle stoploss exits as `STOPLOSS_SAME_CANDLE`.
- Added regression coverage for same-candle target ignore, same-candle stoploss, low-before-high priority, and trailing stoploss priority.

### Verification Performed

```powershell
python -m py_compile backtest_runtime.py trading_core.py backtest.py parity_replay.py tests/test_strategy_regression.py
python -m unittest tests.test_strategy_regression tests.test_backtest_export tests.test_parity_replay
python -m unittest discover -s tests
python -B process_flow_benchmark.py
python -B tick_storm_benchmark.py
```

## 2026-05-22 Completed Job - Trailing Stop Loss

### Primary Goal

Add optional Trailing Stop Loss risk management to BACKTEST, PAPER, and LIVE_ZERODHA without changing strategy entry decisions.

### Completion Notes

- Added `trailing_stop.py` for shared trailing settings, validation, and step-level calculation.
- Added Risk Settings fields:
  - `trailing_sl_enabled`
  - `trailing_start_points`
  - `trailing_step_points`
  - `trailing_lock_points`
- Added validation: trailing enabled requires `profit_points > 10`.
- Backtesting now uses the shared formula and logs trailing modifications.
- Paper mode updates the virtual stoploss and exits as `TRAILING_STOPLOSS` when touched.
- LIVE_ZERODHA modifies the existing SELL SL-M order trigger using the original `stoploss_order_id`; it does not cancel/recreate SL-M during trailing.
- Added Zerodha adapter support for modifying an existing SL-M trigger.

### Verification Performed

```powershell
python -m py_compile trailing_stop.py settings_validation.py settings_service.py preflight.py zerodha_client.py order_manager.py backtest_runtime.py live_session.py web_app.py tests/test_strategy_regression.py tests/test_live_entry_active_candle.py tests/test_order_manager.py tests/test_settings_validation.py tests/test_preflight.py
python -m unittest tests.test_settings_validation tests.test_preflight tests.test_order_manager tests.test_strategy_regression tests.test_live_entry_active_candle
python -m unittest discover -s tests
python -B process_flow_benchmark.py
python -B tick_storm_benchmark.py
```

## 2026-05-23 Completed Job - Real-Money Live Start Safety

### Primary Goal

Make real-money live start require fresh safety checks before feed/trading startup.

### Completion Notes

- Added a LIVE-only start gate in the web runtime before historical fetch/feed startup.
- Real-money start now requires:
  - recent LIVE network health check with connected broker status and no failed steps.
  - recent LIVE recovery check with `Safe To Trade` / `Good` status.
  - fresh Zerodha margin refresh with positive available margin.
  - existing live preflight still runs before session start and validates market data, settings, broker, margin, and market-hours warning state.
- Recovery check now reads the latest saved LIVE kill-switch file and blocks start if restored kill-switch state is active.
- Paper trading start behavior is unchanged.

### Verification Performed

```powershell
python -B -m unittest tests.test_live_start_safety
python -B -m unittest tests.test_preflight
python -B -m unittest discover -s tests
python -B process_flow_benchmark.py
python -B tick_storm_benchmark.py
```

## 2026-05-23 Completed Job - Runtime Error Classification

### Primary Goal

Improve broker/feed/order exception classification without changing strategy decisions, entry scoring, candle construction, or order-side semantics.

### Completion Notes

- Added shared runtime error classification in `runtime_errors.py`.
- Order placement now keeps existing retry/reconciliation behavior while also reporting `error_category`.
- Feed health/dispatcher metrics now expose classified feed and dispatcher errors.
- Margin, preflight, recovery, order status, cancel, and trailing-stop modification error logs now include classification metadata where available.
- No Fast OHLCV scoring, trend selection, entry thresholds, or candle decision logic was changed.

### Verification Performed

```powershell
python -B -m py_compile runtime_errors.py order_manager.py preflight.py web_app.py feed_runtime.py live_session.py
python -B -m unittest tests.test_runtime_errors tests.test_order_manager tests.test_feed_status
python -B -m unittest tests.test_preflight tests.test_paper_balance_check tests.test_order_idempotency
python -B -m unittest discover -s tests
python -B process_flow_benchmark.py
python -B tick_storm_benchmark.py
```

## 2026-05-23 Planned Job - Live Session Responsibility Split

### Primary Goal

Reduce future live-trading bug risk by splitting `live_session.py` into smaller ownership modules without changing strategy decisions, order placement semantics, risk rules, or the live/paper hot path behavior.

### Scope

- Extract only clear, already-existing responsibilities from `live_session.py`.
- Keep `LivePaperSession` as the public compatibility surface for web, desktop, tests, and benchmarks.
- Candidate extraction modules:
  - `candle_runtime.py`: tick-to-candle processing and candle-memory trimming.
  - `order_lifecycle.py`: entry order, pending limit entry, target, stoploss, cancellation, partial fill, and exit-order helpers.
  - `broker_reconciliation.py`: local-vs-Zerodha startup/runtime reconciliation helpers.
  - `risk_runtime.py`: live risk checks, kill-switch persistence, and risk-state synchronization.
  - `session_persistence.py`: recoverable position, pending-entry, kill-switch, candle, and audit persistence helpers.
- Move code in small steps with tests after each boundary.

### Guardrails

- No strategy changes.
- No Fast OHLCV scoring changes.
- No entry/exit rule changes.
- No order side, order type, quantity, product, retry, idempotency, protective-order, or reconciliation behavior changes.
- No web/desktop API behavior changes.
- Do not introduce overlapping ownership between old and new modules; each extracted helper must have one clear responsibility.
- Preserve existing tests and benchmark behavior before adding new features.

### Verification Required

```powershell
python -B -m py_compile live_session.py execution_v2.py feed_runtime.py
python -B -m unittest tests.test_paper_balance_check tests.test_order_idempotency tests.test_partial_fill_lifecycle tests.test_live_start_safety
python -B -m unittest discover -s tests
python -B process_flow_benchmark.py
python -B tick_storm_benchmark.py
```

### Progress Notes

- Started behavior-preserving extraction on 2026-05-23.
- Added `session_persistence.py` for recoverable open-position, pending-entry, and kill-switch persistence helpers.
- Added `broker_reconciliation.py` for LIVE startup reconciliation helpers while preserving the `execution_v2.PositionReconciler` patch point used by tests.
- Added `risk_runtime.py` for risk-state synchronization, kill-switch activation, trading-block, and square-off-time helpers.
- Kept `LivePaperSession` as the public compatibility surface.
- No strategy, Fast OHLCV scoring, entry/exit rules, order side/type/quantity/product, retry, idempotency, protective-order, reconciliation semantics, or web/desktop API behavior was intentionally changed.
- Verified so far with:
  - `python -B -m py_compile live_session.py execution_v2.py feed_runtime.py session_persistence.py broker_reconciliation.py risk_runtime.py`
  - `python -B -m unittest tests.test_paper_balance_check tests.test_order_idempotency tests.test_partial_fill_lifecycle tests.test_live_start_safety tests.test_alert_hooks tests.test_live_kill_switch`
  - `python -B -m unittest discover -s tests`
  - `python -B process_flow_benchmark.py`
  - `python -B tick_storm_benchmark.py`

## 2026-05-23 Planned Job - Pre-Live Drill Mode

### Primary Goal

Add a pre-live drill mode that lets the user verify live-readiness before market start without placing real orders, changing strategy logic, or touching the live trading hot path.

### Scope

- Add a manual drill action in the Web Control Center before live start.
- Run existing safety checks in one report:
  - LIVE network health.
  - LIVE recovery/safety status.
  - Zerodha margin refresh.
  - settings validation and live preflight checks.
  - recoverable open-position, pending-entry, and kill-switch state checks.
- Add fake-broker lifecycle checks that do not place real orders:
  - simulated order placement success.
  - simulated rejected order.
  - simulated retryable technical failure.
  - simulated unknown broker state / reconciliation-required response.
- Present a simple `PASS`, `WARN`, or `BLOCKED` summary with detailed reasons.

### Guardrails

- Drill mode must be manually triggered and must not run automatically inside active live trading.
- Drill mode must never place, modify, cancel, or square off real Zerodha orders.
- Drill mode must not change strategy decisions, Fast OHLCV scoring, candle processing, live order placement, retry policy, or risk limits.
- Drill mode must not consume trade count, entry-attempt tracking, cooldown, or pending-entry state.
- Reuse existing validation/recovery/preflight logic where possible instead of duplicating business rules.

### Verification Required

```powershell
python -B -m py_compile web_app.py preflight.py runtime_errors.py live_session.py
python -B -m unittest tests.test_live_start_safety tests.test_preflight tests.test_runtime_errors
python -B -m unittest discover -s tests
python -B process_flow_benchmark.py
python -B tick_storm_benchmark.py
```

## 2026-05-25 Completed Improvement - Paper SL-Limit Exit Fidelity

### Primary Goal

Make PAPER stoploss and trailing-stoploss exits model Zerodha option SL-limit behavior more faithfully, so paper reports do not label stoploss exits as market exits.

### Completion Notes

- LIVE_ZERODHA order placement remains unchanged: real protective stoploss exits use SELL `SL` orders with trigger and limit price, never `SL-M`.
- PAPER stoploss and trailing-stoploss close events now emit simulated `SL` instead of `MARKET`.
- PAPER stoploss and trailing-stoploss order-history rows now include simulated trigger and SL-limit prices.
- Paper fill model remains reporting-only for this improvement: the trade exits at current paper LTP when the virtual stoploss is touched, while the order event records the equivalent simulated `SL` trigger/limit intent.
- Paper trailing-stoploss modification events also include the simulated SL-limit price for audit clarity.
- Added regression coverage for paper trailing stoploss modification followed by stoploss exit, proving the final order-history event is `SL` with trigger and limit prices.

### Guardrails

- No strategy, Fast OHLCV scoring, entry decision, or trailing formula changes.
- No changes to real-money Zerodha placement, modification, cancellation, retry, idempotency, or protective-order OCO behavior.
- Do not make paper exits call Zerodha or any broker adapter.
- Preserve existing paper balance and trade-count behavior unless stricter SL-limit simulation explicitly requires a documented escaped-stop state.

### Verification Performed

```powershell
python -B -m unittest tests.test_live_entry_active_candle
python -B -m unittest tests.test_order_manager
python -B -m unittest tests.test_strategy_regression
python -B -m unittest tests.test_closed_trade_ui_state tests.test_sqlite_store_order_history
python -B -m unittest discover -s tests
```

## 2026-05-25 Completed Improvement - Trailing Start Time Safeguard

### Primary Goal

Add a trailing-stop safeguard that tightens exits if a trade does not reach trailing-start profit within five candles from the signal-generation candle.

### Completion Notes

- Added `trailing_safeguard.py` so the timing/price rule is isolated from Fast OHLCV strategy decisions and the existing trailing-stop formula.
- The safeguard is active only when trailing stoploss is enabled.
- The five-candle clock starts from `signal_index`.
- If price reaches `entry_price + trailing_start_points` before the five-candle deadline, the safeguard is nullified for that trade.
- If price does not reach trailing start by the deadline:
  - target is tightened to `entry_price + 5`.
  - stoploss trigger is tightened to `entry_price - 5`.
  - LIVE_ZERODHA stoploss limit price is still calculated from the configured `stoploss_limit_buffer_points`.
- BACKTEST applies the safeguard inside the exit simulator before target/stop touch checks on the first eligible candle.
- PAPER updates virtual target/stoploss and records simulated modification events.
- LIVE_ZERODHA modifies the existing target SELL LIMIT and stoploss SELL SL orders; it does not cancel/recreate protective orders.

### Guardrails

- No Fast OHLCV scoring, entry decision, or strategy filter changes.
- No change to the existing trailing-start/step/lock formula.
- No live order placement side/product/quantity/idempotency changes.
- No SL-M path added for Zerodha option stoploss orders.

### Verification Performed

```powershell
python -B -m unittest tests.test_live_entry_active_candle
python -B -m unittest tests.test_strategy_regression
python -B -m unittest tests.test_order_manager
```
