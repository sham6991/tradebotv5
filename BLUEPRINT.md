# TradeBotV5 Blueprint

Date: 2026-05-21

TradeBotV5 is a private Python trading research and execution workspace for NIFTY option backtesting, NIFTY RSI reversal optimization, paper trading, Zerodha live trading, session replay, order lifecycle audit, and recovery/safety checks.

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

- Local project folder: `tradebotV5`
- New private GitHub target: `https://github.com/sham6991/tradebotV5`
- Default local web URL: `http://127.0.0.1:8007`
- Zerodha redirect URL: `http://127.0.0.1:8007/zerodha/callback`
- Postback remains disabled; order status, fill quantity, pending quantity, cancellation, rejection, and average price are refreshed by strict polling, optional KiteTicker order updates where available, and full broker reconciliation.

## Entry Points

- `main.py`
  - primary entry point for the web control center.
  - delegates to `web_app.main()`.
- `Start TradeBot Web.bat`
  - Windows launcher for the local web app.
  - reuses an already-running server on `127.0.0.1:8007`, or starts one in a visible server window, waits for readiness, then opens the browser.
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
  - includes a NIFTY RSI Reversal Research Optimizer that logs in through its own research-data Zerodha connection, fetches only NIFTY historical candles for last 1, 2, 3, or 6 months, ranks RSI reversal bullish/bearish values, and exports `nifty_optimizer_*.xlsx`.
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
  - Paper Data means data/feed only and cannot place real orders.
  - Real Money can place live Zerodha orders.
  - Backtest Live Data means historical NIFTY research data only for the RSI reversal optimizer.
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
   - standard bullish/bearish EMA-diff and RSI thresholds.
   - RSI reversal thresholds gated by user-defined EMA-diff reversal conditions.
2. Bullish NIFTY conditions select CE when either:
   - `EMA20 - EMA50 > bullish_threshold` and `RSI > rsi_bull`
   - `EMA20 - EMA50 >= bullish_reversal_condition` and `RSI > rsi_reversal_bullish`
3. Bearish NIFTY conditions select PE when either:
   - `EMA20 - EMA50 < bearish_threshold` and `RSI < rsi_bear`
   - `EMA20 - EMA50 <= bearish_reversal_condition` and `RSI < rsi_reversal_bearish`
4. Sideways/neutral conditions skip trading without starting cooldown, but must log a compact no-trade reason such as `NIFTY_NEUTRAL`, `EMA_DIFF_INSIDE_RANGE`, `RSI_NO_DIRECTION`, or `REVERSAL_GATE_NOT_MET`.
5. Fast OHLCV option-entry scoring is applied only to the selected CE/PE dataset.
6. Option entry uses only option Open, High, Low, Close, and Volume. It does not use option EMA, RSI, Supertrend, VWAP, or old option indicator-score weights.
7. Entry is allowed only when the shared fast OHLCV evaluator passes:
   - candle data validation
   - `Early Score`
   - hard rejection filters
   - mandatory `Main Fast Trigger`
   - market-entry or buy-limit thresholds.
8. Entry price uses separate signal and execution fields:
   - `SignalReferencePrice`: option candle close used for signal calculation and audit.
   - `ActualEntryPrice`: broker `average_price` after confirmed fill in LIVE mode.
   - `PaperEntryPrice`: simulated from the configured paper fill model.
   - buy-limit entry: `Close - min(max(AvgRange10 * BuyLimitOffsetMultiplier, MinimumOffset), MaximumOffset)`.
   - live/paper buy-limit entries expire after `BuyLimitValiditySeconds`.
   - target and stoploss are calculated from `ActualEntryPrice` in LIVE mode and `PaperEntryPrice` in paper mode, never from signal close.
9. Exit conditions:
   - target
   - stoploss
   - time exit
   - manual square-off
   - emergency/kill-switch flow
   - configured square-off time
10. New entries are blocked while a position, pending entry, active exit order, or order placement is in progress. A global session trade lock is acquired before every order placement and re-checks all blocking conditions under the lock so CE and PE cannot both enter.
11. Cooldown applies only after a trade/position is complete. Missed buy-limit cooldown can also block re-entry for the configured number of candles.

## Core Modules

