# TradeBotV4 Blueprint

Date: 2026-05-18

TradeBotV4 is a private Python trading research and execution workspace for NIFTY option backtesting, paper trading, Zerodha live trading, session replay, order lifecycle audit, and recovery/safety checks.

This repository must remain private. Do not commit broker credentials, access tokens, generated reports, SQLite databases, CSV uploads, virtual environments, caches, or live-session state files.

## Change Rule

- Treat this blueprint as the current baseline.
- Before major trading-flow changes, commit the current state and work from a branch or separated working version.
- Do not overwrite working strategy, order, risk, or recovery logic without keeping the previous working version available.
- After accepted changes, update this blueprint with:
  - files affected
  - behavior changed
  - verification performed

## Current Project State

- Local project folder: `tradebotV4`
- New private GitHub target: `https://github.com/sham6991/tradebotV4`
- Default local web URL: `http://127.0.0.1:8006`
- Zerodha redirect URL: `http://127.0.0.1:8006/zerodha/callback`
- Postback remains disabled; order status, fill quantity, pending quantity, cancellation, rejection, and average price are refreshed by polling and reconciliation logic.

## Entry Points

- `main.py`
  - primary entry point for the web control center.
  - delegates to `web_app.main()`.
- `Start TradeBot Web.bat`
  - Windows launcher for the local web app.
- `web_app.py`
  - HTTP server, API routes, Zerodha login callback, backtest runner, live/paper session control, replay loading, recovery center, and network health checks.
- `web_static/index.html`
  - browser UI shell for dashboard, backtest, paper desk, live trading, session replay, and Zerodha setup.
- `desktop_app.py`
  - legacy Tkinter launcher for comparison.
- `ui.py`
  - desktop Tkinter app composition.
- `event_replay.py`
  - CLI session replay and JSON/text export.

## User Interfaces

### Web Control Center

The current primary interface is the browser UI served from `web_app.py`.

Major areas:

- Dashboard
  - feed status
  - ticks-per-second summary
  - feed backlog
  - account/margin snapshot
  - live order rows
  - live trade snapshot
- Network Health
  - Zerodha API reachability
  - optional authenticated profile, margin, and order-book checks
  - per-step latency and quality summary
- Recovery & Safety Center
  - reads persisted open-position, pending-entry, and kill-switch state.
  - compares local state with broker order status when LIVE Zerodha is connected.
  - gives restart/trading recommendations.
- Backtest
  - accepts CSV/Excel uploads or server paths.
  - stores uploads under ignored `data/uploads/`.
  - runs strategy/backtest path and exports reports under ignored `results/`.
  - includes a Backtest Live Data optimizer that logs in through its own Zerodha connection, fetches NIFTY/CE/PE historical candles for a user-selected date range and interval, sweeps only selected high-impact risk settings, and exports `livebacktesting_*.xlsx`.
- Paper Desk
  - uses Zerodha data connection for instruments/feed when connected.
  - uses persisted simulated paper balance and the same live session engine.
- Live Trading
  - real-money Zerodha mode.
  - requires broker connection and preflight checks.
- Session Replay
  - loads ignored SQLite session databases from `results/` or upload.
  - filters timeline by critical events, warnings, partial fills/exits, rejected/failed orders, kill switch, reconciliation, and unknown broker state.
- Zerodha URLs
  - separate Paper Data, Real Money, and Backtest Live Data login flows.
  - Real Money and Backtest Live Data block each other while either is connected.
  - displays redirect URL for the Zerodha app.

### Desktop UI

The Tkinter UI remains available through `desktop_app.py` and `ui.py`.

Major mixins/modules:

- `ui_backtest.py`
- `ui_live.py`
- `ui_live_runtime.py`
- `ui_replay.py`
- `ui_shared.py`
- `ui_zerodha_auth.py`
- `ui_theme.py`

## Trading Flow

1. NIFTY direction is calculated from:
   - `EMA20 - EMA50`
   - `RSI`
   - optional RSI reversal thresholds.
