# TradeBotV3

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

