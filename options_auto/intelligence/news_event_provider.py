from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


PULSE_URL = "https://pulse.zerodha.com/"


@dataclass
class NewsItem:
    title: str
    url: str = ""
    summary: str = ""
    source: str = "Zerodha Pulse"
    published_at: str = ""
    fetched_at: str = ""
    age_minutes: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "headline": self.title,
            "url": self.url,
            "summary": self.summary,
            "source": self.source,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "age_minutes": self.age_minutes,
        }


@dataclass
class NewsProviderResult:
    provider: str
    status: str
    items: list[dict[str, Any]] = field(default_factory=list)
    fetched_at: str = ""
    fetched_at_epoch: float = 0.0
    source_url: str = PULSE_URL
    error: str = ""
    cache_status: str = "NETWORK"
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "items": list(self.items or []),
            "fetched_at": self.fetched_at,
            "fetched_at_epoch": self.fetched_at_epoch,
            "source_url": self.source_url,
            "error": self.error,
            "cache_status": self.cache_status,
            "stale": bool(self.stale),
        }


class PulseNewsProvider:
    """Fetches Zerodha Pulse headlines outside the tick path."""

    def __init__(
        self,
        *,
        source_url: str = PULSE_URL,
        cache_path: str | None = None,
        fetcher: Callable[[str, float], str] | None = None,
    ):
        self.source_url = source_url
        self.cache_path = cache_path or ""
        self.fetcher = fetcher

    def fetch(self, settings: dict[str, Any] | None = None) -> NewsProviderResult:
        settings = dict(settings or {})
        timeout = max(0.2, float(settings.get("news_event_fetch_timeout_seconds") or 1.5))
        max_items = max(1, int(float(settings.get("news_event_max_items") or 12)))
        now_epoch = time.time()
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            html = self._fetch_html(timeout)
            items = [item.to_dict() for item in _PulseHtmlParser.parse(html, fetched_at=fetched_at)[:max_items]]
            result = NewsProviderResult(
                provider="ZERODHA_PULSE",
                status="OK" if items else "PARSE_FAILED",
                items=items,
                fetched_at=fetched_at,
                fetched_at_epoch=now_epoch,
                source_url=self.source_url,
                error="" if items else "Zerodha Pulse returned no parseable headlines.",
            )
            if items:
                self._write_cache(result)
            return result
        except Exception as exc:
            cached = self._read_cache()
            if cached and bool(settings.get("news_event_use_stale_cache", True)):
                max_stale_minutes = max(1.0, float(settings.get("news_event_stale_cache_max_minutes") or 30.0))
                age_minutes = max(0.0, (now_epoch - float(cached.get("fetched_at_epoch") or 0.0)) / 60.0)
                if age_minutes <= max_stale_minutes:
                    return NewsProviderResult(
                        provider=str(cached.get("provider") or "ZERODHA_PULSE"),
                        status="STALE_CACHE",
                        items=list(cached.get("items") or []),
                        fetched_at=str(cached.get("fetched_at") or ""),
                        fetched_at_epoch=float(cached.get("fetched_at_epoch") or 0.0),
                        source_url=str(cached.get("source_url") or self.source_url),
                        error=f"Network fetch failed; using stale cache: {exc}",
                        cache_status="STALE",
                        stale=True,
                    )
            return NewsProviderResult(
                provider="ZERODHA_PULSE",
                status="FETCH_FAILED",
                items=[],
                fetched_at=fetched_at,
                fetched_at_epoch=now_epoch,
                source_url=self.source_url,
                error=str(exc),
                cache_status="FAILED",
            )

    def _fetch_html(self, timeout: float) -> str:
        if self.fetcher:
            return str(self.fetcher(self.source_url, timeout) or "")
        request = Request(self.source_url, headers={"User-Agent": "TradeBotV5 OptionsAuto/1.0"})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except URLError as exc:
            raise RuntimeError(f"Zerodha Pulse fetch failed: {exc}") from exc

    def _write_cache(self, result: NewsProviderResult) -> None:
        if not self.cache_path:
            return
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp_path = f"{self.cache_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(result.to_dict(), handle, indent=2, sort_keys=True)
            os.replace(tmp_path, self.cache_path)
        except OSError:
            return

    def _read_cache(self) -> dict[str, Any]:
        if not self.cache_path:
            return {}
        try:
            with open(self.cache_path, "r", encoding="utf-8") as handle:
                return dict(json.load(handle) or {})
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}


class _PulseHtmlParser(HTMLParser):
    def __init__(self, fetched_at: str):
        super().__init__(convert_charrefs=True)
        self.fetched_at = fetched_at
        self._anchor_href = ""
        self._capture_anchor = False
        self._anchor_text: list[str] = []
        self._capture_time = False
        self._time_text: list[str] = []
        self._last_item: NewsItem | None = None
        self.items: list[NewsItem] = []

    @classmethod
    def parse(cls, html: str, *, fetched_at: str) -> list[NewsItem]:
        parser = cls(fetched_at)
        parser.feed(html or "")
        parser.close()
        return _dedupe_items(parser.items)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        class_name = attrs_dict.get("class", "").lower()
        if tag.lower() == "a" and attrs_dict.get("href"):
            self._anchor_href = attrs_dict["href"]
            self._capture_anchor = True
            self._anchor_text = []
        if tag.lower() == "time" or "date" in class_name or "time" in class_name:
            self._capture_time = True
            self._time_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._capture_anchor:
            title = _clean_text(" ".join(self._anchor_text))
            url = _absolute_pulse_url(self._anchor_href)
            self._capture_anchor = False
            self._anchor_href = ""
            self._anchor_text = []
            if _looks_like_headline(title, url):
                item = NewsItem(title=title, url=url, fetched_at=self.fetched_at)
                self.items.append(item)
                self._last_item = item
        if self._capture_time and tag in {"time", "span", "div"}:
            text = _clean_text(" ".join(self._time_text))
            self._capture_time = False
            self._time_text = []
            if text and self._last_item and not self._last_item.published_at:
                self._last_item.published_at = text
                self._last_item.age_minutes = _relative_age_minutes(text)

    def handle_data(self, data: str) -> None:
        if self._capture_anchor:
            self._anchor_text.append(data)
        if self._capture_time:
            self._time_text.append(data)


def _clean_text(value: str) -> str:
    return " ".join(unescape(str(value or "")).split())


def _absolute_pulse_url(href: str) -> str:
    href = str(href or "").strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"https://pulse.zerodha.com{href}"
    return href


def _looks_like_headline(title: str, url: str) -> bool:
    if len(title) < 12:
        return False
    lowered = title.lower()
    if lowered in {"zerodha pulse", "login", "signup", "home"}:
        return False
    return bool(url)


def _dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        key = f"{item.title.lower()}|{item.url.lower()}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _relative_age_minutes(value: str) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    digits = "".join(ch if ch.isdigit() or ch == "." else " " for ch in text).split()
    amount = float(digits[0]) if digits else 0.0
    if "min" in text:
        return amount
    if "hour" in text or "hr" in text:
        return amount * 60.0
    if "day" in text:
        return amount * 1440.0
    if "just" in text or "now" in text:
        return 0.0
    return None
