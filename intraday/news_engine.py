from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from typing import Any
from xml.etree import ElementTree


POSITIVE_WORDS = {"gain", "wins", "order", "growth", "profit", "upgrade", "beats", "launch", "approval"}
NEGATIVE_WORDS = {"loss", "fraud", "downgrade", "misses", "probe", "fall", "weak", "debt", "delay"}


@dataclass
class NewsItem:
    symbol: str
    headline: str
    source: str = "Manual"
    url: str = ""
    timestamp: str = ""
    sentiment: str = "Unknown"
    impact: str = "Low"
    relevance: float = 0.0
    summary: str = ""
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "headline": self.headline,
            "source": self.source,
            "url": self.url,
            "timestamp": self.timestamp or datetime.now().isoformat(timespec="seconds"),
            "sentiment": self.sentiment,
            "impact": self.impact,
            "relevance": self.relevance,
            "summary": self.summary,
            "raw": self.raw or {},
        }


class ManualNewsImportAdapter:
    def collect(self, symbols: list[str], payload: dict[str, Any] | None = None) -> list[NewsItem]:
        payload = payload or {}
        rows = payload.get("news") or []
        items = []
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if symbol and symbol not in symbols:
                continue
            headline = str(row.get("headline") or "").strip()
            if not headline:
                continue
            item = NewsItem(
                symbol=symbol,
                headline=headline,
                source=str(row.get("source") or "Manual"),
                url=str(row.get("url") or ""),
                timestamp=str(row.get("timestamp") or datetime.now().isoformat(timespec="seconds")),
                raw=row,
            )
            items.append(score_news_item(item))
        return items


