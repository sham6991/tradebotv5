# Zerodha Kite Connect Contract Notes

Review date: 2026-06-16

## Docs Reviewed

- Kite Connect v3 Introduction: overview, response shape, HTTP error model.
- Kite Connect v3 User: login flow, token exchange, auth header, token expiry, funds/margins.
- Kite Connect v3 Orders: placement fields, order book behavior, order status fields.
- Kite Connect v3 Margin calculation: order margin request and response contract.
- Kite Connect v3 Market quotes and instruments: instrument master, `/quote`, `/quote/ohlc`, `/quote/ltp`, limits, missing-key behavior.
- Kite Connect v3 WebSocket streaming: connection limits, subscription limits, modes, heartbeat, quote/index/full packet fields, market depth, text order updates.
- Kite Connect v3 Historical candle data: candle fields, intervals, `oi`, continuous futures behavior.
- Kite Connect v3 Postbacks / WebHooks: order-update payload and checksum behavior; no public postback is being added in this task.
- Official `pykiteconnect` v4 docs/source behavior: `KiteTicker` modes, reconnect defaults, `is_connected`, subscribe/unsubscribe/set_mode, order update callback.

## Contract Findings

- Kite APIs are REST-like HTTP APIs; most inputs are form encoded and responses are JSON, except instrument CSV and other documented exceptions. Standard HTTP status codes indicate success/error with JSON data where applicable.
- Login starts at `https://kite.zerodha.com/connect/login?v=3&api_key=...`. The redirect returns a short-lived `request_token`; `/session/token` requires `api_key`, `request_token`, and checksum `sha256(api_key + request_token + api_secret)`.
- Requests after login require `Authorization: token api_key:access_token`. Access tokens expire at 6 AM the next day or when invalidated.
- WebSocket endpoint is `wss://ws.kite.trade?api_key=...&access_token=...`.
- One API key can have up to 3 websocket connections.
- One websocket can subscribe to up to 3000 instrument tokens.
- Websocket request actions are `subscribe`, `unsubscribe`, and `mode`.
- Websocket modes are `ltp`, `quote`, and `full`. `ltp` has last price only; `quote` has quote fields without market depth; `full` includes market depth.
- Websocket market data is binary. Non-market messages, including order updates and errors, are text/JSON.
- Websocket heartbeats are one-byte messages sent every couple seconds when no data is available. Heartbeat does not mean usable market data.
- Full-mode quote packets include instrument token, last traded price, quantity, average price, volume, buy/sell quantity, OHLC, last traded timestamp, open interest, OI day high/low, exchange timestamp, and depth.
- Index websocket packets differ from tradable instruments and do not carry the same F&O fields.
- Market depth has five bid and five offer levels; each level has quantity, price, and orders.
- The instrument master is a daily gzipped CSV. Fields include `instrument_token`, `exchange_token`, `tradingsymbol`, `name`, `last_price`, `expiry`, `strike`, `tick_size`, `lot_size`, `instrument_type`, `segment`, and `exchange`.
- Numeric instrument tokens may be reused after derivative expiry. Stable storage and quote identity should prefer `exchange:tradingsymbol`.
- `/quote` is a snapshot API, not a high-frequency replacement for websocket streaming. It supports up to 500 instruments per request.
- `/quote/ohlc` and `/quote/ltp` support up to 1000 instruments per request.
- Quote APIs identify instruments with repeated `i=exchange:tradingsymbol` query parameters.
- If data is unavailable, invalid, or expired for a requested quote key, the key is absent from the response. Code must check existence before access.
- Full quote response fields include `instrument_token`, `timestamp`, `last_trade_time`, `last_price`, `volume`, `average_price`, `buy_quantity`, `sell_quantity`, `open_interest`, `last_quantity`, `ohlc`, `net_change`, `lower_circuit_limit`, `upper_circuit_limit`, `oi`, `oi_day_high`, `oi_day_low`, and `depth.buy[]` / `depth.sell[]`.
- Historical candle rows are `[timestamp, open, high, low, close, volume]`; with `oi=1`, OI is included. Intervals include `minute`, `3minute`, `5minute`, `10minute`, `15minute`, `30minute`, `60minute`, and `day`.
- Order placement requires exchange, tradingsymbol, transaction type, quantity, product, order type, validity, and variety-specific fields. Order book is transient for the day and returns open, pending, executed, rejected, and cancelled orders.
- Margins endpoints are JSON POST and require order fields such as exchange, tradingsymbol, transaction type, variety, product, order type, quantity, price, and trigger price.
- Public postbacks are not required here. For individual developers, websocket order updates are the preferred order-update path.
- `pykiteconnect` `KiteTicker` supports `MODE_FULL`, `MODE_QUOTE`, and `MODE_LTP`; auto reconnect is enabled by default with exponential backoff. Calling `stop` can terminate the event loop and prevent reconnect.

