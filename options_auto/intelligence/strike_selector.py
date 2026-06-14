from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.constants import SIDE_CE, SIDE_PE, SIDE_WAIT
from options_auto.indicators.option_metrics import liquidity_components, moneyness, premium_affordability_score, premium_momentum_metrics
from options_auto.indicators.technicals import bid_ask_spread_pct, market_depth_imbalance
from options_auto.intelligence.simple_ohlcv_entry import score_simple_ohlcv_entry, simple_ohlcv_entry_enabled, simple_ohlcv_threshold
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
        context = self._compatible_context({**context, "settings": settings}, side)
        side = str(side or SIDE_WAIT).upper()
        if side not in {SIDE_CE, SIDE_PE}:
            return StrikeSelection(side=SIDE_WAIT, selected=None, score=0.0, blockers=["Regime says WAIT."])
        if not instruments:
            return StrikeSelection(side=side, selected=None, score=0.0, blockers=["No option instruments available."])
        quotes = quotes or {}
        simple_mode = simple_ohlcv_entry_enabled(settings)
        threshold = simple_ohlcv_threshold(settings) if simple_mode else float(settings.get("buy_score_threshold") or 70)
        underlying = str(settings.get("underlying") or "").upper()
        available = float(settings.get("available_capital") or settings.get("paper_starting_balance") or 0)
        candidates = []
        for instrument in instruments:
            if str(instrument.get("instrument_type") or instrument.get("option_type") or "").upper() != side:
                continue
            if underlying and str(instrument.get("name") or instrument.get("underlying") or "").upper() not in {"", underlying}:
                continue
            quote = self._quote_for(instrument, quotes)
            candidate = self._candidate(instrument, quote, spot, side, available, context, settings)
            blockers = self._candidate_blockers(candidate, settings)
            candidate["blockers"] = blockers
            if not blockers:
                score = score_simple_ohlcv_entry(candidate, context, settings) if simple_mode else self.score_engine.score(candidate, context)
                candidate.update(score)
                if score.get("warnings"):
                    candidate["warnings"] = list(score.get("warnings") or [])
            else:
                candidate["score"] = 0.0
                candidate["breakdown"] = {}
            candidates.append(candidate)
        candidates.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        if not candidates:
            return StrikeSelection(side=side, selected=None, score=0.0, blockers=["No matching CE/PE contracts found."])
        best = candidates[0]
        if best.get("score", 0.0) < threshold:
            blockers = list(best.get("blockers") or [])
            blockers.append(f"Best score {best.get('score', 0):.1f} is below threshold {threshold:.1f}.")
            return StrikeSelection(side=side, selected=None, score=float(best.get("score", 0.0)), candidates=candidates[:8], blockers=list(dict.fromkeys(blockers)))
        return StrikeSelection(side=side, selected=best, score=float(best["score"]), candidates=candidates[:8])

    def _candidate(self, instrument: dict[str, Any], quote: dict[str, Any], spot: float, side: str, available: float, context: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
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
        total_depth = float(bid_qty or 0) + float(ask_qty or 0)
        liquidity = liquidity_components(volume=volume, oi=oi, spread_pct=spread_pct, bid_qty=bid_qty, ask_qty=ask_qty)
        momentum = premium_momentum_metrics({**quote, "spread_pct": spread_pct}, quote.get("candle") or {}, settings)
        affordability = premium_affordability_score(ltp or ask, available, lot_size)
        distance_points = abs(strike - float(spot))
        distance_pct = (distance_points / float(spot) * 100) if spot else 100.0
        if not momentum.get("premium_expansion_confirmed") and quote.get("momentum_score") not in ("", None):
            momentum["premium_momentum_score"] = max(float(momentum["premium_momentum_score"]), float(quote.get("momentum_score") or 0))
        theta_score = max(15.0, 100.0 - distance_pct * 20.0)
        spread_depth = max(0.0, min(100.0, 100.0 - spread_pct * 55.0))
        option_type = str(instrument.get("instrument_type") or instrument.get("option_type") or side).upper()
        return {
            **instrument,
            "exchange": instrument.get("exchange") or quote.get("exchange") or "NFO",
            "option_type": option_type,
            "strike": strike,
            "lot_size": lot_size,
            "tick_size": float(instrument.get("tick_size") or quote.get("tick_size") or 0.05),
            "ltp": ltp,
            "demo_data": bool(quote.get("demo_data") or instrument.get("demo_data")),
            "bid": bid,
            "ask": ask,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "age_seconds": quote.get("age_seconds"),
            "timestamp_epoch": quote.get("timestamp_epoch") or quote.get("last_updated_epoch"),
            "timestamp": quote.get("timestamp") or quote.get("last_trade_time") or quote.get("exchange_timestamp"),
            "exchange_timestamp": quote.get("exchange_timestamp"),
            "quote_key": quote.get("quote_key"),
            "quote_source": quote.get("quote_source") or quote.get("source"),
            "depth_present": bool(quote.get("depth_present") or (float(bid or 0) > 0 and float(ask or 0) > 0)),
            "bid_present": bool(quote.get("bid_present") or float(bid or 0) > 0),
            "ask_present": bool(quote.get("ask_present") or float(ask or 0) > 0),
            "total_depth": total_depth,
            "spread_pct": spread_pct,
            "depth_imbalance": depth,
            "volume": volume,
            "oi": oi,
            "candle": dict(quote.get("candle") or {}),
            "premium_return_1": quote.get("premium_return_1"),
            "premium_return_3": quote.get("premium_return_3"),
            "option_vwap": quote.get("option_vwap") or quote.get("vwap"),
            "liquidity_score": liquidity["score"],
            "liquidity_components": liquidity,
            "affordability_score": affordability,
            "spread_depth_score": spread_depth,
            "momentum_score": momentum["premium_momentum_score"],
            "premium_momentum_score": momentum["premium_momentum_score"],
            "premium_expansion_confirmed": momentum["premium_expansion_confirmed"],
            "premium_momentum": momentum,
            "option_atr14": quote.get("option_atr14") or quote.get("atr14") or quote.get("atr"),
            "atr14": quote.get("option_atr14") or quote.get("atr14") or quote.get("atr"),
            "relative_volume": momentum["relative_volume"],
            "theta_score": theta_score,
            "moneyness": moneyness(spot, strike, side),
            "distance_from_spot": round(distance_points, 2),
            "distance_pct": round(distance_pct, 4),
        }

    def _compatible_context(self, context: dict[str, Any], side: str) -> dict[str, Any]:
        if context.get("regime") and context.get("market_cue") and context.get("index_features"):
            return context
        bullish = side == SIDE_CE
        trend = 70.0 if bullish else -70.0 if side == SIDE_PE else 0.0
        compatible = dict(context)
        compatible.setdefault("selected_side", side)
        compatible.setdefault("regime", {
            "regime": "strong_bullish" if bullish else "strong_bearish" if side == SIDE_PE else "neutral_sideways",
            "recommended_side": side,
            "confidence": float(context.get("regime_alignment") or 80),
        })
        compatible.setdefault("market_cue", {
            "cue": "strong_bullish" if bullish else "strong_bearish" if side == SIDE_PE else "neutral_sideways",
            "recommended_side": side,
            "confidence": float(context.get("market_cue_score") or 75),
            "components": {"news": context.get("news_score", 0)},
        })
        compatible.setdefault("index_features", {
            "close": 100.0,
            "vwap": 99.0 if bullish else 101.0,
            "ema9": 102.0 if bullish else 98.0,
            "ema20": 101.0 if bullish else 99.0,
            "ema50": 100.0,
            "trend_strength_score": trend,
            "relative_volume": 1.5,
            "atr_pct": 0.25,
        })
        return compatible

    def _candidate_blockers(self, candidate: dict[str, Any], settings: dict[str, Any]) -> list[str]:
        blockers = []
        if not candidate.get("instrument_token") and not candidate.get("token"):
            blockers.append("Missing instrument token.")
        if int(candidate.get("lot_size") or 0) <= 0:
            blockers.append("Missing lot size.")
        if float(candidate.get("ltp") or 0) <= 0:
            blockers.append("Missing option LTP.")
        if float(candidate.get("bid") or 0) <= 0 or float(candidate.get("ask") or 0) <= 0:
            blockers.append("Invalid bid/ask spread.")
        if float(candidate.get("ask") or 0) < float(candidate.get("bid") or 0):
            blockers.append("Invalid bid/ask spread.")
        if int(float(candidate.get("bid_qty") or 0)) + int(float(candidate.get("ask_qty") or 0)) < int(settings.get("min_depth_qty") or 1):
            blockers.append("Depth too low.")
        if settings.get("strict_liquidity_filter") and not candidate.get("depth_present"):
            blockers.append("Market depth is missing.")
        min_volume = float(settings.get("min_volume") or 0)
        if min_volume > 0 and float(candidate.get("volume") or 0) < min_volume:
            blockers.append("Volume below configured minimum.")
        min_oi = float(settings.get("min_oi") or 0)
        if min_oi > 0 and float(candidate.get("oi") or 0) < min_oi:
            blockers.append("OI below configured minimum.")
        if settings.get("strict_liquidity_filter") and float(candidate.get("liquidity_score") or 0) < 45:
            blockers.append("Liquidity score too low.")
        if not settings.get("allow_deep_otm", False) and candidate.get("moneyness") == "OTM" and float(candidate.get("distance_pct") or 0) > 1.2:
            blockers.append("Deep OTM disabled.")
        if settings.get("premium_expansion_required") and not simple_ohlcv_entry_enabled(settings) and not candidate.get("premium_expansion_confirmed"):
            blockers.append("Option premium is not confirming index direction.")
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
