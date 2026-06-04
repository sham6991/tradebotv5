# TradeBotV5 Planner

Date created: 2026-05-18

`BLUEPRINT.md` remains the base document for project structure, architecture, safety rules, and current behavior. This planner is only for dated work queues and should be updated as planned jobs are completed or moved.

## 2026-06-04 Options Auto Phased Implementation - Completed

Primary goal:

Add the new `Options Auto` terminal for NIFTY/SENSEX index options with backtest, shadow, paper, and guarded real-money workflows.

Completion notes:

- Added modular `options_auto/` package with mode guard, session state, logging, market cue, regime classifier, strike selection, scoring, risk/discipline/master governor, data adapters, backtest, shadow, paper lifecycle, real safety, watchdog, promotion, Telegram safety, drift, missed-trade learning, and replay components.
- Added `/options-auto` cockpit and `/api/options-auto/*` routes for defaults, evaluate, shadow, paper approval/execution, paper market processing, real dry-run, real preflight, real reconciliation, emergency plan, health, exit evaluation, promotion, drift, missed trades, replay, and Telegram command validation.
- Implemented paper account balance/ledger, approval timeout, simulated entry/target/SL/OCO, trailing/breakeven/partial/theta/time/reversal exits, and local-only order history.
- Implemented real execution safety foundation with static-IP confirmation only in REAL mode, broker preflight evidence, duplicate order guard, manual-order detector, unprotected-position detector, Stop New Entries, Safe Mode, and dry-run emergency exit plans.
- Kept real order placement disabled through the web route and hard-guarded by `ModeGuard`; no real order path is available from paper/backtest/shadow mode.
- Added advanced cockpit safety visibility for mode, Kite status, data freshness, engine status, health, position protection, OCO, real-money state, reconciliation, and manual-attention status.
- Added report writers for backtest, shadow, and replay outputs under `results/options_auto/...`.

Verification performed:

```powershell
python -B -m unittest tests.test_options_auto_analysis_replay tests.test_options_auto_watchdog_health tests.test_options_auto_exit_manager tests.test_options_auto_real_safety tests.test_options_auto_paper_lifecycle tests.test_options_auto_phase_engines tests.test_options_auto_mode_guard tests.test_options_auto_order_state_machine tests.test_options_auto_indicators tests.test_options_auto_strike_selector tests.test_options_auto_web_routes
python -B -m compileall -q options_auto
node --check web_static\options_auto.js
```

Safety limitations:

- Real-money order placement remains intentionally disabled from the Options Auto web route until separately reviewed with a real 1-lot trial checklist.
- Emergency exit currently produces a dry-run broker action plan and does not send Zerodha orders.
- Shadow learning, missed-trade learning, drift detection, and replay are analysis-only and do not auto-change live parameters.

## 2026-06-03 Intraday Real-Order Safety Improvement Queue

Primary goal:

Improve intraday decision quality and real-money execution safeguards before expanding automated real-order behavior.

Priority 1 - Real order reconciliation:

- Add a broker-backed order state machine for `PLACED`, `OPEN`, `PARTIAL`, `COMPLETE`, `REJECTED`, and `CANCELLED`.
- Reconcile real orders through Kite order history, trades, postbacks, or WebSocket updates instead of trusting only the initial `place_order` response.
- Add idempotency checks before any retry so request timeouts cannot create duplicate real orders.

Priority 2 - Protective order safety:

- Place stoploss and target only after confirmed entry fill.
- Size protective orders from actual filled quantity, including partial fills.
- If stoploss placement fails after a real entry fill, freeze new trades and trigger emergency exit handling.
- Validate SL-LIMIT trigger/limit relationship and tick-size rounding before broker send.

Priority 3 - Broker-real kill switch:

- Cancel pending intraday orders.
- Fetch live positions and open orders from Zerodha.
- Square off open MIS positions where possible.
- Verify the account is flat before clearing the emergency state.
- Keep trading frozen if any cancel/square-off/reconciliation step is uncertain.

Priority 4 - Final approval revalidation:

- Re-check LTP, spread, depth, margin, position state, candle age, price band, and risk after user approval and before broker send.
- Expire real-money approval quickly, ideally 10-15 seconds.
- Show a real-order ticket with symbol, side, quantity, entry, stoploss, target, max loss, margin, live LTP, spread, data age, setup reason, and active account mode.

Priority 5 - Data freshness and broker health gates:

- Block real orders if candles, ticks, quote, depth, or margin data are stale.
- Block real orders if the system falls back to simulated/provided data in real mode.
- Pause real order execution when quote, margin, order, or instrument APIs start failing.
- Add rate-limit and broker latency guardrails.

Priority 6 - Decision logic improvements:

- Separate setup score from hard execution gates.
- Make regime-specific rules binding: reduce quantity or require stricter structure in `HIGH_VOLATILITY`, `TRAP_HEAVY`, `LOW_VOLUME`, and `CHOPPY`.
- Cap news influence so Pulse/Google news can support or block a valid setup but cannot create a trade by itself.
- Add event blackout windows around RBI announcements, results, macro releases, and sudden market-wide news.
- Calibrate trigger weights from paper/backtest/live outcomes by setup type.

