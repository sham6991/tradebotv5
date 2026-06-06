from __future__ import annotations

from typing import Any

from options_auto.data.live_index_candles import LiveIndexCandleStore
from options_auto.data.live_option_candles import LiveOptionCandleStore
from options_auto.data.options_feed_health import OptionsFeedHealth, QUOTE_SNAPSHOT_POLLING, WEBSOCKET_TICKS


class OptionsLiveFeed:
    def __init__(self) -> None:
        self.index_candles = LiveIndexCandleStore()
        self.option_candles = LiveOptionCandleStore()
        self.health = OptionsFeedHealth()
        self.subscribed_tokens: list[int] = []
        self.websocket_connected = False
        self.quote_polling_fallback = True

    def subscribe_locked_contracts(self, index_token: Any, ce_contract: dict[str, Any], pe_contract: dict[str, Any]) -> dict[str, Any]:
        tokens = [_token(index_token), _token(ce_contract), _token(pe_contract)]
        self.subscribed_tokens = [token for token in tokens if token > 0]
        return self.snapshot()

    def mark_websocket_connected(self, connected: bool = True) -> None:
        self.websocket_connected = bool(connected)
        self.health.mark_mode(WEBSOCKET_TICKS if connected else QUOTE_SNAPSHOT_POLLING)

    def on_tick(self, tick: dict[str, Any], *, role: str, interval: str = "3minute", client: Any | None = None, underlying: str = "NIFTY", mode: str = "PAPER") -> dict[str, Any]:
        role = str(role or "").upper()
        self.health.mark_mode(WEBSOCKET_TICKS)
        token = tick.get("instrument_token") or tick.get("token")
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
        }


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
