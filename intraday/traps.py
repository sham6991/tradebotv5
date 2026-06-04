from __future__ import annotations


def detect_trap(candles: list[dict], side: str, relative_volume: float, spread_pct: float) -> dict:
    side = str(side or "").upper()
    if len(candles or []) < 3:
        return {"trap_score": 0.0, "trap_warning": "INSUFFICIENT_DATA", "reasons": ["Need more candles"]}
    current = candles[-1]
    previous = candles[-2]
    high = float(current.get("high") or current.get("close") or 0)
    low = float(current.get("low") or current.get("close") or 0)
    close = float(current.get("close") or 0)
    prev_high = float(previous.get("high") or previous.get("close") or 0)
    prev_low = float(previous.get("low") or previous.get("close") or 0)
    score = 0.0
    reasons = []
    if side == "LONG" and high > prev_high and close < prev_high:
        score += 45
        reasons.append("Breakout failed back below previous high")
    if side == "SHORT" and low < prev_low and close > prev_low:
        score += 45
        reasons.append("Breakdown failed back above previous low")
    if relative_volume < 1.0:
        score += 20
        reasons.append("Weak relative volume")
    if spread_pct > 0.08:
        score += 20
        reasons.append("Spread widened")
    if not reasons:
        return {"trap_score": 0.0, "trap_warning": "NONE", "reasons": []}
    warning = "HIGH" if score >= 60 else "MEDIUM" if score >= 30 else "LOW"
    return {"trap_score": min(100.0, score), "trap_warning": warning, "reasons": reasons}
