from __future__ import annotations

from typing import Any, Protocol


class BrokerProtocol(Protocol):
    def orders(self) -> list[dict[str, Any]]:
        ...

    def positions(self) -> list[dict[str, Any]]:
        ...

    def available_margin(self) -> float:
        ...

