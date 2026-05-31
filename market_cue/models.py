from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


YFINANCE_SYMBOLS = {
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


@dataclass
class CueValue:
    name: str
    source: str
    symbol: str = ""
    value: float | None = None
    previous_close: float | None = None
    percent_change: float | None = None
    timestamp: str = ""
    status: str = "UNAVAILABLE"
    warning: str = ""
    stale: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "symbol": self.symbol,
            "value": self.value,
            "previous_close": self.previous_close,
            "percent_change": self.percent_change,
            "timestamp": self.timestamp,
            "status": self.status,
            "warning": self.warning,
            "stale": self.stale,
            "raw": self.raw,
        }


def empty_fii_dii(fetch_mode: str = "auto_download", status: str = "FAILED") -> dict[str, Any]:
    return {
        "source": "NSE FII/DII CSV",
        "fetch_mode": fetch_mode,
        "segment": "Capital Market",
        "scope": None,
        "data_date": None,
        "fii_net": None,
        "dii_net": None,
        "fii_buy": None,
        "fii_sell": None,
        "dii_buy": None,
        "dii_sell": None,
        "units": "INR crores",
        "status": status,
        "warnings": [],
        "source_file_name": "",
    }
