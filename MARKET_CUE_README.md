# Indian Market Cue Analyzer

Decision-support module for NIFTY 50 and BANK NIFTY opening cues.

## Setup

Install dependencies:

```powershell
pip install -r requirements.txt
```

The existing TradeBot web app runs with:

```powershell
python web_app.py
```

The analyzer is integrated into the existing custom web app at the `Market Cue` view and under `/api/market-cue/*`.

## Integration Notes

- The analyzer reuses the existing Virtual/Paper Zerodha client.
- It does not create a separate Kite login flow.
- It does not expose Kite keys or access tokens to the frontend.
- It does not place, modify, or cancel orders.
- The former Backtest Live Data usage is routed to the same Virtual/Paper Zerodha connection.
- `/api/status` exposes only `PAPER` and `LIVE`; old `BACKTEST` requests remain accepted as a `PAPER` alias.

## Sources

- Zerodha Kite Connect for NIFTY 50, BANK NIFTY, India VIX, and previous close context.
- `yfinance` for global, currency, commodity, and bond cues.
- NSE FII/DII CSV from `https://www.nseindia.com/reports/fii-dii`.
- Manual CSV upload and manual FII/DII entry as fallbacks.

The NSE FII/DII page provides two CSV downloads. Prefer
`FII/FPI & DII trading activity on NSE, BSE and MSEI in Capital Market Segment`
because it is the broader combined exchange file. Use
`FII/FPI & DII trading activity on NSE in Capital Market Segment` only as the
NSE-only fallback. NSE may block automated server requests with browser/session
protection, so the dashboard keeps manual CSV upload available by design.

Reliability behavior:

- `get_market_cue_bias()` returns the latest generated/saved report only. It does
  not trigger surprise network fetches when no report exists.
- `yfinance` first uses `fast_info`; if that is incomplete, it falls back to
  daily history and marks the row as partial.
- If live Kite quotes fail and daily historical data is used, Indian index
  scoring and confidence are reduced and the report labels the fallback clearly.
- Stale cached global values expire instead of being reused indefinitely.

## yfinance Smoke Test

```python
import yfinance as yf

symbols = {
    "Dow Jones": "^DJI",
    "Nasdaq": "^IXIC",
    "S&P 500": "^GSPC",
    "Nasdaq Futures": "NQ=F",
    "S&P Futures": "ES=F",
    "Nikkei 225": "^N225",
    "Hang Seng": "^HSI",
    "Shanghai": "000001.SS",
    "FTSE 100": "^FTSE",
    "DAX": "^GDAXI",
    "CAC 40": "^FCHI",
    "WTI Crude": "CL=F",
    "Gold": "GC=F",
    "Silver": "SI=F",
    "USD/INR": "INR=X",
    "DXY": "DX-Y.NYB",
    "US 10Y Yield": "^TNX",
}

for name, symbol in symbols.items():
    ticker = yf.Ticker(symbol)
    info = ticker.fast_info
    last_price = info.get("last_price")
    previous_close = info.get("previous_close")
    change_pct = ((last_price - previous_close) / previous_close) * 100 if last_price and previous_close else None
    print(name, symbol, last_price, previous_close, change_pct)
```

## Sample JSON Response

```json
{
  "bias": "Mild Bearish",
  "score": -4.2,
  "confidence": 61,
  "risk_level": "Medium",
  "data_reliability": "Good",
  "nifty_plan": {},
  "banknifty_plan": {},
  "institutional_flow": {
    "fii_net": -2407.87,
    "dii_net": 1361.43,
    "data_date": "2026-05-26",
    "source": "NSE FII/DII CSV",
    "fetch_mode": "auto_download",
    "scope": "NSE+BSE+MSEI",
    "units": "INR crores"
  },
  "source_status": {}
}
```

## Sample Report

Overall bias is Mild Bearish with 61% confidence. Institutional flow is negative while domestic support, crude, currency, and global cues may provide partial offsets. CE/PE output is conditional only and never an instruction to buy immediately.
