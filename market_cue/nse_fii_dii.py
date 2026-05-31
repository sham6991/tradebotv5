from __future__ import annotations

import os
import re
from io import StringIO
from typing import Any
from urllib.parse import urljoin

import pandas as pd

from .models import empty_fii_dii
from .utils import fresh_date_string, safe_float

NSE_HOME = "https://www.nseindia.com"
FII_DII_PAGE = "https://www.nseindia.com/reports/fii-dii"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_HOME,
    "Connection": "keep-alive",
}


def fetch_nse_fii_dii_auto() -> dict[str, Any]:
    result = empty_fii_dii("auto_download", "FAILED")
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as exc:
        result["warnings"].append(f"NSE auto-download dependency missing: {exc}")
        return result

    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        session.get(NSE_HOME, timeout=10).raise_for_status()
        response = session.get(FII_DII_PAGE, timeout=10)
        response.raise_for_status()
        links = discover_csv_links(response.text, BeautifulSoup)
        if not links:
            result["warnings"].append("NSE FII/DII CSV link was not discovered.")
            return result
        chosen = choose_preferred_link(links)
        csv_url = urljoin(NSE_HOME, chosen["href"])
        csv_response = session.get(csv_url, timeout=10)
        csv_response.raise_for_status()
        parsed = parse_nse_fii_dii_csv(csv_response.text, source_name="NSE FII/DII CSV")
        parsed["fetch_mode"] = "auto_download"
        parsed["source_file_name"] = os.path.basename(chosen["href"])
        if not parsed.get("scope"):
            parsed["scope"] = "NSE+BSE+MSEI" if "bse" in (chosen.get("text", "") + chosen.get("href", "")).lower() else "NSE only"
        return parsed
    except Exception as exc:
        result["warnings"].append(f"NSE FII/DII auto-download unavailable: {exc}")
        return result


def discover_csv_links(html: str, soup_cls=None) -> list[dict[str, str]]:
    if soup_cls is None:
        from bs4 import BeautifulSoup as soup_cls
    soup = soup_cls(html, "html.parser")
    links: list[dict[str, str]] = []
    for anchor in soup.find_all("a"):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        href = anchor.get("href") or ""
        lowered = f"{text} {href}".lower()
        if href and ("csv" in lowered or "fii" in lowered or "dii" in lowered):
            links.append({"text": text, "href": href})
    return links


def choose_preferred_link(links: list[dict[str, str]]) -> dict[str, str]:
    broader = [item for item in links if "bse" in f"{item.get('text', '')} {item.get('href', '')}".lower()]
    return (broader or links)[0]


def parse_nse_fii_dii_csv(csv_text: str, source_name: str = "NSE FII/DII CSV", fetch_mode: str = "manual_upload", file_name: str = "") -> dict[str, Any]:
    result = empty_fii_dii(fetch_mode, "FAILED")
    result["source"] = source_name
    result["source_file_name"] = file_name
    try:
        df = _read_csv_flexibly(csv_text)
    except Exception as exc:
        result["warnings"].append(f"CSV parse failed: {exc}")
        return result
    if df.empty:
        result["warnings"].append("CSV has no rows.")
        return result

    normalized = [_normalize_column(col) for col in df.columns]
    df.columns = normalized
    category_col = _first_col(df, ["category", "clienttype", "investortype", "particulars", "name"])
    date_col = _first_col(df, ["date", "tradedate", "reportdate"])
    buy_col = _first_col(df, ["buy", "purchase", "grosspurchase", "buyvalue"])
    sell_col = _first_col(df, ["sell", "sale", "grosssale", "sellvalue"])
    net_col = _first_col(df, ["net", "netvalue", "netinvestment", "netamount"])

    if not category_col:
        category_col = _detect_category_col(df)
    if not net_col and buy_col and sell_col:
        df["netcomputed"] = [
            (safe_float(row.get(buy_col)) or 0) - (safe_float(row.get(sell_col)) or 0)
            for _, row in df.iterrows()
        ]
        net_col = "netcomputed"

    if date_col:
        for value in df[date_col].dropna():
            date_value = fresh_date_string(value)
            if date_value:
                result["data_date"] = date_value
                break
    if not result["data_date"]:
        date_value = _find_date_in_text(csv_text)
        if date_value:
            result["data_date"] = date_value

    result["scope"] = _detect_scope(csv_text, file_name)
    result["segment"] = "Capital Market"

    fii_row = _find_row(df, category_col, ["fii", "fpi", "foreign"])
    dii_row = _find_row(df, category_col, ["dii", "domestic"])

    _apply_row(result, "fii", fii_row, buy_col, sell_col, net_col)
    _apply_row(result, "dii", dii_row, buy_col, sell_col, net_col)

    if result["fii_net"] is not None and result["dii_net"] is not None:
        result["status"] = "OK"
    elif result["fii_net"] is not None or result["dii_net"] is not None:
        result["status"] = "PARTIAL"
        result["warnings"].append("Only one of FII/FPI or DII rows was detected.")
    else:
        result["status"] = "FAILED"
        result["warnings"].append("FII/FPI and DII net values were not detected.")
    if not result["data_date"]:
        result["warnings"].append("FII/DII data date was not detected.")
        if result["status"] == "OK":
            result["status"] = "PARTIAL"
    return result


