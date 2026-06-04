from __future__ import annotations

from typing import Any

import pandas as pd


class MarketReplayEngine:
    def replay(self, candles: pd.DataFrame | list[dict[str, Any]] | None, decisions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        frame = candles if isinstance(candles, pd.DataFrame) else pd.DataFrame(list(candles or []))
        decisions = [dict(item) for item in list(decisions or [])]
        timeline = []
        pnl = 0.0
        for index, row in frame.iterrows():
            visible = frame.iloc[: index + 1]
            decision = decisions[index] if index < len(decisions) else {"decision": "WAIT", "reason": "Replay decision placeholder."}
            pnl += float(decision.get("pnl_delta") or 0)
            timeline.append({
                "row": int(index),
                "datetime": str(row.get("datetime", "")),
                "close": float(row.get("close") or 0),
                "visible_rows": len(visible),
                "decision": decision.get("decision") or ("ALLOW" if decision.get("allowed") else "WAIT"),
                "reason": decision.get("reason") or decision.get("explanation") or "",
                "rejected_reasons": decision.get("blockers") or [],
                "pnl": round(pnl, 2),
            })
        return {
            "mode": "MARKET_REPLAY",
            "rows": len(frame),
            "timeline": timeline,
            "orders_placed": 0,
            "real_orders_placed": 0,
            "analysis_only": True,
        }
