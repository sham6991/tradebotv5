# Algo Options Lab Blueprint

Date: 2026-05-10

This file is the current project blueprint and baseline reference. Future feature work should be made from a cloned/snapshotted copy of the project or a clearly separated working version, then documented back here only after the change is accepted.

## Future Change Rule

- Treat this blueprint as the stable baseline.
- Before major changes, create a copy/snapshot of the current project folder or use version control.
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

- NIFTY, CE, and PE are trimmed to their common datetime range.
- NIFTY EMA/RSI is recomputed after trimming to avoid previous-day carryover distortion.
- Option data is enriched with formula columns automatically.
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

## Verification Baseline

Last known checks performed:

- Python compile checks passed for changed active modules.
- Unit test suite passes:
  - `python -m unittest discover -s tests`
  - latest result: 25 tests OK.
- Current unit coverage includes:
  - Zerodha order manager mock lifecycle.
  - risk guard daily/profit/loss/square-off/kill-switch behavior.
  - report-only startup reconciliation.
  - live kill switch entry blocking.
  - partial-fill order details.
  - partial-fill order-history persistence/migration.
  - partial pending-entry lifecycle.
  - partial exit kill-switch protection.

## Recommended Next Major Upgrade

After this baseline, the next large change should be done in a cloned/snapshotted copy:

- Add structured event logs for high-risk order state transitions:
  - kill switch activation
  - startup reconciliation warning/error
  - partial entry
  - partial exit
  - order placed/open/complete/rejected/cancelled
- Add automated regression tests for backtest vs live decision parity.
- Add fuller startup reconciliation auto-repair only after report-only behavior is trusted.
- Add strategy replay mode from saved candles.

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