2. Bullish NIFTY conditions select CE.
3. Bearish NIFTY conditions select PE.
4. Sideways/neutral conditions skip trading without starting cooldown.
5. Option formula/scoring is applied only to the selected CE/PE dataset.
6. Entry is allowed only when option Buy Score meets `min_buy_score`.
7. Entry price uses:
   - next aligned option candle open plus `entry_offset`.
8. Exit conditions:
   - target
   - stoploss
   - time exit
   - manual square-off
   - emergency/kill-switch flow
   - configured square-off time
9. New entries are blocked while a position or pending entry exists.
10. Cooldown applies only after a trade/position is complete.

## Core Modules

- `strategy.py`
  - market trend logic.
  - option momentum and formula columns.
  - Buy Score / Buy Entry calculations.
  - scoring row construction for incremental live updates.
- `engine.py`
  - CE/PE routing.
  - option metadata parsing.
  - timestamp alignment.
  - datetime index maps.
  - entry offset calculation.
  - signal selection.
- `trading_core.py`
  - shared backtest trade lifecycle.
  - target, stoploss, time-exit simulation.
- `backtest.py`
  - dataset trimming.
  - indicator/formula preparation.
  - report export.
- `live_backtest_optimizer.py`
  - isolated Backtest Live Data historical optimizer.
  - fetches day-by-day Zerodha historical candles for NIFTY, CE, and PE.
  - optimizes only selected tested settings: safety points, time exit, trend thresholds, RSI thresholds, minimum Buy Score, minimum volume ratio, and max chase range.
  - keeps other Paper/Real settings unchanged when applying latest optimized results.
  - exports Summary, Optimized Settings, Base Settings, Optimization Steps, All Runs, Day Results, Best Trades, Setting Ranges, Fetch Log, and Contracts sheets.
- `execution_v2.py`
  - paper/live session lifecycle.
  - tick-to-candle processing.
  - live/paper order handling.
  - pending limit entry lifecycle.
  - partial fill / partial exit protection.
  - protective target/stoploss orders.
  - order status polling.
  - kill switch and risk integration.
  - persistence and session audit output.
  - feed dispatcher, reconnect, watchdog, backlog, and health metrics.
- `order_manager.py`
  - low-level Zerodha order adapter.
  - market, limit, and SL-M order placement.
  - cancellation, status, details, average price, and filled quantity access.
- `zerodha_client.py`
  - Zerodha Kite API wrapper.
  - instruments, historical candles, ticker startup, margin, profile, and order APIs.
- `zerodha_auth.py`
  - local auth store.
  - access token storage.
  - localhost callback server.
- `risk_guard.py`
  - max daily loss/profit.
  - max consecutive losses.
  - square-off time.
  - hard kill switch.
- `preflight.py`
  - settings, market data, broker, and market-hours validation.
- `position_reconciler.py`
  - startup/recovery reconciliation checks.
- `event_logger.py`
  - structured event normalization and persistence wrapper.
- `sqlite_store.py`
  - SQLite persistence for orders, trades, events, settings profile metadata, and recoverable session state.
- `session_audit.py`
  - end-of-session audit report.
- `reporting.py`
  - timestamped paths, Excel reports, and datetime formatting.
- `config_profile.py`
  - settings profile hashing/versioning while excluding secret/token-like fields.
- `candle_builder.py`
  - live tick aggregation into interval OHLCV candles.

## Backtest Behavior

- Backtests use the same `TradingEngine.find_trade()` decision path as live/paper for:
  - NIFTY trend.
  - CE/PE selection.
  - option timestamp alignment.
  - Buy Score gate.
  - entry offset.
  - target, stoploss, and time exit.
- NIFTY, CE, and PE are trimmed to their common datetime range.
- NIFTY EMA/RSI is recomputed after trimming.
- Option data is enriched with formula columns.
- Order handling is simulated from candle high/low.
- Reports are exported to ignored `results/`.
- Backtest Live optimizer:
  - requires the separate Backtest Live Data Zerodha connection.
  - uses the user-selected historical candle interval from the optimizer form.
  - does not optimize chart interval.
  - applies latest optimized results to Paper or Real Money only for settings actually swept by the optimizer.
  - derives `watch_buy_score = min_buy_score - 5`.
  - preserves balances, chart interval, lot size, max trades, profit points, entry offset, cooldown, and all other untouched settings when applying optimized results.

