from __future__ import annotations

from typing import Any

import pandas as pd

from options_auto.backtest.backtest_broker import BacktestBroker
from options_auto.indicators.technicals import enrich_technicals, wilder_atr
from options_auto.intelligence.regime_classifier import RegimeClassifier
from options_auto.intelligence.trade_score_engine import TradeScoreEngine


class OptionsAutoBacktestEngine:
    """Enhanced backtest engine with order fill simulation.
    
    This engine:
    1. Enriches candle data with technical indicators
    2. Classifies market regime (bullish/bearish/neutral)
    3. Scores trade opportunities
    4. Simulates order fills using BacktestBroker
    5. Tracks P&L and trade metrics
    """

    def __init__(self):
        self.regime_classifier = RegimeClassifier()
        self.trade_scorer = TradeScoreEngine()
        self.broker = BacktestBroker(
            starting_balance=20000.0,
            slippage_points=0.05,
            charge_per_order=20.0
        )

    def run(
        self,
        index_candles: pd.DataFrame,
        option_candles: list[pd.DataFrame] | None = None,
        settings: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        settings = dict(settings or {})
        enriched_index = enrich_technicals(index_candles)
        
        if enriched_index.empty:
            return self._empty_result(settings, option_candles)

        decisions = []
        active_trade = None
        
        for idx, row in enriched_index.iterrows():
            candle_idx = int(idx)
            datetime_str = str(row.get("datetime", ""))
            close = float(row.get("close") or 0)
            
            # If we have an active trade, check exit conditions
            if active_trade:
                exit_decision = self._check_exit_conditions(
                    row,
                    active_trade,
                    enriched_index.iloc[:candle_idx + 1]
                )
                
                if exit_decision["should_exit"]:
                    # Simulate exit
                    exit_price = exit_decision["exit_price"]
                    gross_pnl = (exit_price - active_trade["entry_price"]) * active_trade["quantity"]
                    net_pnl = gross_pnl - (active_trade.get("charges", 40))
                    
                    decisions.append({
                        "row": candle_idx,
                        "datetime": datetime_str,
                        "close": close,
                        "decision": "EXIT",
                        "reason": exit_decision["reason"],
                        "exit_price": round(exit_price, 2),
                        "gross_pnl": round(gross_pnl, 2),
                        "net_pnl": round(net_pnl, 2),
                        "holding_candles": candle_idx - active_trade["entry_candle"],
                    })
                    
                    active_trade = None
                continue
            
            # No active trade, check for entry signal
            if candle_idx < 20:  # Need enough candles for indicators
                decisions.append({
                    "row": candle_idx,
                    "datetime": datetime_str,
                    "close": close,
                    "decision": "WAIT",
                    "reason": "Warming up indicators",
                })
                continue
            
            entry_decision = self._check_entry_signal(
                row,
                enriched_index.iloc[:candle_idx + 1],
                settings
            )
            
            if entry_decision["should_enter"]:
                entry_price = close
                target = entry_price * (1 + entry_decision["target_multiplier"] * 0.01)
                stoploss = entry_price * (1 - entry_decision["stoploss_multiplier"] * 0.01)
                quantity = int(settings.get("default_quantity", 1))
                
                active_trade = {
                    "entry_price": entry_price,
                    "target": target,
                    "stoploss": stoploss,
                    "quantity": quantity,
                    "entry_candle": candle_idx,
                    "entry_datetime": datetime_str,
                    "charges": 40,  # Entry + Exit charges
                }
                
                decisions.append({
                    "row": candle_idx,
                    "datetime": datetime_str,
                    "close": close,
                    "decision": "ENTRY",
                    "reason": entry_decision["reason"],
                    "regime": entry_decision["regime"],
                    "score": entry_decision["score"],
                    "entry_price": round(entry_price, 2),
                    "target": round(target, 2),
                    "stoploss": round(stoploss, 2),
                })
            else:
                decisions.append({
                    "row": candle_idx,
                    "datetime": datetime_str,
                    "close": close,
                    "decision": "WAIT",
                    "reason": entry_decision.get("reason", "Setup not ready"),
                })

        # Square off any remaining active trade
        if active_trade and not enriched_index.empty:
            last_row = enriched_index.iloc[-1]
            exit_price = float(last_row.get("close") or 0)
            gross_pnl = (exit_price - active_trade["entry_price"]) * active_trade["quantity"]
            net_pnl = gross_pnl - active_trade["charges"]
            
            decisions.append({
                "row": len(enriched_index) - 1,
                "datetime": str(last_row.get("datetime", "")),
                "close": exit_price,
                "decision": "END_OF_DAY_EXIT",
                "reason": "Backtest end - squaring off",
                "gross_pnl": round(gross_pnl, 2),
                "net_pnl": round(net_pnl, 2),
            })

        # Calculate metrics
        entry_decisions = [d for d in decisions if d["decision"] == "ENTRY"]
        exit_decisions = [d for d in decisions if d["decision"] in ["EXIT", "END_OF_DAY_EXIT"]]
        
        total_trades = min(len(entry_decisions), len(exit_decisions))
        winning_trades = len([d for d in exit_decisions if d.get("net_pnl", 0) > 0])
        losing_trades = len([d for d in exit_decisions if d.get("net_pnl", 0) < 0])
        total_pnl = sum(d.get("net_pnl", 0) for d in exit_decisions)

        return {
            "mode": "BACKTEST",
            "settings": settings,
            "rows": len(enriched_index),
            "option_frames": len(option_candles or []),
            "decisions": decisions,
            "metrics": {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": round(winning_trades / total_trades * 100, 2) if total_trades > 0 else 0,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl_per_trade": round(total_pnl / total_trades, 2) if total_trades > 0 else 0,
            },
            "orders_placed": total_trades,
            "real_orders_placed": 0,
        }

    def _check_entry_signal(
        self,
        current_row: pd.Series,
        history: pd.DataFrame,
        settings: dict[str, Any]
    ) -> dict[str, Any]:
        """Check if current candle meets entry criteria."""
        ema9 = float(current_row.get("ema9") or 0)
        ema20 = float(current_row.get("ema20") or 0)
        close = float(current_row.get("close") or 0)
        rsi14 = float(current_row.get("rsi14") or 50)
        
        # Basic regime detection
        if close > ema9 > ema20:
            regime = "bullish"
            recommended_side = "CE"
            score = min(100, 50 + (rsi14 - 50) * 0.5)
            target_mult = 1.5
            sl_mult = 0.8
        elif close < ema9 < ema20:
            regime = "bearish"
            recommended_side = "PE"
            score = min(100, 50 + (50 - rsi14) * 0.5)
            target_mult = 1.5
            sl_mult = 0.8
        else:
            regime = "neutral"
            recommended_side = "WAIT"
            score = 40
            target_mult = 0.0
            sl_mult = 0.0
        
        # Entry threshold
        entry_threshold = float(settings.get("entry_score_threshold", 65))
        should_enter = score >= entry_threshold and recommended_side != "WAIT"
        
        return {
            "should_enter": should_enter,
            "reason": f"Score: {score}, Regime: {regime}" if should_enter else f"Low score: {score}",
            "regime": regime,
            "score": round(score, 2),
            "target_multiplier": target_mult,
            "stoploss_multiplier": sl_mult,
        }

    def _check_exit_conditions(
        self,
        current_row: pd.Series,
        active_trade: dict[str, Any],
        history: pd.DataFrame
    ) -> dict[str, Any]:
        """Check if active trade should be exited."""
        close = float(current_row.get("close") or 0)
        high = float(current_row.get("high") or close)
        low = float(current_row.get("low") or close)
        
        # Check stoploss
        if low <= active_trade["stoploss"]:
            return {
                "should_exit": True,
                "exit_price": active_trade["stoploss"] - 0.05,
                "reason": "STOPLOSS_HIT",
            }
        
        # Check target
        if high >= active_trade["target"]:
            return {
                "should_exit": True,
                "exit_price": active_trade["target"] - 0.05,
                "reason": "TARGET_HIT",
            }
        
        # Check time-based exit (45 minute max = 9 candles for 5-min candles)
        candles_held = len(history) - active_trade["entry_candle"]
        if candles_held > 9:
            return {
                "should_exit": True,
                "exit_price": close,
                "reason": "TIME_EXIT",
            }
        
        return {"should_exit": False}

    def _empty_result(
        self,
        settings: dict[str, Any],
        option_candles: list[pd.DataFrame] | None = None
    ) -> dict[str, Any]:
        """Return empty result structure."""
        return {
            "mode": "BACKTEST",
            "settings": settings,
            "rows": 0,
            "option_frames": len(option_candles or []),
            "decisions": [],
            "metrics": {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl_per_trade": 0.0,
            },
            "orders_placed": 0,
            "real_orders_placed": 0,
        }

