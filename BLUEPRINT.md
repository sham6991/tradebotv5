# Algo Options Lab Blueprint

Date: 2026-05-11

This file is the current project blueprint and baseline reference. The project is now under Git version control and backed up to a private GitHub repository.

## Future Change Rule

- Treat this blueprint as the stable baseline.
- Before major changes, create a Git commit and work from a branch or clearly separated working version.
- Do not overwrite working trading logic without keeping the previous working version available.
- After every accepted change, update this blueprint with:
  - what changed
  - files affected
  - behavior changed
  - tests/smoke checks done

## Active Entry Points

- `main.py` launches the Tkinter app.
- `ui.py` is the active UI.
- `execution_v2.py` is the active live/paper trading engine.
- `execution.py` is old/inactive for the UI and should not be used for new live-flow changes unless intentionally migrated.
- `event_replay.py` powers the read-only Session Replay mode.

## Current Trading Flow

1. NIFTY data decides market direction using:
   - `crossover = EMA20 - EMA50`
   - `RSI`
2. If crossover is greater than the bullish threshold and RSI is greater than `RSI Bull`:
   - select CE.
3. If crossover is less than the bearish threshold and RSI is less than `RSI Bear`:
   - select PE.
4. If crossover is between thresholds or RSI is between `RSI Bear` and `RSI Bull`:
   - no trade, cooldown applies.
5. Once CE/PE is selected:
   - option formula scoring is applied only to selected option data.
6. Entry is allowed when option Buy Score meets the user-defined `Min Buy Score`.
7. Entry price:
   - `next option candle open + Entry Offset`
   - default offset is `-2`
8. Exit conditions only:
   - target
   - stoploss
   - time exit
   - manual/emergency square-off in live mode
   - auto square-off time in live mode
9. No new trade is allowed while a position or pending entry exists.
10. Cooldown applies after completed trade exit and after sideways/no-trade NIFTY decisions.

## Backtest Behavior

- Backtesting uses the same `TradingEngine.find_trade()` decision path as live/paper for:
  - NIFTY trend
  - CE/PE selection
  - option timestamp alignment
  - Buy Score / Buy Entry gate
  - entry offset
  - target and stoploss calculation
- NIFTY, CE, and PE are trimmed to their common datetime range.
- NIFTY EMA/RSI is recomputed after trimming to avoid previous-day carryover distortion.
- Option data is enriched with formula columns automatically.
- Backtest order handling is simulated from candle high/low:
  - target if candle high reaches target
  - stoploss if candle low reaches stoploss
  - otherwise time exit
  - order status is `PAPER`
- Reports are exported to Excel:
  - main trade file
  - CE trades
  - PE trades
  - candles
  - skips
- Date/time output format is standardized as:
  - `YYYY-MM-DD HH:MM:SS`

## Live/Paper Behavior

- Live ticks from Zerodha are aggregated into real OHLCV candles using `candle_builder.py`.
- Strategy decisions use completed interval candles.
- Live mode uses a tabbed log area:
  - Log Trade
  - Live Trade
  - Order History
- Tick log shows raw ticks, separated into tabs:
  - NIFTY
  - CE
  - PE
- Live exits can react to live option tick price for target/stoploss.
- Paper mode uses manual balance.
- Real-money mode fetches Zerodha available margin and uses that as the starting balance.
- Paper mode and live mode use the same live session engine, candle builder, signal engine, risk controls, and event/audit path. Live mode adds broker order placement and broker-state reconciliation.

## Session Replay Behavior

- Session Replay is the third main UI workspace beside Backtest and Live Desk.
- It is read-only and must never connect to Zerodha or modify session databases.
- It loads previous SQLite session databases from `results/`.
- It shows:
  - summary counts
  - timeline rows
  - critical/warning highlights
  - partial-fill/partial-exit highlights
  - rejected/failed order highlights
  - kill switch and reconciliation events
  - selected event/order payload JSON
- It can export replay reports to text or JSON.
- CLI usage:
  - `python event_replay.py results\your_session.db`
  - `python event_replay.py results\your_session.db --format json --output results\session_replay.json`

## Live Order Rules

- `Entry Offset = 0`
  - place market BUY.