Priority 7 - Risk and eligibility controls:

- Sync risk state from broker before every real order: positions, open orders, day trades, P&L, and margin.
- Add no-new-entry and square-off cutoffs near market close.
- Add daily limits for real mode: max loss, max trades, max order attempts, max rejected orders, max API failures, and max slippage.
- Reject low-liquidity, wide-spread, circuit-risk, ASM/GSM/T2T-like, or unreliable MIS/shorting candidates before real start.
- Track intended price versus actual average fill and block symbols or real mode when slippage exceeds limits.

Priority 8 - Audit and drill mode:

- Store immutable audit records for locked settings, formula version, data timestamps, score breakdown, order payloads, broker responses, order updates, and user approval timestamps.
- Add a dry-run real mode that uses real live data and real margin checks but never places orders.
- Keep dry-run/drill mode separate from paper simulation and real execution.

### Completed 2026-06-03 - Intraday Order Execution Safeguards

Implemented for the intraday feature:

- Added broker-order idempotency checks before real entry send by matching existing Kite orders using tag/session payload details.
- Added post-error reconciliation after uncertain real `place_order` failures; if no matching broker order is found, real trading is paused/blocked.
- Added instrument tick-size based price normalization for entry, stoploss, and target order requests.
- Added SL-LIMIT trigger/limit relationship validation for BUY and SELL exits, including one-tick minimum buffer enforcement.
- Added real-order circuit/price-band blockers for entry, stoploss, and target prices.
- Added abnormal intraday move blocker versus day open.
- Added real-data hard blocks for simulated/provided data, missing live Zerodha source, stale candle, stale tick/depth, missing live depth, synthetic depth, and quote/depth fetch failures.
- Added Zerodha quote/depth metadata into intraday live candle rows so real safeguards can verify live depth source and timestamps.
- Added broker API health guard for quote, margin, order, instrument, funds, order-book, and position failures; real orders pause when broker health is degraded.
- Added focused regression tests for tick rounding, SL-LIMIT validation, idempotency matching, stale/non-live data blocks, circuit/abnormal-move blocks, broker API pause, and duplicate-send avoidance.

Guardrails:

- No market orders for intraday stock terminal unless a separate reviewed feature explicitly enables them.
- No real order placement without a current Main App Real Money connection.
- No automatic real order unless real-auto confirmation, final revalidation, broker health, and risk gates all pass.
- No fallback simulated data in real-money order decisions.

### Completed 2026-06-03 - Intraday Active Trade Management, Paper Setup, and Backtest Flow

Implemented for the intraday feature:

- Added `intraday/active_trade_manager.py` with active trade health scoring and actions: `HOLD`, `TIGHTEN_SL`, `MOVE_SL_TO_BREAKEVEN`, `TRAIL_SL`, `PARTIAL_EXIT`, `FULL_EXIT`, `MODIFY_TARGET`, and `NO_ACTION`.
- Added breakeven SL, trailing SL, partial-exit simulation, weak-thesis full exit, dynamic target extension, and health-score logging.
- Enforced the core stoploss safety rule: active management can only tighten risk and must never widen an existing stoploss.
- Added real-order reconciliation hooks for entry/protective order state, including confirmed fills, partial filled quantity handling, protective SL/target placement after fill, broker-freeze on protective-order failure, and broker-status reconciliation for SL/target fills.
- Added real-mode SL/target management through `modify_order` on existing protective broker orders; active management does not create duplicate real stoploss orders while modifying.
- Added paper-account settlement behavior: losses and charges affect available balance at trade close; positive net profit is kept as pending session profit and released to available balance when the session is stopped/exported.
- Added intraday UI controls for paper balance edit/reset, setup save/reset, max open trades, active management, breakeven SL, trailing SL, partial exit, and trailing method.
- Added persistent setup storage in the intraday UI so saved setup values reload for later paper/backtest/real sessions.
- Added active trade panel fields for health, management action, R multiple, current/initial SL, current/initial target, last LTP, unrealized P&L, and management notes.
- Added `intraday_trade_management_events` table and Excel sheet `Trade Management Events`.
- Added focused tests for breakeven, SL-never-widens, trailing SL, partial exit, weak-health full exit, and real SL modification without duplicate SL placement.
- Confirmed backtest remains isolated from the live terminal and still produces an Excel export through the paper backtest/replay route.

Verification completed:

```powershell
python -m py_compile intraday\active_trade_manager.py intraday\models.py intraday\paper_account.py intraday\database.py intraday\export_excel.py intraday\order_lifecycle.py intraday\session_manager.py
python -m unittest tests.test_intraday_active_trade_manager tests.test_intraday_paper_account_and_lifecycle tests.test_intraday_session_manager tests.test_intraday_web_routes tests.test_intraday_news_engine tests.test_intraday_execution_safeguards tests.test_intraday_engine_service tests.test_intraday_margin_engine tests.test_intraday_models_and_orders tests.test_intraday_mode_manager_and_market_cue tests.test_intraday_indicators_and_scoring
python -m unittest discover tests
python process_flow_benchmark.py
python tick_storm_benchmark.py
```