- `strategy.py`
  - market trend logic.
  - CE/PE trend selection using standard threshold conditions plus EMA-gated RSI reversal conditions.
  - delegates option entry scoring to `fast_ohlcv_entry.py`.
  - builds NIFTY and option scoring rows for reports/live logs.
- `fast_ohlcv_entry.py`
  - shared OHLCV-only option-entry evaluator used by backtest, paper, and real-money live mode.
  - calculates candle features, `Early Score`, hard rejection filters, mandatory `Main Fast Trigger`, aggressive live-entry checks, buy-limit price, and backtest limit-fill status.
  - excludes the current candle from `RecentHigh3` and `RecentLow3`.
  - uses proper wick/body formulas based on `max(open, close)` and `min(open, close)`.
  - applies gap-spike, stale-volume, optional chop, spread, one-attempt-per-candle, and missed-limit cooldown support.
  - scoring row construction for incremental live updates.
- `engine.py`
  - CE/PE routing.
  - option metadata parsing.
  - timestamp alignment.
  - datetime index maps.
  - fast OHLCV market/limit entry selection.
  - signal selection.
- `backtest_runtime.py`
  - backtesting-only trade lifecycle and OHLC execution simulation.
  - conservative target, stoploss, trailing stoploss, and time-exit simulation.
- `trading_core.py`
  - compatibility import for `BacktestTradingCore`.
  - not used by paper or real-money execution.
- `backtest.py`
  - dataset trimming.
  - indicator/formula preparation.
  - report export.
- `live_backtest_optimizer.py`
  - isolated Backtest Live Data NIFTY optimizer.
  - fetches day-by-day Zerodha historical candles for NIFTY only.
  - optimizes only `rsi_reversal_bullish` and `rsi_reversal_bearish` report values.
  - bullish setup: current candle has `EMA20 < EMA50` and RSI meets the candidate bullish reversal value; from the next candle onward it measures the first `EMA20 >= EMA50` confirmation and the first `EMA20 - EMA50 >= 20` target cross.
  - bearish setup: current candle has `EMA20 > EMA50` and RSI meets the candidate bearish reversal value; from the next candle onward it measures the first `EMA20 <= EMA50` confirmation and the first `EMA20 - EMA50 <= -15` target cross.
  - ranks candidates by confirmation rate, target-cross rate, next-candle confirmation, sample size, and least confirmation/target time.
  - exports Workbook Guide, Summary, Optimized RSI Values, Candidate Runs, Bullish Events, Bearish Events, and Fetch Log sheets.
- `execution_v2.py`
  - compatibility facade for the live/paper execution runtime.
  - preserves existing imports of `Executor` and `LivePaperSession` for web, desktop, tests, and benchmarks.
- `live_session.py`
  - paper/live session lifecycle.
  - tick-to-candle processing.
  - live/paper order handling.
  - pending limit entry lifecycle.
  - partial fill / partial exit protection.
  - protective target/stoploss orders.
  - order status polling.
  - kill switch and risk integration.
  - persistence and session audit output.
- `broker_reconciliation.py`
  - LIVE startup reconciliation helpers for local-vs-Zerodha state checks.
- `risk_runtime.py`
  - live/paper risk-state synchronization, kill-switch activation, trading-block checks, and square-off-time helpers.
- `session_persistence.py`
  - recoverable open-position, pending-entry, and kill-switch persistence helpers used by `LivePaperSession`.
- `feed_runtime.py`
  - `Executor` runtime facade for Zerodha connection, live-history fetch, and session startup.
  - feed dispatcher, reconnect, watchdog, backlog, and health metrics.
- Future live-runtime work should continue splitting `live_session.py` before adding more complex real-money order handling:
  - `candle_runtime.py`: tick-to-candle processing.
  - `strategy_runtime.py`: signal generation and decision events.
  - `order_lifecycle.py`: entry, target, SL, cancel, partial fill, and OCO handling.
  - `broker_reconciliation.py`: continue expanding local vs Zerodha runtime reconciliation if needed.
  - `risk_runtime.py`: continue moving live risk checks when ownership is clear.
  - `session_persistence.py`: continue moving candle and audit persistence helpers when ownership is clear.
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
  - settings, fast OHLCV relationship validation, market data, broker, and market-hours validation.
