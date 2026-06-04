from __future__ import annotations

from .constants import SIDE_LONG, SIDE_SHORT
from .models import Signal, StockSnapshot
from .scoring import build_signal


def rank_snapshots(snapshots: list[StockSnapshot]) -> list[StockSnapshot]:
    def rank(snapshot: StockSnapshot) -> float:
        score = max(snapshot.final_long_score, snapshot.final_short_score)
        liquidity_bonus = snapshot.liquidity_score * 0.08
        spread_penalty = snapshot.spread_pct * 25
        trap_penalty = snapshot.trap_score * 0.15
        return score + liquidity_bonus - spread_penalty - trap_penalty

    return sorted(snapshots, key=rank, reverse=True)


def select_best_signal(snapshots: list[StockSnapshot], settings, session_id: str) -> Signal | None:
    ranked = rank_snapshots(snapshots)
    signals = [build_signal(snapshot, settings, session_id) for snapshot in ranked]
    tradeable = [signal for signal in signals if signal.side in {SIDE_LONG, SIDE_SHORT}]
    if tradeable:
        return tradeable[0]
    return signals[0] if signals else None
