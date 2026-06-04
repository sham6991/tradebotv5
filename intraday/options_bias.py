from __future__ import annotations


def calculate_options_bias(_symbol: str, payload: dict | None = None) -> dict:
    payload = payload or {}
    data = payload.get("options_bias") or {}
    if not data:
        return {"bias": "Unavailable", "score": 0.0, "reason": "Options data unavailable"}
    bias = str(data.get("bias") or "Neutral").title()
    if bias == "Bullish":
        score = 5.0
    elif bias == "Bearish":
        score = -5.0
    else:
        score = 0.0
    return {"bias": bias, "score": score, "reason": data.get("reason") or "Provided options context"}