- `settings_service.py`
  - shared settings/profile ownership for web and desktop paths.
  - owns defaults, labels, blank-value fallback, runtime parsing, profile load/save, account-runtime exclusion, apply-backtest-to-live, and real account snapshot persistence.
- `settings_validation.py`
  - shared validation for fast OHLCV settings relationships before save/run.
  - blocks invalid settings such as market score below buy-limit score, hard wick below trigger wick, maximum offset below minimum offset, or invalid backtest limit-fill mode.
- `parity_replay.py`
  - compares PAPER/LIVE SQLite order-history entries against the same NIFTY/CE/PE candles replayed through the backtest decision path.
  - reports CE/PE side, entry type, order type, entry price, target, stoploss, Early Score, and missing/extra entry mismatches.
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

Real-money execution should be controlled only from the Web Control Center unless the desktop path is fully maintained to the same standard. `desktop_app.py`, `ui.py`, and related Tkinter paths are legacy/comparison paths for backtest, replay, and paper testing.

## Backtest Behavior

- Backtests use the same `TradingEngine.find_trade()` decision path as live/paper for:
  - NIFTY trend.
  - CE/PE selection.
  - `bullish_reversal_condition` and `bearish_reversal_condition` gates for RSI reversal entries.
  - option timestamp alignment.
  - fast OHLCV option entry.
  - market vs buy-limit decision.
  - target, stoploss, and time exit.
- NIFTY, CE, and PE are trimmed to their common datetime range.
- NIFTY EMA/RSI is recomputed after trimming.
- Option data is enriched with fast OHLCV entry columns.
- Market-entry handling supports `MarketEntryBacktestMode`:
  - `SIGNAL_CLOSE`
  - `NEXT_CANDLE_OPEN`
  - `SIGNAL_CLOSE_PLUS_SLIPPAGE`
  - `NEXT_CANDLE_OPEN_PLUS_SLIPPAGE`
- Production-like backtests should default to `NEXT_CANDLE_OPEN_PLUS_SLIPPAGE` and include `SlippagePoints`, `SlippagePercent`, and `MinimumSlippagePoints`.
- Buy-limit backtest handling uses `BacktestLimitFillMode`:
  - `SIMPLE`: filled if next candle low is at or below the buy limit.
  - `CONSERVATIVE`: filled only if next candle low reaches the buy limit, next close is at or above the limit, and next candle is green.
  - `STRICT`: limit entries are not tested from OHLC-only data.
- Production-like buy-limit backtests should enable `LimitFillRequiresSignalStillValid`, re-checking that the next candle has not broken `RecentLow3` or produced a severe red rejection before accepting the simulated fill.
- Backtest exits use a conservative OHLC sequence:
  - entry candle: complete BUY, create virtual target/stoploss, ignore same-candle target, and check only stoploss.
  - if entry candle low touches stoploss, exit as `STOPLOSS_SAME_CANDLE`.
  - from the next candle onward: update trailing stoploss first when enabled, check low against current stoploss, then check high against target.
  - after the entry candle, target can execute only if stoploss/trailing stoploss was not touched first.
  - possible backtest exit reasons are `STOPLOSS_SAME_CANDLE`, `STOPLOSS`, `TRAILING_STOPLOSS`, `TARGET`, and `TIME_EXIT`.
- Optional Trailing Stop Loss risk setting applies to BACKTEST, PAPER, and LIVE_ZERODHA after a BUY is complete:
  - defaults disabled.
  - starts only after `trailing_start_points` profit, default 10.
  - moves stoploss upward in `trailing_step_points` increments, default 5, locking `trailing_lock_points`, default 5.
  - requires `profit_points > 10` when enabled.
  - never moves stoploss downward.
- Reports are exported to ignored `results/`.
- Backtest Live NIFTY optimizer:
  - requires the separate Backtest Live Data Zerodha connection.
  - uses the user-selected historical candle interval from the optimizer form.
  - date range is selected as last 1, 2, 3, or 6 months.
  - does not fetch CE/PE option candles.
  - does not optimize profit, stop, time-exit, fast OHLCV entry, volume, or risk settings.
  - does not apply results to Paper or Real Money profiles; paper/live trading settings and process flow remain separate.
  - reports RSI reversal values and event diagnostics only.