def parse_manual_entry(fii_net: Any, dii_net: Any, data_date: Any, reason: str = "") -> dict[str, Any]:
    result = empty_fii_dii("manual_entry", "PARTIAL")
    result.update({
        "source": "Manual Entry",
        "fii_net": safe_float(fii_net),
        "dii_net": safe_float(dii_net),
        "data_date": fresh_date_string(data_date) or str(data_date or "").strip() or None,
        "scope": "Manual",
        "manual_reason": str(reason or "").strip(),
    })
    if result["fii_net"] is not None and result["dii_net"] is not None and result["data_date"]:
        result["status"] = "OK"
    else:
        result["warnings"].append("Manual FII/DII entry is incomplete.")
    return result


def _read_csv_flexibly(csv_text: str) -> pd.DataFrame:
    errors = []
    for skiprows in (0, 1, 2, 3, 4, 5):
        try:
            df = pd.read_csv(StringIO(csv_text), skiprows=skiprows)
            if len(df.columns) >= 2:
                return df.dropna(how="all")
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError("; ".join(errors[-2:]) or "unknown CSV structure")


def _normalize_column(column: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(column or "").strip().lower())


def _first_col(df: pd.DataFrame, names: list[str]) -> str | None:
    for col in df.columns:
        if any(name in col for name in names):
            return col
    return None


def _detect_category_col(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        values = " ".join(str(value).lower() for value in df[col].head(12).tolist())
        if any(token in values for token in ("fii", "fpi", "dii", "foreign", "domestic")):
            return col
    return None


def _find_row(df: pd.DataFrame, category_col: str | None, tokens: list[str]) -> pd.Series | None:
    if not category_col:
        return None
    for _, row in df.iterrows():
        text = str(row.get(category_col, "")).lower()
        if any(token in text for token in tokens):
            return row
    return None


def _apply_row(result: dict[str, Any], prefix: str, row: pd.Series | None, buy_col: str | None, sell_col: str | None, net_col: str | None) -> None:
    if row is None:
        return
    result[f"{prefix}_buy"] = safe_float(row.get(buy_col)) if buy_col else None
    result[f"{prefix}_sell"] = safe_float(row.get(sell_col)) if sell_col else None
    result[f"{prefix}_net"] = safe_float(row.get(net_col)) if net_col else None


def _detect_scope(text: str, file_name: str = "") -> str:
    lowered = f"{text[:500]} {file_name}".lower()
    return "NSE+BSE+MSEI" if "bse" in lowered or "msei" in lowered else "NSE only"


def _find_date_in_text(text: str) -> str | None:
    for pattern in (r"\d{4}-\d{2}-\d{2}", r"\d{2}-[A-Za-z]{3}-\d{4}", r"\d{2}/\d{2}/\d{4}", r"\d{2}-\d{2}-\d{4}"):
        match = re.search(pattern, text)
        if match:
            date_value = fresh_date_string(match.group(0))
            if date_value:
                return date_value
    return None
