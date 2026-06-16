from __future__ import annotations

from typing import Any


class QuoteIdentityResolver:
    def resolve(self, role: str, instrument: dict[str, Any], quotes: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
        quotes = quotes or {}
        exchange = str(instrument.get("exchange") or "NFO").upper()
        tradingsymbol = str(instrument.get("tradingsymbol") or instrument.get("symbol") or "").upper()
        token = str(instrument.get("instrument_token") or instrument.get("token") or "")
        expected_quote_key = f"{exchange}:{tradingsymbol}" if exchange and tradingsymbol else ""
        candidates = [
            ("exchange_tradingsymbol", expected_quote_key),
            ("instrument_token", token),
            ("token", str(instrument.get("token") or "")),
            ("tradingsymbol", tradingsymbol),
        ]
        for resolved_by, key in candidates:
            if key and key in quotes:
                quote = dict(quotes.get(key) or {})
                mismatch = self._role_mismatch(role, tradingsymbol, quote)
                return {
                    "role": str(role or "").upper(),
                    "expected_exchange": exchange,
                    "expected_tradingsymbol": tradingsymbol,
                    "expected_quote_key": expected_quote_key,
                    "expected_token": token,
                    "resolved": not mismatch,
                    "resolved_by": resolved_by,
                    "resolved_quote_key": str(quote.get("quote_key") or key),
                    "resolved_token": str(quote.get("instrument_token") or quote.get("token") or ""),
                    "resolved_source": str(quote.get("quote_source") or quote.get("source") or ""),
                    "mismatch": mismatch,
                    "failure_reason": "Quote role mismatch." if mismatch else "",
                    "quote": {} if mismatch else quote,
                }
        return {
            "role": str(role or "").upper(),
            "expected_exchange": exchange,
            "expected_tradingsymbol": tradingsymbol,
            "expected_quote_key": expected_quote_key,
            "expected_token": token,
            "resolved": False,
            "resolved_by": "",
            "resolved_quote_key": "",
            "resolved_token": "",
            "resolved_source": "",
            "mismatch": False,
            "failure_reason": f"Quote not found for {expected_quote_key or tradingsymbol or token}.",
            "quote": {},
        }

    def quote_for(self, role: str, instrument: dict[str, Any], quotes: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
        return dict(self.resolve(role, instrument, quotes).get("quote") or {})

    def _role_mismatch(self, role: str, tradingsymbol: str, quote: dict[str, Any]) -> bool:
        role = str(role or "").upper()
        symbol = str(quote.get("tradingsymbol") or quote.get("symbol") or quote.get("quote_key") or tradingsymbol or "").upper()
        if role == "CE" and symbol.endswith("PE"):
            return True
        if role == "PE" and symbol.endswith("CE"):
            return True
        return False