## Live And Paper Behavior

- Live ticks are aggregated into interval candles using `candle_builder.py`.
- Strategy decisions use completed candles by default.
- Live forming-candle decisions are separated from closed-candle decisions:
  - with aggressive live entry OFF, a forming candle can only produce `SETUP FORMING`; no order is placed.
  - with aggressive live entry ON, a forming candle can place a market entry only when `aggressive_entry_score` and all stricter aggressive checks pass.
  - `aggressive_setup_score` is display/status-only for `SETUP FORMING` and must never place an order by itself.
- If `fast_ohlcv_entry_enabled` is false, option entry is disabled and `FAST_OHLCV_DISABLED` is logged. The system must not silently fall back to legacy option buy-score logic.
- Paper and live modes share the same session engine, candle builder, risk guard, structured events, persistence, replay, and audit path.
- LIVE mode adds broker order placement, order-status polling, broker reconciliation, and real margin checks.
- Paper balance is a simulated account balance from the Paper settings profile, updates after completed paper trades, and is preserved across disconnects/sessions.
- Real Money balance/margin is fetched only from Zerodha, not from manual paper settings; disconnected real mode shows `Not Connected`.
- Paper/Real session candle interval is selected outside Risk Settings before starting a session. Paper/Real Risk Settings do not show `chart_interval`.
- Every trade log must include `chart_interval`, `candle_start_time`, `candle_end_time`, `signal_generated_at`, `order_placed_at`, and `order_filled_at`.
- Entry order behavior:
  - `MARKET ENTRY`: market BUY based on a signal reference price, with position opening only after Zerodha confirms execution and returns broker `average_price`.
  - `BUY LIMIT ENTRY`: limit BUY at the fast OHLCV calculated buy-limit price.
  - Zerodha `order_id` means broker registration/request acceptance only; it must create `ENTRY_ORDER_PLACED`, not `POSITION_OPEN`.
  - After placement, the engine polls order details/history until the order reaches `COMPLETE`, `OPEN`, `REJECTED`, `CANCELLED`, a partial state, or `UNKNOWN`.
  - Unfilled live/paper limit entries are cancelled after `BuyLimitValiditySeconds`; they are not converted to market orders.
  - stale pending limit entries are cancelled and logged.
  - one entry attempt per symbol/candle is enforced when enabled.
  - missed limit orders activate `MissedLimitCooldownCandles` and are not chased.
  - live/paper spread protection blocks entries when bid/ask spread is available and exceeds `MaxSpreadPoints`.
  - after a cancel request, the engine must re-poll status and inspect filled quantity before deciding `MISSED_LIMIT_ENTRY`, `PARTIAL_ENTRY_FILLED`, `POSITION_OPEN`, or `UNKNOWN_CANCEL_STATE`.
- Partial pending-entry fills:
  - cancel remaining pending quantity.
  - open/manage only filled quantity.
  - use broker average fill price where available.
  - place protective exits for filled quantity only.
- Protective exits after entry fill:
  - calculate target and stoploss from broker average fill price.
  - validate target SELL LIMIT price, stoploss SELL SL-M trigger, quantity, product, exchange, trading symbol, tick size, and positive price constraints before placement.
  - place target SELL LIMIT and stoploss SELL SL-M as an actively monitored OCO pair.
  - if target completes, cancel stoploss immediately and mark the trade exited by target only after stoploss is cancelled or safely inactive.
  - if stoploss completes, cancel target immediately and mark the trade exited by stoploss only after target is cancelled or safely inactive.
  - if both exit orders complete, activate kill switch, block new trades, and reconcile broker positions immediately.
  - if cancellation status is unknown, mark `UNKNOWN_BROKER_STATE`, block new trades, and reconcile.
- Trailing Stop Loss in LIVE_ZERODHA:
  - uses the actual completed BUY average price.
  - keeps exactly one active SELL SL-M protective order for the position.
  - modifies the existing SL-M order trigger upward using its original `stoploss_order_id`.
  - never cancels/recreates the SL-M order just to trail.
  - modifies only when the next fixed trailing level is reached and the SL-M status is modifiable.
  - if modification fails, keeps the old SL-M active and continues monitoring.
  - target completion cancels the SL-M; SL-M completion cancels the target.
