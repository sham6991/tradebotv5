from __future__ import annotations


class IntradayWebSocketManager:
    def __init__(self):
        self.connected = False
        self.symbols: list[str] = []

    def start(self, symbols: list[str]) -> dict:
        self.symbols = list(symbols)
        self.connected = False
        return {"connected": self.connected, "symbols": self.symbols, "message": "Live tick subscription is broker-adapter controlled."}

    def stop(self) -> dict:
        self.connected = False
        self.symbols = []
        return {"connected": False}