## App Mapping Required

- Quote identity: use `exchange:tradingsymbol` for locked CE/PE contracts whenever available; token lookup is fallback only.
- Websocket latest quotes and snapshot quotes must normalize to one schema with: `quote_key`, `exchange`, `tradingsymbol`/`symbol`, `instrument_token`, `ltp`, `last_price`, `bid`, `ask`, `bid_qty`, `ask_qty`, `depth`, `volume`, `oi`, `open_interest`, `timestamp`, `exchange_timestamp`, `last_trade_time`, `received_epoch`, `age_seconds`, `quote_source`, and `data_mode`.
- Map Zerodha `open_interest` and `oi` into both normalized `oi` and `open_interest`.
- Extract top bid/ask and quantities from `depth.buy[0]` and `depth.sell[0]` when present. Do not fabricate depth when it is missing.
- Treat `timestamp`, `exchange_timestamp`, and local `received_epoch` separately. Missing exchange timestamps must not be treated as fresh real-time data for final real-order validation.
- `entry_data_ready` must require fresh INDEX, CE, PE, candles, valid contract lock, broker availability, no active feed conflict, and no safety block. A connected websocket or heartbeat alone is not ready.
- Snapshot fallback must be batched, throttled, observable, and back off on failures/rate-limit style exceptions. It must not run every scan cycle.
- Snapshot fallback should request CE and PE together and must report exact omitted keys.
- Real final order validation must use strict freshness and must never reuse paper scanner thresholds or stale cached snapshots.

## Current Code Paths Affected

- `zerodha_client.py`: ticker lifecycle, named tickers, websocket budget diagnostics, quote snapshot calls.
- `feed_runtime.py`: Main App live feed ownership and active ticker status.
- `intraday/session_manager.py` and `intraday/stock_live_feed.py`: Intraday live feed ownership/reference only.
- `options_auto/terminal_service.py`: live scan start, websocket start, locked contract resolution, snapshot fallback, readiness, decision handoff, UI summary.
- `options_auto/data/options_live_feed.py`: websocket ticks, role health, quote freshness.
- `options_auto/data/options_feed_health.py` and `options_auto/data/feed_role_health.py`: role freshness and blocker details.
- `options_auto/data/live_quote_provider.py`, `options_auto/data/options_quote_provider.py`, `options_auto/data/quote_health_recovery.py`, and `options_auto/data/snapshot_quote_fallback.py`: quote normalization and fallback.
- `options_auto/execution/quote_freshness.py` and `options_auto/execution/execution_safety.py`: scanner versus final-order freshness and data-quality blockers.
- `options_auto/intelligence/strike_selector.py` and `options_auto/intelligence/decision_pipeline.py`: normalized quote usage and clear blocker propagation.
- `options_auto/web_routes.py`, `web_static/options_auto.html`, `web_static/options_auto.js`, and `web_static/options_auto.css`: diagnostics and operator status.

## Risks Found Before Edits

- Preserved WIP had a snapshot fallback helper that called `get_quotes` twice per request path through `DataRecoveryHelper`, which can violate throttling intent.
- Preserved WIP fallback accepted token values in a parameter named `exchange_tokens`; Kite `/quote` expects `exchange:tradingsymbol` keys.
- Preserved WIP tests were mostly unit placeholders and did not prove active feed conflict, omitted quote-key handling, idempotent start, or real final validation separation.
- Websocket budget exists in WIP but must be tied to start behavior and UI/status diagnostics.
- Existing code may still surface generic data/governor blockers unless DATA-stage blockers carry category, role, symbol/key, age, source attempted, and next action.

## Tests To Prove Compliance