- Partial target/stoploss exits:
  - calculate `RemainingQty = PositionQty - FilledExitQty`.
  - use `partial_exit_response = AUTO_FLATTEN` or `MANUAL_REVIEW`.
  - default real-money behavior is `AUTO_FLATTEN` only after broker reconciliation confirms remaining quantity and order state is clear.
  - if order state is unknown, use `MANUAL_REVIEW`, activate kill switch, and block new entries.
  - log critical event/order history rows.
- Order status polling requirements:
  - entry orders: every 0.5 to 1 second until terminal state or timeout.
  - exit orders: every 0.5 to 1 second while a position is open.
  - general order reconciliation: every 3 to 5 seconds during a live position.
  - full broker reconciliation: every 30 to 60 seconds or on any error.
  - on polling failure, mark `UNKNOWN_BROKER_STATE`, block new entries, and attempt reconciliation.
- Use KiteTicker/WebSocket order updates where available as primary order-state input, polling as fallback, and full order-book plus positions reconciliation as final safety.
- Live feed callbacks stay lightweight and enqueue tick batches.
- UI/web rendering is throttled and should never render all raw ticks from a full trading day.
- Active Orders and Live Trade UI state is forced to clear/complete after completed target/stoploss/manual exits.
- Tick tables for NIFTY/CE/PE are scrollable and capped in the visible table.

## Safety And Recovery

Safety controls include:

- max trades
- max loss per trade
- max order value
- max daily turnover
- max daily premium bought
- max daily loss
- max daily profit
- max consecutive losses
- max allowed entry slippage
- max broker position age
- entry-to-protection timeout
- daily API error limit
- square-off time
- entry cutoff time
- force square-off time
- broker close buffer minutes
- manual square-off
- manual kill switch
- market-hours guard
- live broker connection requirement
- Zerodha margin check before live entry
- startup/recovery state review

Real-money start is blocked unless all checks passed within the last 2 minutes:

- Zerodha authenticated connection.
- profile check.
- margin check.
- order-book check.
- position check.
- recovery check.
- network health check.
- market-hours check.
- no kill switch.
- no pending local state.
- no unknown broker orders.
- settings hash locked for the session.
- explicit user confirmation for real-money start.

On app restart, recovery must reconcile broker state before trading can resume:

1. Load local recoverable state.
2. Fetch Zerodha order book.
3. Fetch Zerodha positions.
4. Fetch broker open orders.
5. Compare local position, pending entry, target/SL order IDs, completed orders, and unmatched broker orders.
6. Resume trading only when local state and broker state match exactly and no kill-switch or unknown state remains.
7. If any mismatch exists, block trading, show Recovery Required, and allow only manual square-off or explicit reconcile actions.

Emergency exit must always use broker-confirmed quantity:

1. Cancel pending entry orders.
2. Cancel open target/SL orders when safe.
3. Fetch current broker position quantity.
4. If broker quantity is positive, place MARKET SELL for actual broker quantity.
5. Poll until complete.
6. If rejected, try a marketable LIMIT SELL only when configured and safe.
7. If still unresolved, show CRITICAL MANUAL EXIT REQUIRED.
8. Block all new entries.

LIVE order intent and tagging are mandatory:

- Before every broker order, persist `ORDER_INTENT_CREATED` with `intent_id`, symbol, side, quantity, price, order type, trade ID, and candle ID.
- Use unique Zerodha order tags for every order:
  - `TB4_ENTRY_<session_short>_<trade_no>`
  - `TB4_TGT_<session_short>_<trade_no>`
  - `TB4_SL_<session_short>_<trade_no>`
  - `TB4_SQOFF_<session_short>_<trade_no>`
- Store tag, order ID, trade ID, session ID, strategy version, and settings hash.
- If placement times out, search broker orders by tag/time/symbol/quantity before retrying. Never blindly place a duplicate order.

Every order must use one central order-state classifier rather than scattered raw status checks. The classifier returns:

- `ENTRY_PENDING`
- `ENTRY_OPEN`
- `ENTRY_FILLED`
- `ENTRY_PARTIAL`
- `ENTRY_REJECTED`
- `ENTRY_CANCELLED_EMPTY`
- `ENTRY_CANCELLED_PARTIAL`
- `EXIT_PENDING`
- `EXIT_FILLED`
- `EXIT_PARTIAL`
- `EXIT_REJECTED`
- `UNKNOWN`

LIVE order placement validation must include:

- quantity is a valid lot multiple.
- quantity is below exchange freeze quantity or autoslice is explicitly supported.
- product is valid for NFO options and consistent between entry and exit.
- order variety is regular unless intentionally configured otherwise.
- trading symbol exists in the current instrument dump.
- instrument is active and not expired.
- NIFTY index token and CE/PE option tokens match current instruments.
- LTP exists.
- bid/ask spread is acceptable when available.
- Zerodha margin is fresh and sufficient.
- target price is above entry price, tick-size rounded, and valid.
- stoploss SL-M trigger is positive, below entry price for bought options, tick-size rounded, not too close to LTP, and inside valid broker range.
- market and SL-M orders use Zerodha market protection when supported and configured.

Risk escalation rules:

- If entry rejection occurs, log reason and do not retry unless the reason is explicitly classified as temporary and safe.
- If target or stoploss is rejected after entry fill, attempt emergency square-off and activate kill switch.
- If cancellation is rejected or unknown, reconcile before any new action.
- If entry fill slippage exceeds `MaxAllowedSlippage`, square off or mark risk violation according to the real-money setting; default real-money action is square-off for extreme slippage.
- If protective target/SL orders are not placed within `ProtectionPlacementTimeoutSeconds`, emergency square-off.
- If API errors reach the configured daily threshold, activate kill switch and block new trades.

Recovery state files and generated session artifacts are intentionally ignored by Git.

## Persistence

- Human-readable reports remain Excel-based.
- SQLite session databases store:
  - trades
  - orders
  - order history
  - order lifecycle transitions including `INTENT_CREATED`, `BROKER_ORDER_REQUEST_SENT`, `ORDER_ID_RECEIVED`, `ORDER_OPEN`, `ORDER_PARTIAL`, `ORDER_COMPLETE`, `ORDER_CANCEL_REQUESTED`, `ORDER_CANCELLED`, `ORDER_REJECTED`, `ORDER_UNKNOWN`, and `RECONCILED`
  - compact no-trade decision rows for every candle, including timestamp, NIFTY bias, selected option, CE/PE scores when available, skip reason, main fast trigger, rejection reason, risk block reason, and broker block reason
  - early-score entry fields, entry type, final decision, decision reason, and rejection reason
  - structured events
  - recoverable open-position/pending-entry/kill-switch state
  - settings profile metadata
- JSON files store local live/paper recovery state, ignored runtime account snapshots, and session audits.
- End-of-session audit must include open broker orders, broker positions, local positions, unmatched broker orders, unmatched local orders, kill switch status, API errors, feed disconnects, max latency, max backlog, settings hash, and strategy version.
- Every Excel report must include run metadata: report timestamp, app start timestamp, code version or git commit hash, settings hash, profile name, mode, data source, chart interval, broker connected yes/no, backtest fill mode, slippage model, final balance, trade count, win/loss, max drawdown, and sheet guide.
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

Strategy selection profile keys include `bullish_threshold`, `bearish_threshold`, `rsi_bull`, `rsi_bear`, `rsi_reversal_bullish`, `rsi_reversal_bearish`, `bullish_reversal_condition` defaulting to `-20`, and `bearish_reversal_condition` defaulting to `10`. These keys are shared by backtest, paper, and real-money modes.

Optional strategy routing mode:

- `OptionEntryMode = NIFTY_FILTERED | OPTION_FIRST_WITH_NIFTY_CONFIRMATION | BOTH_CE_PE_SCAN_WITH_NIFTY_BIAS`
- Real-money default is `NIFTY_FILTERED`.
- Research/backtest may use `BOTH_CE_PE_SCAN_WITH_NIFTY_BIAS`, scoring CE and PE, applying NIFTY bias as priority/penalty, choosing only one strongest symbol, and never entering both sides together.

Fast OHLCV option-entry profile keys include:

- `fast_ohlcv_entry_enabled`
- `buy_limit_score_low`
- `market_entry_score`
- `aggressive_entry_score`
- `trigger_upper_wick_max`
- `hard_rejection_upper_wick_max`
- `aggressive_upper_wick_max`
- `minimum_body_percent`
- `market_entry_minimum_body_percent`
- `aggressive_minimum_body_percent`
- `minimum_close_position`
- `market_entry_minimum_close_position`
- `aggressive_minimum_close_position`
- `volume_previous_multiplier`
- `avg_volume_minimum_multiplier`
- `volume_pickup_avg_multiplier`
- `large_candle_multiplier`
- `move_from_low_max_multiplier`
- `aggressive_move_from_low_max_multiplier`
- `gap_spike_multiplier`
- `buy_limit_offset_multiplier`
- `minimum_offset`
- `maximum_offset`
- `buy_limit_validity_seconds`
- `backtest_limit_fill_mode`
- `market_entry_backtest_mode`
- `slippage_points`
- `slippage_percent`
- `minimum_slippage_points`
- `limit_fill_requires_signal_still_valid`
- `enable_chop_filter`
- `chop_lookback_candles`
- `chop_overlap_count`
- `aggressive_live_entry_enabled`
- `aggressive_setup_score`
- `one_entry_attempt_per_candle`
- `missed_limit_cooldown_candles`
- `max_spread_points`
- `partial_exit_response`
- `max_loss_per_trade`
- `max_allowed_slippage`
- `protection_placement_timeout_seconds`
- `max_broker_position_age_seconds`
- `daily_api_error_limit`
- `max_daily_turnover`
- `max_daily_premium_bought`
- `max_order_value`
- `entry_cutoff_time`
- `force_square_off_time`
- `broker_close_buffer_minutes`
- `paper_first_guard_required`

The `real` profile must not store stale Zerodha margin/account values. Runtime account snapshots belong in ignored state such as `data/runtime/real_account_snapshot.json` or `results/runtime/real_account_snapshot.json`, with `fetched_at`, `available_margin`, `used_margin`, `broker_user_id`, `source = Zerodha`, and `valid_until`. Old stored margin must never be used for live order decisions.

Current profile rules:

- `Early Score` is the canonical fast OHLCV entry score name across strategy, reports, logs, tests, and UI-facing exports.
- Old legacy option-score settings/labels have been removed from active profile defaults and active exports.
- Fast OHLCV setting relationships are validated before save/run. Invalid combinations are rejected, including:
  - market-entry score at or below buy-limit score.
  - hard rejection wick at or below trigger wick.
  - market/aggressive body or close-position thresholds below the base thresholds.
  - maximum offset below minimum offset.
  - chop overlap count above chop lookback candles.
  - unsupported backtest limit-fill mode.
- Paper simulated balance is preserved unless the user manually changes Paper Risk Settings balance.
- Applying backtest settings to live profiles preserves Paper balance and Paper/Real chart intervals, but excludes runtime account/margin fields from settings profiles.
- Backtest Live NIFTY optimizer results are report-only and are not copied into Paper or Real Money settings.
- Settings hash is frozen at live session start. During an active live session, active strategy settings cannot change; edits apply only after restart or a new session.
- If real profile settings hash changes, real-money trading is blocked until a paper session completes with the same settings hash or the user gives the explicit untested-settings override required by policy.
- A shared `settings_service.py` must own defaults, validation, profile load/save, hash generation, safe apply-backtest-to-live, runtime/account field exclusion, UI labels, and blank-value fallback for both web and desktop paths.

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
- fast OHLCV entry decisions
- fast OHLCV settings relationship validation
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
- Backtest Live NIFTY optimizer export and interval propagation
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

This TradeBotV5 snapshot is intended to be published as a new private GitHub project:

- Owner: `sham6991`
- Repository: `tradebotV5`
- Visibility: private
- Initial branch: current local branch unless renamed before publish.
- Previous remote before V5 publish: legacy TradeBot repository.

## Commercial Implementation Backlog

Before real capital trading, implement or verify these as blocking requirements:

