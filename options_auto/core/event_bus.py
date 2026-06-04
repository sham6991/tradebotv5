from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers[str(event_type)].append(callback)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = {"event_type": str(event_type), **dict(payload or {})}
        for callback in list(self._subscribers.get(str(event_type), [])):
            callback(event)