- Websocket budget with zero, default, named, and limit-reached tickers.
- Options Auto blocks start when Main App feed is active and when Intraday feed is active.
- Same Options Auto owner reconnect/start is idempotent and does not duplicate ticker/scanner threads.
- Connected websocket with no INDEX/CE/PE ticks is not data ready.
- Websocket full-depth ticks normalize bid/ask/depth/OI.
- Snapshot quote rows normalize to the same schema and map `open_interest` to `oi`.
- Missing snapshot key reports the exact requested `exchange:tradingsymbol` without `KeyError`.
- Websocket stale plus fresh snapshot uses `zerodha_snapshot_quote`.
- Quote API exception/rate-limit starts backoff and avoids repeated calls each scan.
- Locked CE/PE resolution works by `exchange:tradingsymbol` and token fallback.
- Paper scanner tolerates configured scanner freshness, while real final validation blocks stale/unknown/cached-old quotes.
- UI summary exposes recovery state, connection budget, data blocker, fallback status, and does not crash with missing fields.

## 2026-06-16 Decision/Depth/Owner Alignment Addendum

Docs re-reviewed for the decision/depth/owner patch:

- Kite Connect v3 Introduction: REST-like APIs, JSON response shape, standard HTTP status/error behavior.
- Kite Connect v3 Market quotes and instruments: `/quote` snapshot contract, 500-instrument limit, missing-key behavior, full quote fields, instrument CSV fields, and the instrument-token reuse warning.
- Kite Connect v3 WebSocket streaming: websocket endpoint requires `api_key` and `access_token`; max 3 websocket connections per API key; max 3000 instrument subscriptions per connection; modes are `ltp`, `quote`, and `full`; heartbeats are not market data; full quote packets include OI, exchange timestamp, and five bid/five offer depth levels.
- Kite Connect v3 Historical candles: intervals and optional OI field.
- Kite Connect v3 Orders: order placement and order-book behavior were reviewed only to preserve real-money safety; this patch must not bypass preflight, final validation, kill switch, or idempotency.
- Official `pykiteconnect` v4 docs/source: `KiteTicker` builds the websocket URL from `api_key` plus `access_token`, exposes subscribe/set_mode callbacks, and `close()` stops retry before closing.

Implementation contract for this patch:

- A preferred websocket owner may be selected before login, but a real KiteTicker must not start until a Zerodha access token/client exists.
- Only one app module may own websocket live market data at a time: `MAIN_APP`, `OPTIONS_AUTO`, or `INTRADAY`; same-owner reconnect/resubscribe is allowed.
- Owner-blocked modules must report owner blockers instead of vague stale/depth/governor blockers.
- Option CE/PE websocket subscriptions must request full mode so five-level depth can arrive; if depth is absent, classify top-of-book/no-depth/degraded explicitly instead of fabricating depth.
- `exchange:tradingsymbol` remains the preferred quote identity; numeric token and bare tradingsymbol are fallback aliases only.
- Snapshot fallback remains `/quote` based, batched, throttled, and must treat omitted keys as explicit missing-key blockers.
- Unknown snapshot timestamps must be marked `age_known=false`; they cannot pass real final order validation.

Files affected by this contract:

- `websocket_owner_controller.py`: shared owner preference/active lock state.
- `web_app.py`: Main App owner endpoints, main feed start/stop enforcement, status exposure.
- `options_auto/web_routes.py` and `options_auto/terminal_service.py`: owner-state exposure, owner-blocked start/readiness behavior, paper state preservation.
- `intraday/web_routes.py` and `intraday/session_manager.py`: owner lock before intraday websocket start and safe release on stop.
- `options_auto/data/live_quote_provider.py`: central normalized quote schema and depth health attachment.
- `options_auto/intelligence/strike_selector.py`: use normalized quote depth health and preserve OI/depth identity.
- `web_static/options_auto.js`: render owner/depth/decision diagnostics without crashing on missing fields.

Tests required for this addendum:

- Owner preference before login, activation requiring login, owner acquisition for each module, cross-owner blocking, same-owner reconnect, wrong-owner release refusal, stale startup clearing, owner status in Main/Options Auto summaries, Options Auto owner-blocked paper preservation.
- Depth health for full depth, top-of-book, no-depth, stale, invalid spread, and real-final no-depth blocking.
- Quote normalization for websocket/snapshot depth, OI mapping, alias identity, timestamp source, and unknown snapshot age.