- `Entry Offset != 0`
  - place limit BUY at `next candle open + entry offset`.
- If limit BUY is not filled within one minute:
  - cancel order
  - log `TIME EXHAUSTION CANCELLATION` in remarks.
- Real-money orders use actual average fill price and filled quantity where Zerodha returns them.
- Exit order failure does not falsely close the position.
- Low-level order calls are routed through `order_manager.py`.
- Pending BUY partial fill handling:
  - if filled quantity is greater than zero but less than ordered quantity, cancel the remaining pending quantity.
  - open/manage the position only for the filled quantity.
  - use actual average fill price.
  - place target and stoploss only for the filled quantity.
- Partial target/stoploss exit handling:
  - do not auto-finalize the trade.
  - keep the open position for manual review.
  - activate kill switch.
  - block new entries.
  - log a critical event/order-history row.
- Live logs expose partial-fill visibility:
  - ordered quantity
  - filled quantity
  - pending quantity
  - cancelled quantity
  - partial-fill flag

## Safety Controls

UI currently supports:

- Max trades
- Max daily loss
- Max daily profit
- Max consecutive losses
- Square-off time
- Manual square-off button
- Manual kill switch button
- Market-hours entry guard
- Margin check before live entry

Runtime safety modules:

- `risk_guard.py`
  - daily loss block
  - daily profit block
  - consecutive loss block
  - square-off time block
  - hard kill switch block
- `position_reconciler.py`
  - report-only startup reconciliation for restored pending/open state.
  - logs warnings/errors when local state and broker order state disagree.
  - startup reconciliation `ERROR` activates the kill switch.

## Persistence And Logs

- Excel remains the human-readable report output.
- SQLite database files are created beside live report files for:
  - orders
  - trades
  - events
  - recoverable state
- SQLite `order_history` stores current-session order events and partial-fill quantity fields.
- Open positions are persisted to JSON and SQLite state.
- Pending limit entries are persisted to JSON and SQLite state.
- Kill switch state is persisted for the active session.
- Structured order lifecycle events are written into the existing SQLite `events` table through `event_logger.py`.
- Session close writes a JSON audit report when a live/paper session database exists.
- Alert hooks surface high-risk events without changing trading decisions:
  - kill switch activation
  - startup reconciliation errors
  - partial entry fills
  - partial exit fills
  - unknown broker state after order timeout/error

## Important Files

- `strategy.py`
  - market trend logic
  - option formula/scoring logic
- `engine.py`
  - trade selection
  - NIFTY to CE/PE routing
  - timestamp alignment
  - entry offset calculation
- `trading_core.py`
  - backtest trade lifecycle
- `execution_v2.py`
  - paper/live session lifecycle
  - live order lifecycle coordination
  - live candle processing
  - pending order cancellation
  - risk guard integration
  - startup reconciliation integration
  - kill switch integration
- `order_manager.py`
  - low-level Zerodha order adapter
  - market/limit/SL-M placement
  - cancellation/status/fill-price/fill-quantity/order-details
- `risk_guard.py`
  - live risk blocks and kill switch state
- `position_reconciler.py`
  - report-only startup state reconciliation
- `zerodha_client.py`
  - Zerodha API wrapper
- `ui.py`
  - Tkinter UI
  - backtest, paper, live, logs, tick tabs
- `candle_builder.py`
  - raw tick to interval candle aggregation
- `sqlite_store.py`
  - SQLite audit/state persistence
- `reporting.py`
  - Excel export and datetime formatting
- `event_logger.py`
  - structured event payload model
- `event_replay.py`
  - read-only session replay timeline and reports
- `session_audit.py`
  - end-of-session audit JSON
- `preflight.py`
  - live/paper startup validation
- `ui_replay.py`
  - Session Replay UI workspace

## Verification Baseline

Last known checks performed:

- Python compile checks passed for the full project:
  - `python -m compileall -q .`
- Unit test suite passes:
  - `python -m unittest discover -s tests`
  - latest result: 80 tests OK, 1 skipped.
- The skipped test is the gated 10-million tick candle-builder stress test:
  - `RUN_CANDLE_BUILDER_STRESS=1`