## Live And Paper Behavior

- Live ticks are aggregated into interval candles using `candle_builder.py`.
- Strategy decisions use completed candles plus the current active candle snapshot for live entry decisions when available.
- Paper and live modes share the same session engine, candle builder, risk guard, structured events, persistence, replay, and audit path.
- LIVE mode adds broker order placement, order-status polling, broker reconciliation, and real margin checks.
- Paper balance is a simulated account balance from the Paper settings profile, updates after completed paper trades, and is preserved across disconnects/sessions.
- Real Money balance/margin is fetched only from Zerodha, not from manual paper settings; disconnected real mode shows `Not Connected`.
- Paper/Real session candle interval is selected outside Risk Settings before starting a session. Paper/Real Risk Settings do not show `chart_interval`.
- Entry order behavior:
  - `entry_offset = 0`: market BUY.
  - `entry_offset != 0`: limit BUY at next option candle open plus offset.
  - stale pending limit entries are cancelled and logged.
- Partial pending-entry fills:
  - cancel remaining pending quantity.
  - open/manage only filled quantity.
  - use actual average fill price where available.
- Partial target/stoploss exits:
  - keep the position for manual review.
  - activate kill switch.
  - block new entries.
  - log critical event/order history rows.
- Live feed callbacks stay lightweight and enqueue tick batches.
- UI/web rendering is throttled and should never render all raw ticks from a full trading day.
- Active Orders and Live Trade UI state is forced to clear/complete after completed target/stoploss/manual exits.
- Tick tables for NIFTY/CE/PE are scrollable and capped in the visible table.

## Safety And Recovery

Safety controls include:

- max trades
- max daily loss
- max daily profit
- max consecutive losses
- square-off time
- manual square-off
- manual kill switch
- market-hours guard
- live broker connection requirement
- Zerodha margin check before live entry
- startup/recovery state review

Recovery state files and generated session artifacts are intentionally ignored by Git.

## Persistence

- Human-readable reports remain Excel-based.
- SQLite session databases store:
  - trades
  - orders
  - order history
  - structured events
  - recoverable open-position/pending-entry/kill-switch state
  - settings profile metadata
- JSON files store local live/paper recovery state and session audits.
- Ignored generated outputs include:
  - `results/`
  - `*.db`
  - `*.sqlite`
  - `*.xlsx`
  - `*.csv`
  - local auth/token/secret files
  - `data/uploads/` CSV contents through the `*.csv` rule
  - `.venv/`
  - `__pycache__/`

## Settings Profiles

Settings profiles live in `data/settings_profiles.json` and currently cover:

- backtest
- paper
- real

Profile data includes strategy, risk, lot size, order product, and square-off settings. Secret-like values are excluded from deterministic config profile hashes by `config_profile.py`.

Note: the current `real` profile can include the last fetched Zerodha margin value. This is not an auth secret, but it is account-sensitive operational data.

Current profile rules:

- `watch_buy_score` is derived as `min_buy_score - 5`.
- Paper simulated balance is preserved unless the user manually changes Paper Risk Settings balance.
- Applying backtest settings to live profiles preserves Paper balance, Real Money balance, `zerodha_margin_fetched`, and Paper/Real chart intervals.
- Applying latest Backtest Live optimizer results copies only optimizer-tested settings and preserves all other Paper/Real settings.

## Tests And Verification

Primary verification command:

```powershell
python -m unittest discover -s tests
```

Compile check:

```powershell
python -m compileall -q .
```

Targeted compile check already approved in this workspace:

```powershell
python -m py_compile zerodha_auth.py ui_zerodha_auth.py ui.py ui_live.py ui_live_runtime.py zerodha_client.py execution_v2.py
```

Current test areas include:

- strategy regressions
- config profile hashing/versioning
- event logging
- event replay
- session audit
- async SQLite store
- buffered Excel writer
- candle builder
- live candle memory trimming
- incremental live indicators/scoring
- timestamp index maps
- order manager
- order idempotency
- partial fill lifecycle
- risk guard
- live kill switch
- startup preflight
- position reconciler
- UI replay helpers
- live UI update throttling
- alert hooks
- Backtest Live Data connection locking
- Backtest Live optimizer export and interval propagation
- closed-trade UI state cleanup
- web feed input validation and disconnect behavior