class RssNewsAdapter:
    def __init__(self, max_items_per_symbol: int = 2, timeout_seconds: float = 3.0):
        self.max_items_per_symbol = max(1, int(max_items_per_symbol))
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    def collect(self, symbols: list[str], payload: dict[str, Any] | None = None) -> list[NewsItem]:
        payload = payload or {}
        if not payload.get("live_news_enabled") and not payload.get("fetch_live_news"):
            return []
        items: list[NewsItem] = []
        for symbol in symbols:
            items.extend(self._collect_symbol(symbol))
        return items

    def _collect_symbol(self, symbol: str) -> list[NewsItem]:
        query = quote_plus(f"{symbol} stock NSE India")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        request = Request(url, headers={"User-Agent": "TradeBotV5/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            xml = response.read()
        root = ElementTree.fromstring(xml)
        rows = []
        for item in root.findall(".//item")[: self.max_items_per_symbol]:
            headline = _text(item.find("title"))
            if not headline:
                continue
            timestamp = _rss_timestamp(_text(item.find("pubDate")))
            rows.append(score_news_item(NewsItem(
                symbol=str(symbol or "").upper(),
                headline=headline,
                source=_text(item.find("source")) or "Google News",
                url=_text(item.find("link")),
                timestamp=timestamp,
                raw={"provider": "google_news_rss"},
            )))
        return rows


class ZerodhaPulseNewsAdapter:
    url = "https://pulse.zerodha.com/"

    def __init__(
        self,
        max_items: int = 40,
        max_items_per_symbol: int = 2,
        include_market_items: int = 5,
        timeout_seconds: float = 3.0,
    ):
        self.max_items = max(1, int(max_items))
        self.max_items_per_symbol = max(1, int(max_items_per_symbol))
        self.include_market_items = max(0, int(include_market_items))
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    def collect(self, symbols: list[str], payload: dict[str, Any] | None = None) -> list[NewsItem]:
        payload = payload or {}
        if not payload.get("live_news_enabled") and not payload.get("fetch_live_news"):
            return []
        if str(payload.get("zerodha_pulse_enabled", "true")).strip().lower() in {"0", "false", "no", "off"}:
            return []
        request = Request(self.url, headers={"User-Agent": "TradeBotV5/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            html = response.read().decode("utf-8", errors="replace")
        return parse_zerodha_pulse_html(
            html,
            symbols,
            max_items=self.max_items,
            max_items_per_symbol=self.max_items_per_symbol,
            include_market_items=self.include_market_items,
        )


class FinnhubNewsAdapter:
    def collect(self, _symbols: list[str], _payload: dict[str, Any] | None = None) -> list[NewsItem]:
        return []


class AlphaVantageNewsAdapter:
    def collect(self, _symbols: list[str], _payload: dict[str, Any] | None = None) -> list[NewsItem]:
        return []


class GdeltNewsAdapter:
    def collect(self, _symbols: list[str], _payload: dict[str, Any] | None = None) -> list[NewsItem]:
        return []


def parse_zerodha_pulse_html(
    html: str,
    symbols: list[str],
    max_items: int = 40,
    max_items_per_symbol: int = 2,
    include_market_items: int = 5,
) -> list[NewsItem]:
    parser = _PulseHtmlParser()
    parser.feed(html or "")
    parser.close()
    selected_symbols = [str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()]
    selected_symbols = list(dict.fromkeys(selected_symbols))
    rows = parser.rows[: max(1, int(max_items))]
    items: list[NewsItem] = []
    per_symbol_counts = {symbol: 0 for symbol in selected_symbols}
    market_count = 0

    for row in rows:
        headline = _clean_text(row.get("headline", ""))
        if not headline:
            continue
        summary = _clean_text(row.get("summary", ""))
        source_name = _clean_source(row.get("source", ""))
        source = f"Zerodha Pulse / {source_name}" if source_name else "Zerodha Pulse"
        matched_symbols = _matching_pulse_symbols(row, selected_symbols)
        if matched_symbols:
            for symbol in matched_symbols:
                if per_symbol_counts.get(symbol, 0) >= max_items_per_symbol:
                    continue
                item = NewsItem(
                    symbol=symbol,
                    headline=headline,
                    source=source,
                    url=_clean_text(row.get("url", "")),
                    timestamp=_pulse_timestamp(row.get("timestamp", "")),
                    summary=summary,
                    raw={"provider": "zerodha_pulse", "source": source_name, "matched_symbols": matched_symbols},
                )
                items.append(score_news_item(item))
                per_symbol_counts[symbol] = per_symbol_counts.get(symbol, 0) + 1
            continue

        if market_count < include_market_items and _is_market_pulse_row(row):
            item = NewsItem(
                symbol="MARKET",
                headline=headline,
                source=source,
                url=_clean_text(row.get("url", "")),
                timestamp=_pulse_timestamp(row.get("timestamp", "")),
                summary=summary,
                raw={"provider": "zerodha_pulse", "source": source_name, "market_context": True},
            )
            item = score_news_item(item)
            item.relevance = min(item.relevance, 0.35)
            items.append(item)
            market_count += 1

    return items


def score_news_item(item: NewsItem) -> NewsItem:
    words = {part.strip(".,:;!?()[]{}").lower() for part in item.headline.split()}
    positive = len(words & POSITIVE_WORDS)
    negative = len(words & NEGATIVE_WORDS)
    if positive > negative:
        sentiment = "Positive"
        impact = "Medium" if positive >= 2 else "Low"
        relevance = 0.75
    elif negative > positive:
        sentiment = "Negative"
        impact = "Medium" if negative >= 2 else "Low"
        relevance = 0.75
    else:
        sentiment = "Neutral"
        impact = "Low"
        relevance = 0.45
    item.sentiment = sentiment
    item.impact = impact
    item.relevance = relevance
    item.summary = item.summary or item.headline
    return item


def _text(node) -> str:
    return (node.text or "").strip() if node is not None else ""


def _rss_timestamp(value: str) -> str:
    if not value:
        return datetime.now().isoformat(timespec="seconds")
    try:
        return parsedate_to_datetime(value).isoformat(timespec="seconds")
    except (TypeError, ValueError, IndexError):
        return datetime.now().isoformat(timespec="seconds")


def _pulse_timestamp(value: str) -> str:
    value = _clean_text(value)
    if not value:
        return datetime.now().isoformat(timespec="seconds")
    for fmt in ("%I:%M %p, %d %b %Y", "%I:%M%p, %d %b %Y"):
        try:
            return datetime.strptime(value, fmt).isoformat(timespec="seconds")
        except ValueError:
            pass
    return datetime.now().isoformat(timespec="seconds")


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _clean_source(value: str) -> str:
    return _clean_text(str(value or "").replace("\u2014", " ").strip(" -"))


def _matching_pulse_symbols(row: dict[str, str], symbols: list[str]) -> list[str]:
    text = f"{row.get('headline', '')} {row.get('summary', '')} {row.get('url', '')}".upper()
    matches = []
    for symbol in symbols:
        aliases = _symbol_aliases(symbol)
        if any(_has_phrase(text, alias) for alias in aliases):
            matches.append(symbol)
    return matches


def _symbol_aliases(symbol: str) -> list[str]:
    symbol = str(symbol or "").strip().upper()
    aliases = {
        "INFY": ["INFY", "INFOSYS"],
        "RELIANCE": ["RELIANCE", "RELIANCE INDUSTRIES", "RIL"],
        "TCS": ["TCS", "TATA CONSULTANCY"],
        "HDFCBANK": ["HDFCBANK", "HDFC BANK"],
        "ICICIBANK": ["ICICIBANK", "ICICI BANK"],
        "SBIN": ["SBIN", "SBI", "STATE BANK OF INDIA"],
        "AXISBANK": ["AXISBANK", "AXIS BANK"],
        "KOTAKBANK": ["KOTAKBANK", "KOTAK MAHINDRA"],
        "BHARTIARTL": ["BHARTIARTL", "BHARTI AIRTEL", "AIRTEL"],
        "LT": ["LT", "LARSEN", "LARSEN & TOUBRO", "L&T"],
        "M&M": ["M&M", "MAHINDRA"],
        "MARUTI": ["MARUTI", "MARUTI SUZUKI"],
        "TATAMOTORS": ["TATAMOTORS", "TATA MOTORS"],
        "TATASTEEL": ["TATASTEEL", "TATA STEEL"],
        "SUNPHARMA": ["SUNPHARMA", "SUN PHARMA"],
        "ADANIENT": ["ADANIENT", "ADANI ENTERPRISES"],
        "ADANIPORTS": ["ADANIPORTS", "ADANI PORTS"],
        "ULTRACEMCO": ["ULTRACEMCO", "ULTRATECH CEMENT"],
        "BAJFINANCE": ["BAJFINANCE", "BAJAJ FINANCE"],
        "BAJAJFINSV": ["BAJAJFINSV", "BAJAJ FINSERV"],
        "HCLTECH": ["HCLTECH", "HCL TECH", "HCLTECHNOLOGIES"],
        "TECHM": ["TECHM", "TECH MAHINDRA"],
        "POWERGRID": ["POWERGRID", "POWER GRID"],
        "COALINDIA": ["COALINDIA", "COAL INDIA"],
        "HINDUNILVR": ["HINDUNILVR", "HUL", "HINDUSTAN UNILEVER"],
        "ASIANPAINT": ["ASIANPAINT", "ASIAN PAINTS"],
        "NESTLEIND": ["NESTLEIND", "NESTLE INDIA"],
        "JSWSTEEL": ["JSWSTEEL", "JSW STEEL"],
        "INDUSINDBK": ["INDUSINDBK", "INDUSIND BANK"],
        "EICHERMOT": ["EICHERMOT", "EICHER MOTORS"],
        "HEROMOTOCO": ["HEROMOTOCO", "HERO MOTOCORP"],
        "DRREDDY": ["DRREDDY", "DR REDDY", "DR. REDDY"],
        "DIVISLAB": ["DIVISLAB", "DIVI'S LAB", "DIVIS LAB"],
        "APOLLOHOSP": ["APOLLOHOSP", "APOLLO HOSPITALS"],
    }
    return list(dict.fromkeys([symbol, *aliases.get(symbol, [])]))


def _has_phrase(text: str, phrase: str) -> bool:
    phrase = str(phrase or "").strip().upper()
    if not phrase:
        return False
    if any(ch in phrase for ch in "&.'"):
        return phrase in text
    return re.search(rf"(?<![A-Z0-9]){re.escape(phrase)}(?![A-Z0-9])", text) is not None


def _is_market_pulse_row(row: dict[str, str]) -> bool:
    text = f"{row.get('headline', '')} {row.get('summary', '')}".upper()
    keywords = {
        "NIFTY",
        "SENSEX",
        "BANK NIFTY",
        "STOCK MARKET",
        "STOCK MARKETS",
        "DALAL STREET",
        "STOCKS",
        "NSE",
        "BSE",
        "RBI",
        "SEBI",
        "FII",
        "DII",
        "FOREIGN INVESTOR",
        "CRUDE",
        "RUPEE",
        "INFLATION",
    }
    return any(keyword in text for keyword in keywords)


class _PulseHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._item_depth = 0
        self._in_title = False
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if tag == "li" and "item" in classes and self._current is None:
            self._current = {"headline": "", "summary": "", "source": "", "timestamp": "", "url": ""}
            self._item_depth = 1
            return
        if tag == "li" and self._current is not None:
            self._item_depth += 1
            return
        if self._current is None:
            return
        if tag == "h2" and "title" in classes:
            self._in_title = True
        elif tag == "a" and self._in_title:
            self._current["url"] = attrs_dict.get("href", "")
            self._capture = "headline"
        elif tag == "div" and "desc" in classes:
            self._capture = "summary"
        elif tag == "span" and "date" in classes:
            self._current["timestamp"] = attrs_dict.get("title", "")
            self._capture = "date"
        elif tag == "span" and "feed" in classes:
            self._capture = "source"

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag == "li":
            self._item_depth -= 1
            if self._item_depth <= 0:
                if _clean_text(self._current.get("headline", "")):
                    self.rows.append(dict(self._current))
                self._current = None
                self._capture = None
                self._in_title = False
            return
        if tag == "h2":
            self._in_title = False
        if (tag == "a" and self._capture == "headline") or (tag == "div" and self._capture == "summary") or (tag == "span" and self._capture in {"date", "source"}):
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture in {"headline", "summary", "source"}:
            self._current[self._capture] = f"{self._current.get(self._capture, '')} {data}".strip()


class NewsEngine:
    def __init__(self, adapters: list[Any] | None = None):
        self.adapters = adapters or [ManualNewsImportAdapter(), ZerodhaPulseNewsAdapter(), RssNewsAdapter(), GdeltNewsAdapter()]
        self.last_status: dict[str, Any] = {
            "status": "NOT_RUN",
            "message": "News engine has not run yet.",
            "adapter_status": [],
        }

    def collect(self, symbols: list[str], payload: dict[str, Any] | None = None) -> list[NewsItem]:
        seen = set()
        collected = []
        adapter_status = []
        for adapter in self.adapters:
            name = adapter.__class__.__name__
            try:
                rows = adapter.collect(symbols, payload) or []
            except Exception:
                adapter_status.append({"adapter": name, "status": "FAILED", "items": 0})
                continue
            adapter_status.append({"adapter": name, "status": "OK", "items": len(rows)})
            for item in rows:
                key = (item.symbol, item.headline.lower(), item.source.lower())
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
        live_requested = bool((payload or {}).get("live_news_enabled") or (payload or {}).get("fetch_live_news"))
        if collected:
            status = "OK"
            message = f"Collected {len(collected)} news headline(s)."
        elif live_requested:
            status = "UNAVAILABLE"
            message = "No live headlines returned by configured sources; news score is treated as neutral."
        else:
            status = "DISABLED"
            message = "Live news is disabled; news score is treated as neutral."
        self.last_status = {
            "status": status,
            "message": message,
            "adapter_status": adapter_status,
            "symbols": list(symbols),
            "live_news_enabled": live_requested,
        }
        return collected


def sentiment_score_for_symbol(items: list[NewsItem], symbol: str) -> dict:
    symbol = str(symbol or "").upper()
    matched = [item for item in items if not item.symbol or item.symbol == symbol]
    if not matched:
        return {"score": 0.0, "sentiment": "Unavailable", "items": []}
    score = 0.0
    for item in matched:
        weight = 10.0 if item.impact in {"High", "Critical"} else 5.0 if item.impact == "Medium" else 2.0
        if item.sentiment == "Positive":
            score += weight
        elif item.sentiment == "Negative":
            score -= weight
    return {"score": max(-10.0, min(10.0, score)), "sentiment": matched[0].sentiment, "items": [item.to_dict() for item in matched]}