- A direct decision-engine smoke check confirmed:
  - bullish NIFTY selects CE
  - bearish NIFTY selects PE
  - Buy Score gate remains active
- A direct backtest-core smoke check confirmed:
  - CE trade
  - Buy Entry `BUY`
  - Buy Score `85.0`
  - target exit
  - order status `PAPER`
- Current unit coverage includes:
  - Zerodha order manager mock lifecycle.
  - risk guard daily/profit/loss/square-off/kill-switch behavior.
  - report-only startup reconciliation.
  - live kill switch entry blocking.
  - partial-fill order details.
  - partial-fill order-history persistence/migration.
  - partial pending-entry lifecycle.
  - partial exit kill-switch protection.
  - structured event payload shape and lifecycle event logging.
  - event replay summaries/highlights.
  - session audit summaries.
  - buffered Excel writer.
  - async SQLite store wrapper.
  - optimized candle builder behavior.
  - incremental live indicator/scoring updates.
  - timestamp index maps.
  - live UI update throttling.
  - live candle memory trimming.
  - order idempotency.
  - alert hooks.
  - Session Replay UI helper behavior.

## Recommended Next Major Upgrade

After this baseline, the next work should be done from Git commits/branches:

1. Add backtest vs live/paper parity reporting:
   - compare what the strategy would have done from saved candles against what the live/paper session actually did.
2. Add fuller startup reconciliation auto-repair only after report-only behavior is trusted.

## GitHub Repository Checkpoint - 2026-05-11

- Private GitHub repository:
  - `https://github.com/sham6991/tradebotV3`
- Local branch:
  - `main`
- Initial private snapshot commit:
  - `c894639 Initial private TradeBotV3 snapshot`
- `.gitignore` protects:
  - `results/`
  - `*.db`
  - `*.xlsx`
  - `*.csv`
  - `__pycache__/`
  - virtual environments
  - token/secret-like local files
- Current tracked source snapshot intentionally excludes generated trading reports and broker/session secrets.

## Accepted Upgrade Log - 2026-05-12

- Added config/profile versioning through `config_profile.py`.
- Settings profile versioning now:
  - builds a deterministic sanitized settings hash.
  - ignores session IDs and secret/token-like fields.
  - stores `settings_hash`, `settings_version`, and `settings_schema_version` in live/paper session rows.
  - adds settings profile fields to event and order-history payloads.
  - exposes settings profile metadata in session audits and replay reports.
- Files affected:
  - `config_profile.py`
  - `execution_v2.py`
  - `sqlite_store.py`
  - `reporting.py`
  - `session_audit.py`
  - `event_replay.py`
  - `tests/test_config_profile.py`
  - `tests/test_sqlite_store_order_history.py`
  - `tests/test_session_audit.py`
  - `tests/test_event_replay.py`
  - `BLUEPRINT.md`
- Behavior changed:
  - Live/paper session databases now carry a reproducible settings profile version for audit and replay comparison.
  - Trading decisions are unchanged.
- Tests/smoke checks done:
  - `python -m unittest tests.test_config_profile tests.test_sqlite_store_order_history tests.test_session_audit tests.test_event_replay` passed: 10 tests OK.
  - `python -m compileall -q .` passed.
  - `python -m unittest discover -s tests` passed: 90 tests OK, 1 skipped.

- Added strategy regression fixtures in `tests/test_strategy_regression.py`.
- Strategy regressions cover:
  - bullish NIFTY selects CE.
  - bearish NIFTY selects PE.
  - sideways NIFTY gives no trade.
  - Buy Score below threshold blocks entry.
  - target exit.
  - stoploss exit.
  - time exit.
- Files affected:
  - `tests/test_strategy_regression.py`
  - `BLUEPRINT.md`
- Behavior changed:
  - No trading behavior changed; core strategy and backtest paths now have explicit regression coverage.
- Tests/smoke checks done:
  - `python -m unittest tests.test_strategy_regression` passed: 7 tests OK.
  - `python -m compileall -q .` passed.
  - `python -m unittest discover -s tests` passed: 87 tests OK, 1 skipped.

