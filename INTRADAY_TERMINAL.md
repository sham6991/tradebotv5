# Intraday Stocks Terminal

The Intraday Stocks Terminal is intentionally implemented as a separate module
under `intraday/` with separate web assets under `web_static/intraday.*`.

Open it from the V5 web server:

```text
http://127.0.0.1:8007/intraday
```

The current implementation provides the modular foundation:

- locked five-stock session setup for PAPER and REAL modes
- safe default LIMIT entries and SL-LIMIT order request builders
- Zerodha Kite parameter formatting behind `intraday/order_request.py`
- paper broker abstraction and Zerodha broker adapter
- EMA, RSI, VWAP, volume profile, liquidity, trap, news, options-bias, scoring, and risk modules
- session manager with settings lock, approval flow, kill switch, SQLite persistence, and Excel export
- Bloomberg-style terminal UI on `/intraday`

Real trading remains guarded:

- REAL mode requires explicit confirmation before session start
- auto real orders require a separate confirmation flag
- order placement is isolated behind broker adapters
- protective orders are not assumed until entry fill confirmation is available
- market orders are blocked unless the locked session explicitly allows them