### Completed 2026-06-03 - Intraday Real Kill Switch and Hard Entry Gates

Implemented for the intraday feature:

- Added broker-aware real-money kill switch actions:
  - cancel open selected-symbol MIS orders from the broker order book,
  - send emergency square-off market orders for selected-symbol MIS positions,
  - refetch orders and positions after emergency actions,
  - keep the real session frozen when flat-account verification is uncertain.
- Added a separate emergency market-order path so normal intraday stock entries remain `LIMIT_ONLY`.
- Added `kill_switch_report` to intraday status for audit/UI visibility.
- Added event blackout gates through locked settings and evaluation payloads, blocking new entries during configured RBI/results/macro/custom windows.
- Added stock eligibility gates across paper, backtest, and real modes:
  - blocked symbols,
  - MIS eligibility flag,
  - ASM/GSM/T2T-style flags,
  - minimum liquidity score,
  - maximum spread percentage,
  - maximum trap score,
  - optional minimum relative volume.
- Added `hard_gates` into the signal score breakdown so setup score remains separate from trade permission.
- Capped news influence in entry scoring; news can support or penalize a setup but cannot bypass structure, volume, liquidity, trap, risk, and hard eligibility gates.
- Added terminal journal visibility for event blackout blockers and kill-switch reports.
- Added focused tests for real emergency cancel/square-off/flat verification, event blackout blocking, and spread eligibility blocking.

Verification completed:

```powershell
python -m unittest tests.test_intraday_real_kill_switch_and_gates tests.test_intraday_active_trade_manager tests.test_intraday_session_manager tests.test_intraday_paper_account_and_lifecycle tests.test_intraday_indicators_and_scoring tests.test_intraday_execution_safeguards
python -m py_compile intraday\order_request.py intraday\zerodha_broker.py intraday\trade_gates.py intraday\models.py intraday\scoring.py intraday\session_manager.py
node --check web_static\intraday.js
python -m unittest discover tests
python process_flow_benchmark.py
python tick_storm_benchmark.py
```

### Completed 2026-06-03 - Intraday Critical Real Lifecycle Fixes

Implemented for the intraday feature:

- Fixed real active-manager `FULL_EXIT` handling so a real trade is not marked closed locally until a broker market square-off order is confirmed filled.
- Real active-manager exits now cancel the target order first, keep the stoploss protection live, send a broker emergency market square-off order, and reconcile the exit fill from the broker order book before closing local trade state.
- Added duplicate pending-exit protection so repeated active-management cycles do not submit multiple real square-off orders for the same trade.
- Fixed failed real protective stoploss placement after entry fill to immediately request emergency square-off handling instead of only freezing future orders.
- Kept normal intraday entries `LIMIT_ONLY`; market orders remain isolated to emergency/square-off handling.
- Added focused regression tests for real active-manager exit reconciliation and failed-SL emergency square-off.

### Added 2026-06-03 - Intraday Audit Future Improvement Queue

Future improvements from the intraday audit:

- Add a REAL-mode stop-session flatness check: block normal stop if selected-symbol MIS positions or open orders remain, or provide explicit square-off-and-stop / leave-live-and-stop choices.
- Enforce currently dormant risk settings: `cooldown_after_trade_seconds`, `cooldown_after_loss_seconds`, `no_trade_first_minutes`, `max_candles_without_progress`, and `stop_after_consecutive_losses`.
- Update risk state from actual trade outcomes, not only order attempts, so consecutive-loss and cooldown locks reflect real closed trades.
- Use `limit_order_timeout_seconds` instead of the hardcoded 60-second pending-entry timeout.
- Re-check pending approval expiry inside `approve_entry`, then revalidate latest LTP, spread, depth, candle/tick age, margin, open positions, price bands, and score before broker send.
- Improve kill-switch position verification by preserving both Kite `net` and `day` rows or preferring non-zero quantities instead of overwriting rows by `(exchange, symbol, product)`.
- Make multi-open-trade support complete in the UI and reports: render all `active_trades`, close all open backtest trades at day end, and show per-trade controls/status.
- Add caching, backoff, and rate-limit protection for live news and live/historical candle fetches so the 5-second engine loop does not hammer external providers.
- Add clearer news source health in the terminal for Zerodha Pulse, Google News RSS, manual news, and disabled/empty adapters.
- Preserve pre-modification stoploss/target values in trade-management event logs so `old_stoploss` and `old_target` are accurate.
- Add session-specific emergency tags or richer broker response metadata for easier emergency-order reconciliation.
- Add tests for overnight event blackout windows and symbol-scoped blackout windows if those scopes are introduced.

Verification required when implemented:

```powershell
python -m pytest tests\test_intraday_* tests\test_order_idempotency.py tests\test_partial_fill_lifecycle.py tests\test_live_kill_switch.py
python -m pytest
node --check web_static\intraday.js
```

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
