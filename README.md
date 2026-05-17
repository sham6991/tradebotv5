# TradeBotV4

Private trading research and execution workspace for:

- Backtesting
- Paper/live trading
- Candle building
- Order lifecycle logging
- Risk controls
- Session replay and audit review

## Safety Notes

This repository should remain private. Do not commit broker credentials, access tokens, generated reports, SQLite databases, or live session state files.

The `.gitignore` excludes local outputs such as `results/`, `*.db`, `*.xlsx`, token files, caches, and live state JSON files.

## Web App

Easiest start: double-click:

```text
Start TradeBot Web.bat
```

This starts the local TradeBot web app on port 8000.

Run the browser control center:

```powershell
python main.py --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Zerodha app settings for the default local web app:

```text
Redirect URL: http://127.0.0.1:8000/zerodha/callback
```

Postback is disabled. Live order status, average price, fill quantity, pending
quantity, cancellations, and rejections continue to be refreshed through the
existing polling and reconciliation flow.

The previous desktop UI is still available for comparison:

```powershell
python desktop_app.py
```

## Verification

Run the test suite:

```powershell
python -m unittest discover -s tests
```

The large candle-builder stress test is intentionally skipped unless enabled:

```powershell
$env:RUN_CANDLE_BUILDER_STRESS = "1"
python -m unittest tests.test_candle_builder_stress
```

## Session Replay

Replay a previous SQLite session:

```powershell
python event_replay.py results\your_session.db
```

Export JSON:

```powershell
python event_replay.py results\your_session.db --format json --output results\session_replay.json
```