The large candle-builder stress test is gated behind:

```powershell
$env:RUN_CANDLE_BUILDER_STRESS = "1"
python -m unittest tests.test_candle_builder_stress
```

## Git Hygiene

`.gitignore` protects:

- Python caches and test caches.
- virtual environments.
- local secrets and broker auth.
- generated trading reports.
- SQLite databases.
- CSV uploads and datasets.
- live/paper local state JSON.
- IDE/OS files.

Before publishing, confirm:

- `data/zerodha_auth.json` is ignored.
- `.venv/` is ignored.
- `results/` is ignored.
- `data/uploads/*.csv` is ignored.
- only source, tests, docs, static web assets, config defaults, and CI workflow are tracked.

## GitHub Checkpoint

This TradeBotV4 snapshot is intended to be published as a new private GitHub project:

- Owner: `sham6991`
- Repository: `tradebotV4`
- Visibility: private
- Initial branch: current local branch unless renamed before publish.
- Previous remote before V4 publish: `https://github.com/sham6991/tradebotV3.git`

## Recommended Next Work

1. Add a backtest workbook `Run Metadata` or `Summary` sheet with:
   - report/export timestamp
   - exporter/code version
   - settings hash/version
   - app process start time or code-loaded timestamp
   - final balance, trade count, win/loss summary, and workbook sheet guide.
   This should make stale-process/stale-export issues obvious from inside Excel.
2. Move account-sensitive runtime values, especially fetched real margin and live connection state, out of tracked settings profiles.
   - Keep default strategy/risk settings in `data/settings_profiles.json`.
   - Store runtime account/margin/session state in ignored runtime files under `results/` or an ignored `data/runtime/` folder.
3. Create one shared settings/profile module used by both web and desktop UI.
   - Move defaults, labels, blank-value fallback, profile load/save, and apply-backtest-to-live logic out of duplicated UI/web code.
   - Prevent future drift between `ui_shared.py` and `web_app.py`.
4. Add backtest-vs-live parity replay.
   - Run saved paper/live candles through the backtest decision path.
   - Compare signal time, CE/PE selection, Buy Score, entry price, target, stoploss, and skip reason.
   - Export a parity report for any mismatch.
5. Pin dependency versions in `requirements.txt`.
   - Lock `pandas`, `numpy`, `kiteconnect`, `openpyxl`, and `keyring` to known-good versions.
   - Re-test before upgrading dependencies.
6. Make real-money live start require fresh safety checks.
   - Recent network health pass.
   - Recent recovery check pass.
   - Fresh margin check.
   - Market-hours/preflight pass.
   - No restored kill-switch state.
7. Add a paper-first guard after applying backtest settings to live profiles.
   - Require or strongly warn for a paper run/session before starting real money with newly copied settings.
8. Improve broker/feed/order exception classification.
   - Keep UI catch-all behavior, but classify runtime errors as network, auth, margin, rejected, timeout, unknown broker state, or reconciliation-required.
9. Split `execution_v2.py` into smaller ownership modules when making the next major live-runtime change.
   - Suggested modules: live session orchestration, order lifecycle, feed runtime, recovery runtime, and session persistence.
10. Mark or move legacy paths clearly.
    - `execution.py`, Tkinter desktop paths, and older V3-named text should be documented as legacy/comparison paths or moved under a future `legacy/` folder.
11. Add performance benchmark thresholds.
    - Turn candle-builder, tick-storm, process-flow, and scoring benchmarks into pass/fail checks with reasonable thresholds.
    - Use them before optimizing or changing live decision-path code.
12. Rename remaining user-facing `TradeBotV3` strings in auth/keyring text if a clean V4 identity is required.
13. Run a real paper/live trial latency review before further decision-path optimization.
14. Run the full test suite after the web/backtest baseline is committed:
    ```powershell
    python -m unittest discover -s tests
    python -m compileall -q .
    ```
