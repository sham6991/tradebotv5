# TradeBotV5 Main App Plan

## 2026-06-17 Main App Production Refactor

Scope:

- Main App Paper, Live, Live Monitor, and Backtest are the primary index-options workflow.
- Intraday remains available and shares the Zerodha websocket owner lock.
- The removed legacy options module has no runtime routes, UI entry point, owner alias, tests, or imports.

Implemented architecture:

- `main_app/underlyings.py`: NIFTY and SENSEX specs with spot quote keys, derivative aliases, strike steps, exchange candidates, and lot defaults.
- `main_app/instrument_resolver.py`: daily instrument-master based resolver for spot, current futures, ATM CE, ATM PE, lot size, tick size, expiry, and clear blockers.
- `main_app/market_phase_engine.py`: Indian market phase, opening range, and gap classification formulas.
- `main_app/direction_engine.py`: spot price-action scores plus current-month futures volume/VWAP confirmation and first/second deviation zones.
- `main_app/strategy_plugins.py`: plugin registry with `FAST_OHLCV`, returning `StrategySignal` only.
- `main_app/decision_kernel.py`: shared Paper/Live/Monitor/Backtest decision kernel that produces LIMIT-only trade plans.
- `main_app/tick_engine.py`: latest-wins tick cache, bounded queue, dropped tick counters, and decision latency throttle metrics.
- `main_app/execution/`: broker adapters and lifecycle engine enforcing BUY LIMIT, SELL SL-LIMIT, SELL LIMIT, NRML, exact user lots, and SL verification before target placement.

Safety rules:

- MARKET and SL-M are forbidden at the broker/order manager layer.
- Product normalizes to `NRML`.
- Strategy plugins cannot place broker orders.
- Websocket owner supports only `NONE`, `MAIN_APP`, and `INTRADAY`.
- Market depth and news shock logic are not part of Main App decision approval.

Acceptance tests live under `tests/test_main_app_*.py` plus owner/static/order policy tests.