- Added GitHub Actions CI workflow in `.github/workflows/ci.yml`.
- CI runs on push and pull request events.
- CI installs `requirements.txt`, runs `python -m compileall -q .`, and runs `python -m unittest discover -s tests`.
- Files affected:
  - `.github/workflows/ci.yml`
  - `BLUEPRINT.md`
- Behavior changed:
  - GitHub will automatically verify compile checks and unit tests for pushed branches and pull requests.
- Tests/smoke checks done:
  - `python -m compileall -q .` passed.
  - `python -m unittest discover -s tests` passed: 80 tests OK, 1 skipped.

## Accepted Upgrade Log - 2026-05-11

- Added structured event model through `event_logger.py`.
- Migrated high-risk lifecycle events to structured event payloads.
- Added alert hooks for high-risk live events.
- Optimized `candle_builder.py` for high-volume tick aggregation.
- Added candle builder validation, out-of-order protection, volume reset handling, snapshots, flushing, and bounded active keys.
- Added incremental live candle/indicator/scoring append path.
- Added timestamp index maps for faster NIFTY-to-option alignment.
- Added buffered Excel writer.
- Added async SQLite write wrapper.
- Added live UI throttling and health snapshot fields.
- Added live candle memory trimming when safe.
- Added session audit JSON.
- Added event replay engine and CLI.
- Added Session Replay as a third UI workspace.
- Added preflight validation.
- Added order idempotency.
- Added broker error classification for timeout/unknown-state handling.
- Verified core CE/PE, Buy Score, backtest, order, and risk paths remain intact.

## Reliability Upgrade Log - 2026-05-10

Snapshots were created before major refactor steps:

- `tradebotV3_snapshot_order_refactor_20260510_223236`
- `tradebotV3_snapshot_risk_guard_20260510_224501`
- `tradebotV3_snapshot_reconciler_20260510_224855`
- `tradebotV3_snapshot_kill_switch_20260510_225345`
- `tradebotV3_snapshot_partial_fill_api_20260510_225932`
- `tradebotV3_snapshot_partial_fill_visibility_20260510_230251`
- `tradebotV3_snapshot_partial_fill_lifecycle_20260510_230937`

Accepted reliability changes:

- Split low-level order handling into `order_manager.py`.
- Added mock Zerodha lifecycle tests.
- Split live risk checks into `risk_guard.py`.
- Added report-only startup reconciliation through `position_reconciler.py`.
- Added hard kill switch that blocks new entries and logs state.
- Added partial-fill order detail API.
- Added partial-fill visibility in live tabs and SQLite order history.
- Added first partial-fill lifecycle handling for pending BUY entries and protective exits.

## High-Volume Live Feed Readiness

Added after UI/runtime hardening work on 2026-05-10:

- The live feed path must remain separated from Tkinter rendering.
- Zerodha websocket callbacks must stay lightweight and enqueue tick batches only.
- Tick/session processing must run through a bounded dispatcher worker.
- UI tick rendering must be throttled and must never attempt to render all raw ticks from a full trading day.
- Dashboard refresh and Zerodha margin refresh must be non-blocking.
- Real-money balance display must show `not connected` until Zerodha available margin is fetched.
- Raw tick display is for recent visibility only; it is not the system of record.
- Excel exports must remain trade/candle/report focused, not raw tick storage.
- SQLite writes must stay outside the raw tick hot path unless batched.

### Million-Tick Day Requirements

Before calling live mode commercial-grade for full-day high-volume usage:

- Add and run a synthetic tick-storm benchmark.
- Measure:
  - processed tick count
  - elapsed time
  - ticks per second
  - dispatcher backlog
  - dropped tick batches
  - approximate memory pressure where practical
- Add live feed health indicators in the UI:
  - ticks per second
  - dispatcher backlog
  - dropped batches
  - last tick time
- UI tick log should coalesce display data:
  - keep latest tick per instrument
  - keep only bounded recent sample lines
  - never create one permanent UI row per raw tick
- If queue pressure rises, stale display data may be discarded, but trading/session processing should continue receiving queued tick batches in order.
- Add restart/recovery testing for:
  - open position
  - pending entry
  - feed reconnect
  - square-off state

### Current High-Volume Status

- Improved and safer than the original single-threaded UI callback path.
- Not yet proven for a full day of millions of ticks until the benchmark and stress checks are run.
