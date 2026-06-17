# Zerodha Kite Contract Notes

Review date: 2026-06-17

Official Kite Connect v3 sections reviewed for the Main App refactor:

- Introduction, response structure, and errors.
- Authentication/login.
- Orders.
- Margins.
- Instruments.
- Market quotes.
- WebSocket streaming.
- Historical candles.
- Postbacks/order updates, for order-update behavior only.
- Python SDK behavior where this app uses `kiteconnect`.

API limits confirmed:

- WebSocket: one API key supports up to 3 websocket connections; one connection supports up to 3000 instrument subscriptions; supported modes are `ltp`, `quote`, and `full`.
- Quote APIs: `/quote` supports up to 500 instruments; `/quote/ohlc` and `/quote/ltp` support up to 1000 instruments.
- Instrument master: generated once daily; cache once per trading day. Persist `exchange:tradingsymbol` identity, not `instrument_token`, because derivative tokens can be reused after expiry.
- Missing quote behavior: requested quote keys can be absent when data is unavailable or expired. Code must check key existence before reading fields.
- Error behavior: `403` means token/session invalid or expired and requires login recovery; `429` means rate limiting and must block/cool down automated retries.

Required request keys:

- Quote identity uses repeated `i=exchange:tradingsymbol` keys.
- Main App live orders use `exchange`, `tradingsymbol`, `transaction_type`, `quantity`, `product=NRML`, `order_type`, `price`, and for SL-LIMIT `trigger_price`.
- Main App margin checks are advisory/preflight and do not replace order-book reconciliation.

Required response fields mapped by this app:

- Instrument master: `instrument_token`, `exchange_token`, `tradingsymbol`, `name`, `expiry`, `strike`, `tick_size`, `lot_size`, `instrument_type`, `segment`, `exchange`.
- Quote/tick: `last_price`, `timestamp`, `exchange_timestamp`, `last_trade_time`, `ohlc.open`, `ohlc.high`, `ohlc.low`, `ohlc.close`, `volume`, `average_price`, `oi`/`open_interest`.
- Order book/order updates: `order_id`, `exchange_order_id`, `tradingsymbol`, `exchange`, `transaction_type`, `product`, `order_type`, `quantity`, `price`, `trigger_price`, `status`, `filled_quantity`, `pending_quantity`, `average_price`, and timestamps/status messages.

Websocket lifecycle behavior:

- Websocket connected does not mean market data is ready.
- Heartbeats are not valid market data.
- Freshness must use tick/quote timestamps and latest tick age.
- Main App and Intraday must not own Zerodha websocket simultaneously.
- Websocket owner activation only reserves the Zerodha feed slot. It must not auto-start ticks because the selected index and option tokens still have to be loaded and subscribed through Start Feed/Start Paper/Start Live.

Main App implementation contract:

- Supported underlyings: NIFTY and SENSEX.
- Selected underlying must drive index token fetch, option lookup, backtest labels, live desk labels, and optimizer labels. SENSEX must never silently fall back to NIFTY instruments.
- Direction uses spot/index price action, current-month futures volume/VWAP confirmation, and selected option OHLCV confirmation.
- Index volume, market depth, bid/ask depth scoring, news shock scanners, Shadow Mode, market orders, and SL-M orders are not used by Main App.
- Entry must be `BUY LIMIT`.
- Stoploss must be `SELL SL-LIMIT` and verified active before target placement.
- Target must be `SELL LIMIT`.
- Product must be `NRML`.
- User-defined lots are used exactly; risk mode must not auto-change quantity.
- Strategy plugins only emit signals. They do not call Zerodha or mutate broker/order state.
- Paper, Live, Live Monitor, and Backtest share the same decision kernel and lifecycle policy.
- User-facing Main App settings expose only essentials first: mode/context, NIFTY/SENSEX underlying, risk mode, fixed `FAST_OHLCV` entry logic, lots, daily risk, square-off, bias controls, and explicit price-only futures fallback. Advanced thresholds are grouped under Advanced Expert Settings. Market-order, SL-M, order-product override, and market-entry conversion controls are hidden from the operator UI.
- Manual 30-minute bias is an operator override for the current decision window. Saving Paper/Live settings during an active session updates only decision-context settings such as bias and underlying; it does not mutate broker/order lifecycle state.

Code paths affected:

- `main_app/underlyings.py`
- `main_app/instrument_resolver.py`
- `main_app/market_phase_engine.py`
- `main_app/direction_engine.py`
- `main_app/strategy_plugins.py`
- `main_app/decision_kernel.py`
- `main_app/tick_engine.py`
- `main_app/execution/brokers.py`
- `main_app/execution/lifecycle.py`
- `order_manager.py`
- `live_session.py`
- `settings_service.py`
- `websocket_owner_controller.py`
- `web_app.py`
- `web_static/index.html`
- `web_static/app.js`

Tests that prove compliance:

- Main App instrument resolver tests for NIFTY/SENSEX current futures/options, stable identity, missing futures blockers, and no persisted-token dependency.
- Main App direction/kernel tests for market phase, gap/opening range, CE/PE side restriction, futures VWAP confirmation, plugin confidence, and shared trade-plan generation.
- Main App lifecycle tests for LIMIT-only order policy, NRML-only product, exact lots, SL-LIMIT before target, protection failure handling, target/SL reconciliation, paper ledger, and manual reconciliation on impossible double-fill.
- Tick cache tests for latest-wins behavior, bounded queue, dropped tick metrics, and decision throttle mode.
- Removal/owner/UI tests proving only Main App and Intraday remain as websocket owners, no removed route/page is registered, the websocket owner card shows operational fields, and the simplified Main App settings surface keeps forbidden controls out of the operator workflow.