1. `ActualEntryPrice` always comes from Zerodha `average_price`.
2. Target/SL uses actual fill price, not signal close.
3. Entry `order_id` never opens a position by itself.
4. Cancelled orders are checked for partial fills.
5. Target and SL are monitored as an OCO pair.
6. Opposite exit order is cancelled after one exit completes.
7. Unknown broker state blocks new trades.
8. Restart recovery compares local state with Zerodha orders and positions.
9. Settings hash is locked during live session.
10. Fresh margin/order/position checks are required before live start.
11. Paper-first guard applies after settings changes.
12. Emergency square-off uses broker-confirmed quantity.
13. One global trade lock covers CE and PE.
14. Order tags are used for every order.
15. No silent fallback to old option buy-score logic.
16. Runtime account/margin data stays out of tracked settings profiles.
17. WebSocket/order update support is used when available, with polling fallback.
18. API error limits trigger kill switch.
19. Entry-to-protection timeout triggers emergency square-off.
20. Full audit logging exists for every order transition.

## Recommended Next Work

Planned dated jobs are tracked in `PLANNER.md`. Keep this blueprint as the base for project structure and current architecture.

Completed from this section on 2026-05-21:

- Backtest Excel exports now include a `Run Metadata` sheet with timestamps, code/settings identity, mode/source, fill/slippage model, balance/trade summary, drawdown, and sheet guide.
- Real Zerodha account/margin snapshots are excluded from `data/settings_profiles.json`; live margin refresh writes ignored runtime state under `data/runtime/real_account_snapshot.json`.
- Shared settings/profile ownership now lives in `settings_service.py`, with web and desktop paths using it for defaults, labels, normalization, runtime parsing, profile persistence, apply-backtest-to-live, and runtime account snapshot storage.
- Backtest-vs-live parity replay now lives in `parity_replay.py`, comparing a session SQLite order-history DB against supplied NIFTY/CE/PE candle frames replayed through the backtest decision path.

1. Completed 2026-05-21: Add a backtest workbook `Run Metadata` or `Summary` sheet with:
   - report/export timestamp
   - exporter/code version
   - settings hash/version
   - app process start time or code-loaded timestamp
   - final balance, trade count, win/loss summary, and workbook sheet guide.
   This should make stale-process/stale-export issues obvious from inside Excel.
2. Completed 2026-05-21: Move account-sensitive runtime values, especially fetched real margin and live connection state, out of tracked settings profiles.
   - Keep default strategy/risk settings in `data/settings_profiles.json`.
   - Store runtime account/margin/session state in ignored runtime files under `results/` or an ignored `data/runtime/` folder.
3. Completed 2026-05-21: Create one shared settings/profile module used by both web and desktop UI.
   - Move defaults, labels, blank-value fallback, profile load/save, and apply-backtest-to-live logic out of duplicated UI/web code.
   - Prevent future drift between `ui_shared.py` and `web_app.py`.
4. Completed 2026-05-21: Add backtest-vs-live parity replay.
   - Run saved paper/live candles through the backtest decision path.
   - Compare signal time, CE/PE selection, Early Score, entry type, entry price, target, stoploss, and skip reason.
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
9. Continue splitting `live_session.py` into smaller ownership modules before adding complex real-money order handling.
   - Initial `execution_v2.py` split completed on 2026-05-22: `execution_v2.py` is now a compatibility facade, with `LivePaperSession` in `live_session.py` and `Executor` in `feed_runtime.py`.
   - Suggested next modules: candle runtime, strategy runtime, order lifecycle, recovery runtime, and session persistence.
10. Mark or move legacy paths clearly.
    - `execution.py`, Tkinter desktop paths, and older V3-named text should be documented as legacy/comparison paths or moved under a future `legacy/` folder.
11. Add performance benchmark thresholds.
    - Turn candle-builder, tick-storm, process-flow, and scoring benchmarks into pass/fail checks with reasonable thresholds.
    - Use them before optimizing or changing live decision-path code.
12. Rename remaining user-facing legacy TradeBot strings in auth/keyring text if a clean V5 identity is required.
13. Run a real paper/live trial latency review before further decision-path optimization.
14. Run the full test suite after the web/backtest baseline is committed:
    ```powershell
    python -m unittest discover -s tests
    python -m compileall -q .
    ```
