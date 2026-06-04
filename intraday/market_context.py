from __future__ import annotations

from .market_cue_engine import classify_market_cue


def context_alignment(payload: dict | None = None) -> dict:
    payload = payload or {}
    cue = classify_market_cue(payload)
    data = cue.to_dict()
    data["trend"] = str(payload.get("market_trend") or payload.get("nifty_trend") or cue.state).title()
    data.update(cue.source_breakdown)
    return data
