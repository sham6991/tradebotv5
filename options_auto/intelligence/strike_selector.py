from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT
from options_auto.indicators.option_metrics import liquidity_score, moneyness, premium_affordability_score
from options_auto.indicators.technicals import bid_ask_spread_pct, market_depth_imbalance
from options_auto.intelligence.trade_score_engine import TradeScoreEngine


@dataclass
class StrikeSelection:
    side: str
    selected: dict[str, Any] | None
    score: float
    candidates: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "selected": self.selected,
            "score": self.score,
            "candidates": self.candidates,
            "blockers": self.blockers,
        }


class StrikeSelector:
    def __init__(self, score_engine: TradeScoreEngine | None = None) -> None:
        self.score_engine = score_engine or TradeScoreEngine()

    def select(
        self,
        instruments: list[dict[str, Any]],
        quotes: dict[str, dict[str, Any]] | None,
        spot: float,
        side: str,
        settings: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> StrikeSelection:
        settings = dict(settings or {})
        context = dict(context or {})
        side = str(side or SIDE_WAIT).upper()
        if side not in {SIDE_CE, SIDE_PE}:
            return StrikeSelection(side=SIDE_WAIT, selected=None, score=0.0, blockers=["Regime says WAIT."])
        if not instruments:
            return StrikeSelection(side=side, selected=None, score=0.0, blockers=["No option instruments available."])
        quotes = quotes or {}
        threshold = float(settings.get("buy_score_threshold") or 70)
        underlying = str(settings.get("underlying") or "").upper()
        available = float(settings.get("available_capital") or settings.get("paper_starting_balance") or 0)
        candidates = []
        for instrument in instruments:
            if str(instrument.get("instrument_type") or instrument.get("option_type") or "").upper() != side:
                continue
            if underlying and str(instrument.get("name") or instrument.get("underlying") or "").upper() not in {"", underlying}:
                continue
            quote = self._quote_for(instrument, quotes)
            candidate = self._candidate(instrument, quote, spot, side, available, context)
            blockers = self._candidate_blockers(candidate, settings)
            candidate["blockers"] = blockers
            if not blockers:
                score = self.score_engine.score(candidate, context)
                candidate.update(score)
            else:
                candidate["score"] = 0.0
                candidate["breakdown"] = {}
            candidates.append(candidate)
        candidates.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        if not candidates:
            return StrikeSelection(side=side, selected=None, score=0.0, blockers=["No matching CE/PE contracts found."])
        best = candidates[0]
        if best.get("score", 0.0) < threshold:
            return StrikeSelection(side=side, selected=None, score=float(best.get("score", 0.0)), candidates=candidates[:8], blockers=[f"Best score {best.get('score', 0):.1f} is below threshold {threshold:.1f}."])
        return StrikeSelection(side=side, selected=best, score=float(best["score"]), candidates=candidates[:8])

    def _candidate(self, instrument: dict[str, Any], quote: dict[str, Any], spot: float, side: str, available: float, context: dict[str, Any]) -> dict[str, Any]:
        strike = float(instrument.get("strike") or 0)
        lot_size = int(float(instrument.get("lot_size") or instrument.get("lot") or 0))
        ltp = float(quote.get("last_price") or quote.get("ltp") or instrument.get("last_price") or 0)
        bid = quote.get("bid") or quote.get("best_bid") or 0
        ask = quote.get("ask") or quote.get("best_ask") or 0
        bid_qty = quote.get("bid_qty") or quote.get("buy_quantity") or 0
        ask_qty = quote.get("ask_qty") or quote.get("sell_quantity") or 0
        spread_pct = bid_ask_spread_pct(bid, ask, ltp)
        depth = market_depth_imbalance(bid_qty, ask_qty)
        volume = quote.get("volume") or instrument.get("volume") or 0
        oi = quote.get("oi") or instrument.get("oi") or 0
        liquidity = liquidity_score(volume=volume, oi=oi, spread_pct=spread_pct, depth_imbalance=depth)
        affordability = premium_affordability_score(ltp or ask, available, lot_size)
        distance_points = abs(strike - float(spot))
        distance_pct = (distance_points / float(spot) * 100) if spot else 100.0
        momentum = float(quote.get("momentum_score") or context.get("option_momentum_score") or 50)
        theta_score = max(0.0, 100.0 - distance_pct * 20.0)
        spread_depth = max(0.0, min(100.0, 100.0 - spread_pct * 55.0 - abs(depth) * 0.15))
        return {
            **instrument,
            "option_type": side,
            "strike": strike,
            "lot_size": lot_size,
            "ltp": ltp,
            "bid": bid,
            "ask": ask,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "spread_pct": spread_pct,
            "depth_imbalance": depth,
            "liquidity_score": liquidity,
            "affordability_score": affordability,
            "spread_depth_score": spread_depth,
            "momentum_score": momentum,
            "theta_score": theta_score,
            "moneyness": moneyness(spot, strike, side),
            "distance_from_spot": round(distance_points, 2),
            "distance_pct": round(distance_pct, 4),
        }

    def _candidate_blockers(self, candidate: dict[str, Any], settings: dict[str, Any]) -> list[str]:
        blockers = []
        if not candidate.get("instrument_token") and not candidate.get("token"):
            blockers.append("Missing instrument token.")
        if int(candidate.get("lot_size") or 0) <= 0:
            blockers.append("Missing lot size.")
        if float(candidate.get("ltp") or 0) <= 0:
            blockers.append("Missing option LTP.")
        if float(candidate.get("spread_pct") or 100) > float(settings.get("max_spread_pct") or 0.6):
            blockers.append("Spread too wide.")
        if int(float(candidate.get("bid_qty") or 0)) + int(float(candidate.get("ask_qty") or 0)) < int(settings.get("min_depth_qty") or 1):
            blockers.append("Depth too low.")
        if float(candidate.get("liquidity_score") or 0) < 25:
            blockers.append("Liquidity score too low.")
        if not settings.get("allow_deep_otm", False) and candidate.get("moneyness") == "OTM" and float(candidate.get("distance_pct") or 0) > 1.2:
            blockers.append("Deep OTM disabled.")
        return blockers

    def _quote_for(self, instrument: dict[str, Any], quotes: dict[str, dict[str, Any]]) -> dict[str, Any]:
        keys = [
            str(instrument.get("instrument_token") or ""),
            str(instrument.get("token") or ""),
            str(instrument.get("tradingsymbol") or "").upper(),
        ]
        for key in keys:
            if key and key in quotes:
                return dict(quotes[key] or {})
        return {}

