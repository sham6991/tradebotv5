from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from options_auto.data.live_index_candles import LiveIndexCandleStore
from options_auto.data.live_option_candles import LiveOptionCandleStore
from options_auto.data.options_feed_health import OptionsFeedHealth, QUOTE_SNAPSHOT_POLLING, WEBSOCKET_TICKS
from options_auto.data.live_quote_provider import LiveQuoteProvider


class OptionsLiveFeed:
    def __init__(self) -> None:
        self.index_candles = LiveIndexCandleStore()
        self.option_candles = LiveOptionCandleStore()
        self.health = OptionsFeedHealth()
        self.subscribed_tokens: list[int] = []
        self.websocket_connected = False
        self.quote_polling_fallback = True
        self.latest_quotes: dict[str, dict[str, Any]] = {}
        self.tick_buffers: dict[str, list[dict[str, Any]]] = {"INDEX": [], "CE": [], "PE": []}
        self._token_roles: dict[int, str] = {}
        self._contracts_by_token: dict[int, dict[str, Any]] = {}
        self._normalizer = LiveQuoteProvider()

    def subscribe_locked_contracts(self, index_token: Any, ce_contract: dict[str, Any], pe_contract: dict[str, Any]) -> dict[str, Any]:
        previous_tokens = {role: token for token, role in self._token_roles.items()}
        tokens = [_token(index_token), _token(ce_contract), _token(pe_contract)]
        self.subscribed_tokens = [token for token in tokens if token > 0]
        self._token_roles = {}
        self._contracts_by_token = {}
        if _token(index_token) > 0:
            self._token_roles[_token(index_token)] = "INDEX"
        for role, contract in (("CE", ce_contract), ("PE", pe_contract)):
            token = _token(contract)
            if token > 0:
                self._token_roles[token] = role
                self._contracts_by_token[token] = dict(contract or {})
        current_tokens = {role: token for token, role in self._token_roles.items()}
        if self.subscribed_tokens:
            self.health.mark_expected_roles(["INDEX", "CE", "PE"])
        else:
            self.health.mark_expected_roles([])
        for role in ("INDEX", "CE", "PE"):
            if previous_tokens.get(role) != current_tokens.get(role):
                self.tick_buffers[role] = []
        return self.snapshot()

    def mark_websocket_connected(self, connected: bool = True) -> None:
        self.websocket_connected = bool(connected)
        self.health.mark_mode(WEBSOCKET_TICKS if connected else QUOTE_SNAPSHOT_POLLING)

    def mark_websocket_disconnected(self, reason: str = "") -> None:
        self.websocket_connected = False
        self.health.mark_disconnected(reason)

    def on_tick(self, tick: dict[str, Any], *, role: str, interval: str = "3minute", client: Any | None = None, underlying: str = "NIFTY", mode: str = "PAPER") -> dict[str, Any]:
        token = _token(tick.get("instrument_token") or tick.get("token"))
        role = str(role or self._token_roles.get(token) or "").upper()
        self.health.mark_mode(WEBSOCKET_TICKS)
        if role == "INDEX":
            result = self.index_candles.update(
                client=client,
                instrument_token=token,
                underlying=underlying,
                mode=mode,
                interval=interval,
                spot=_price(tick),
                timestamp=tick.get("timestamp") or tick.get("exchange_timestamp"),
                volume=tick.get("volume") or tick.get("volume_traded"),
            )
        else:
            result = self.option_candles.update(token, tick, interval=interval)
        self.health.mark_tick(role, tick.get("timestamp") or tick.get("exchange_timestamp"))
        self._record_latest_quote(token, role, tick)
        self._record_tick_buffer(token, role, tick)
        return result

    def snapshot(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        health = self.health.evaluate(settings)
        return {
            "data_mode": health["data_mode"],
            "websocket_connected": self.websocket_connected,
            "quote_polling_fallback": self.quote_polling_fallback,
            "subscribed_tokens": list(self.subscribed_tokens),
            "health": health,
            "index_candles": self.index_candles.snapshot(),
            "option_candles": self.option_candles.snapshot(),
            "latest_quote_count": len({id(item) for item in self.latest_quotes.values()}),
            "tick_streams": {role: list(rows[-80:]) for role, rows in self.tick_buffers.items()},
        }

    def index_spot(self, underlying: str, mode: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        quote = dict(self.latest_quotes.get("INDEX") or {})
        if not quote:
            return {}
        age = _age_seconds(quote)
        max_age = _number((settings or {}).get("max_tick_age_seconds"), (settings or {}).get("max_quote_age_seconds") or 3)
        ltp = _number(quote.get("ltp"), quote.get("last_price"))
        if ltp <= 0 or age > max_age:
            return {}
        return {
            "underlying": str(underlying or "NIFTY").upper(),
            "spot": ltp,
            "spot_source": "zerodha_websocket_tick",
            "quote_key": quote.get("quote_key") or "WEBSOCKET:INDEX",
            "timestamp": quote.get("timestamp") or "",
            "age_seconds": age,
            "fresh": True,
            "demo_data": False,
            "blockers": [],
            "warnings": [],
            "next_action": "",
        }

    def quote_candidates(self, candidates: list[dict[str, Any]], settings: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        max_age = _number(settings.get("max_tick_age_seconds"), settings.get("max_quote_age_seconds") or 3)
        quotes: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        stale: list[str] = []
        requested: list[str] = []
        for candidate in candidates or []:
            keys = _candidate_keys(candidate)
            requested.append(keys[0] if keys else "")
            quote = {}
            for key in keys:
                if key and key in self.latest_quotes:
                    quote = dict(self.latest_quotes[key] or {})
                    break
            if not quote:
                if keys:
                    missing.append(keys[0])
                continue
            age = _age_seconds(quote)
            if age > max_age:
                stale.append(keys[0] if keys else quote.get("quote_key") or "")
                continue
            enriched = {
                **quote,
                "exchange": candidate.get("exchange") or quote.get("exchange"),
                "tradingsymbol": candidate.get("tradingsymbol") or quote.get("tradingsymbol"),
                "instrument_token": candidate.get("instrument_token") or candidate.get("token") or quote.get("instrument_token"),
                "tick_size": candidate.get("tick_size") or quote.get("tick_size") or 0.05,
                "age_seconds": age,
                "stale": False,
                "source": "zerodha_websocket_tick",
                "quote_source": "zerodha_websocket_tick",
            }
            for key in keys:
                if key:
                    quotes[key] = enriched
        warnings = []
        if missing:
            warnings.append(f"{len(missing)} locked contract websocket quote{'s are' if len(missing) != 1 else ' is'} not ready.")
        if stale:
            warnings.append(f"{len(stale)} locked contract websocket quote{'s are' if len(stale) != 1 else ' is'} stale.")
        return {
            "quotes": quotes,
            "missing_quote_keys": list(dict.fromkeys(missing + stale)),
            "errors": [],
            "warnings": warnings,
            "requested_quote_keys": [key for key in requested if key],
            "valid_quote_count": len({item.get("quote_key") for item in quotes.values() if item.get("quote_key")}),
            "quote_source": "zerodha_websocket_tick",
            "data_mode": WEBSOCKET_TICKS,
            "blocked": False,
        }

    def index_candle_context(self, *, underlying: str, mode: str, interval: str) -> dict[str, Any]:
        return self.index_candles.context(mode=mode, underlying=underlying, interval=interval)

    def _record_latest_quote(self, token: int, role: str, tick: dict[str, Any]) -> None:
        symbol_meta = dict(self._contracts_by_token.get(token) or {})
        symbol = str(symbol_meta.get("tradingsymbol") or tick.get("tradingsymbol") or role or token or "").upper()
        exchange = str(symbol_meta.get("exchange") or tick.get("exchange") or "").upper()
        quote_key = f"{exchange}:{symbol}" if exchange and symbol and role != "INDEX" else str(symbol or role or token)
        normalized = self._normalizer.normalize_quote(quote_key, {**dict(tick or {}), "source": "zerodha_websocket_tick"})
        depth = tick.get("depth") if isinstance(tick.get("depth"), dict) else {}
        depth_buy = list(depth.get("buy") or [])
        depth_sell = list(depth.get("sell") or [])
        bid = _number(normalized.get("bid"))
        ask = _number(normalized.get("ask"))
        bid_qty = _number(normalized.get("bid_qty"))
        ask_qty = _number(normalized.get("ask_qty"))
        timestamp = tick.get("timestamp") or tick.get("last_trade_time") or tick.get("exchange_timestamp") or datetime.now().isoformat(timespec="seconds")
        normalized.update({
            "source": "zerodha_websocket_tick",
            "quote_source": "zerodha_websocket_tick",
            "quote_key": quote_key,
            "exchange": exchange,
            "tradingsymbol": symbol if role != "INDEX" else "",
            "instrument_token": token,
            "timestamp": _timestamp_text(timestamp),
            "age_seconds": _age_seconds({"timestamp": timestamp}),
            "demo_data": False,
            "tick_size": symbol_meta.get("tick_size") or tick.get("tick_size") or 0.05,
            "depth_present": bool(depth_buy or depth_sell),
            "bid_present": bid > 0,
            "ask_present": ask > 0,
            "bid_qty_present": bid_qty > 0,
            "ask_qty_present": ask_qty > 0,
            "depth_buy_levels": len(depth_buy),
            "depth_sell_levels": len(depth_sell),
        })
        keys = [role, str(token), symbol, quote_key]
        for key in keys:
            if key:
                self.latest_quotes[str(key).upper() if key == symbol else str(key)] = normalized

    def _record_tick_buffer(self, token: int, role: str, tick: dict[str, Any]) -> None:
        role = str(role or "").upper()
        if role not in self.tick_buffers:
            return
        quote = dict(self.latest_quotes.get(role) or {})
        symbol_meta = dict(self._contracts_by_token.get(token) or {})
        depth_present = bool(quote.get("depth_present"))
        timestamp = tick.get("timestamp") or tick.get("last_trade_time") or tick.get("exchange_timestamp") or quote.get("timestamp") or datetime.now().isoformat(timespec="seconds")
        exchange_timestamp = tick.get("exchange_timestamp") or tick.get("timestamp") or quote.get("timestamp") or ""
        row = {
            "observed_at": datetime.now().isoformat(timespec="seconds"),
            "role": role,
            "instrument_token": token,
            "tradingsymbol": quote.get("tradingsymbol") or symbol_meta.get("tradingsymbol") or "",
            "exchange": quote.get("exchange") or symbol_meta.get("exchange") or "",
            "quote_key": quote.get("quote_key") or "",
            "ltp": quote.get("ltp") if quote else _price(tick),
            "bid": quote.get("bid") if quote else 0,
            "ask": quote.get("ask") if quote else 0,
            "bid_qty": quote.get("bid_qty") if quote else 0,
            "ask_qty": quote.get("ask_qty") if quote else 0,
            "spread_pct": quote.get("spread_pct") if quote else 100.0,
            "depth_imbalance": quote.get("depth_imbalance") if quote else 0,
            "volume": quote.get("volume") if quote else tick.get("volume") or tick.get("volume_traded") or 0,
            "oi": quote.get("oi") if quote else tick.get("oi") or 0,
            "timestamp": _timestamp_text(timestamp),
            "exchange_timestamp": _timestamp_text(exchange_timestamp),
            "age_seconds": _age_seconds({"timestamp": timestamp}),
            "depth_present": depth_present,
            "source": quote.get("source") or "zerodha_websocket_tick",
        }
        self.tick_buffers[role].append(row)
        if len(self.tick_buffers[role]) > 200:
            self.tick_buffers[role] = self.tick_buffers[role][-200:]


def _token(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("instrument_token") or value.get("token")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _price(tick: dict[str, Any]) -> float:
    for key in ("last_price", "ltp", "price", "close"):
        try:
            value = float(tick.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return 0.0


def _candidate_keys(candidate: dict[str, Any]) -> list[str]:
    exchange = str(candidate.get("exchange") or "NFO").upper()
    symbol = str(candidate.get("tradingsymbol") or "").upper()
    token = str(candidate.get("instrument_token") or candidate.get("token") or "")
    keys = []
    if exchange and symbol:
        keys.append(f"{exchange}:{symbol}")
    if token:
        keys.append(token)
    if symbol:
        keys.append(symbol)
    return keys


def _timestamp_text(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")
    return str(value or "")


def _age_seconds(quote_or_value: Any) -> float:
    if isinstance(quote_or_value, dict):
        if quote_or_value.get("age_seconds") not in ("", None):
            return _number(quote_or_value.get("age_seconds"), 9999.0)
        value = quote_or_value.get("timestamp") or quote_or_value.get("last_trade_time") or quote_or_value.get("exchange_timestamp")
    else:
        value = quote_or_value
    if value in ("", None):
        return 0.0
    try:
        when = value if hasattr(value, "timestamp") else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if when.tzinfo is None:
            return max(0.0, (datetime.now() - when).total_seconds())
        return max(0.0, (datetime.now(timezone.utc) - when).total_seconds())
    except Exception:
        return 0.0


def _number(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0
