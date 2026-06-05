from __future__ import annotations

from typing import Any

import pandas as pd

from options_auto.config.options_auto_defaults import normalize_settings
from options_auto.constants import MODE_BACKTEST, SIDE_CE, SIDE_PE
from options_auto.indicators.technicals import enrich_technicals
from options_auto.intelligence.decision_pipeline import evaluate_options_auto_decision
from options_auto.intelligence.entry_timing_engine import backtest_buy_limit, round_to_tick


class OptionsAutoBacktestEngine:
    """Backtest long option buying with the shared Options Auto decision pipeline."""

    def run(
        self,
        index_candles: pd.DataFrame,
        option_candles: list[pd.DataFrame] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = normalize_settings({**dict(settings or {}), "mode": MODE_BACKTEST})
        index_frame = _normalize_frame(index_candles)
        option_frames = [_prepare_option_frame(frame, index) for index, frame in enumerate(option_candles or [])]
        if index_frame.empty:
            return self._empty_result(settings, option_frames)

        decisions: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        active_trade: dict[str, Any] | None = None
        pending_entry: dict[str, Any] | None = None
        slippage = float(settings.get("slippage_buffer_points") or 0.0)

        for candle_idx, row in index_frame.iterrows():
            index = int(candle_idx)
            timestamp = row.get("datetime") or row.get("timestamp") or row.get("date") or index

            if pending_entry:
                fill = self._try_fill_pending(pending_entry, option_frames, index, settings)
                if fill.get("cancelled"):
                    decisions.append({**fill, "row": index, "datetime": str(timestamp), "decision": "ENTRY_CANCELLED"})
                    pending_entry = None
                elif fill.get("filled"):
                    active_trade = fill["trade"]
                    decisions.append({**fill["decision"], "row": index, "datetime": str(timestamp), "decision": "ENTRY"})
                    pending_entry = None

            if active_trade:
                exit_decision = self._check_exit(active_trade, option_frames, index, slippage, is_last=index == len(index_frame) - 1)
                if exit_decision.get("closed"):
                    active_trade = {**active_trade, **exit_decision}
                    trades.append(active_trade)
                    decisions.append({
                        "row": index,
                        "datetime": str(timestamp),
                        "decision": "EXIT" if exit_decision["exit_reason"] != "DAY_END" else "END_OF_DAY_EXIT",
                        "reason": exit_decision["exit_reason"],
                        "tradingsymbol": active_trade.get("tradingsymbol"),
                        "entry_price": active_trade.get("entry_price"),
                        "exit_price": exit_decision["exit_price"],
                        "gross_pnl": exit_decision["gross_pnl"],
                        "charges": exit_decision["charges"],
                        "net_pnl": exit_decision["net_pnl"],
                    })
                    active_trade = None
                continue

            if pending_entry:
                continue

            candidates, quotes = self._candidates_at(option_frames, index)
            if not candidates:
                decisions.append({
                    "row": index,
                    "datetime": str(timestamp),
                    "decision": "WAIT",
                    "reason": "No option premium candles available for this index candle.",
                })
                continue

            decision = evaluate_options_auto_decision(
                mode=MODE_BACKTEST,
                settings=settings,
                index_history=index_frame.iloc[: index + 1],
                option_candidates=candidates,
                quotes=quotes,
                market_cue_payload={
                    "phase": _phase_from_timestamp(timestamp),
                    "spot": row.get("close"),
                    "timestamp": timestamp,
                    "signal_candle": row.to_dict(),
                    "quote_age_seconds": 0,
                },
                risk_state={"trades_today": len(trades), "open_trades": 1 if active_trade else 0},
                account_state={"available_capital": settings.get("paper_starting_balance")},
                timestamp=timestamp,
            )
            if not decision.get("allowed"):
                decisions.append({
                    "row": index,
                    "datetime": str(timestamp),
                    "decision": "WAIT",
                    "reason": decision.get("explanation"),
                    "blockers": decision.get("blockers") or [],
                    "decision_snapshot": decision.get("decision_snapshot"),
                })
                continue

            selected = decision.get("selected_contract") or {}
            frame_index = int(selected.get("_frame_index", -1))
            option_frame = option_frames[frame_index] if 0 <= frame_index < len(option_frames) else pd.DataFrame()
            mode = str(settings.get("backtest_entry_mode") or "NEXT_CANDLE_OPEN_PLUS_SLIPPAGE").upper()
            if mode == "BUY_LIMIT":
                signal_row = option_frame.iloc[index]
                avg_range_10 = (option_frame["high"] - option_frame["low"]).iloc[max(0, index - 9) : index + 1].mean()
                limit_price = backtest_buy_limit(float(signal_row.get("close") or 0), float(avg_range_10 or 0), {**settings, "tick_size": selected.get("tick_size") or 0.05})
                pending_entry = {
                    "decision": decision,
                    "frame_index": frame_index,
                    "created_index": index,
                    "valid_until": index + int(settings.get("backtest_entry_validity_candles") or 1),
                    "limit_price": limit_price,
                }
                decisions.append({
                    "row": index,
                    "datetime": str(timestamp),
                    "decision": "ENTRY_PENDING",
                    "tradingsymbol": selected.get("tradingsymbol"),
                    "entry_price": limit_price,
                    "reason": "Backtest buy-limit waiting for option premium touch.",
                    "decision_snapshot": decision.get("decision_snapshot"),
                })
            else:
                fill_index = index + 1 if mode == "NEXT_CANDLE_OPEN_PLUS_SLIPPAGE" else index
                if fill_index >= len(option_frame):
                    decisions.append({"row": index, "datetime": str(timestamp), "decision": "WAIT", "reason": "No next option candle available for fill."})
                    continue
                fill_row = option_frame.iloc[fill_index]
                base_price = float(fill_row.get("open") if mode == "NEXT_CANDLE_OPEN_PLUS_SLIPPAGE" else fill_row.get("close") or 0)
                fill_price = round_to_tick(base_price + slippage, float(selected.get("tick_size") or 0.05))
                active_trade = self._open_trade(decision, frame_index, fill_index, fill_price, settings)
                decisions.append({
                    "row": fill_index,
                    "datetime": str(fill_row.get("datetime") or timestamp),
                    "decision": "ENTRY",
                    "tradingsymbol": selected.get("tradingsymbol"),
                    "entry_price": active_trade["entry_price"],
                    "target": active_trade["target"],
                    "stoploss": active_trade["stoploss"],
                    "quantity": active_trade["quantity"],
                    "reason": f"{mode} fill on option premium candle.",
                    "decision_snapshot": decision.get("decision_snapshot"),
                })

        if active_trade:
            exit_decision = self._check_exit(active_trade, option_frames, len(index_frame) - 1, slippage, is_last=True)
            active_trade = {**active_trade, **exit_decision}
            trades.append(active_trade)
            decisions.append({
                "row": len(index_frame) - 1,
                "datetime": str(index_frame.iloc[-1].get("datetime") or ""),
                "decision": "END_OF_DAY_EXIT",
                "reason": exit_decision["exit_reason"],
                "tradingsymbol": active_trade.get("tradingsymbol"),
                "gross_pnl": exit_decision["gross_pnl"],
                "charges": exit_decision["charges"],
                "net_pnl": exit_decision["net_pnl"],
            })

        return self._result(settings, index_frame, option_frames, decisions, trades)

    def _candidates_at(self, option_frames: list[pd.DataFrame], index: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        candidates: list[dict[str, Any]] = []
        quotes: dict[str, dict[str, Any]] = {}
        for frame_index, frame in enumerate(option_frames):
            if frame.empty or index >= len(frame):
                continue
            row = frame.iloc[index]
            option_type = _option_type(row)
            if option_type not in {SIDE_CE, SIDE_PE}:
                continue
            tradingsymbol = str(row.get("tradingsymbol") or f"BACKTEST{frame_index}{option_type}")
            token = str(row.get("instrument_token") or f"BT-{frame_index}")
            tick_size = _safe_float(row.get("tick_size"), 0.05) or 0.05
            close = _safe_float(row.get("close"))
            lot_size = int(_safe_float(row.get("lot_size"), 50) or 50)
            quote_proxy = _historical_quote_proxy(row, close, tick_size, lot_size)
            candidate = {
                "name": row.get("name") or row.get("underlying") or "NIFTY",
                "tradingsymbol": tradingsymbol,
                "instrument_token": token,
                "instrument_type": option_type,
                "option_type": option_type,
                "exchange": row.get("exchange") or "NFO",
                "expiry": row.get("expiry"),
                "strike": row.get("strike") or 0,
                "lot_size": lot_size,
                "tick_size": tick_size,
                "_frame_index": frame_index,
            }
            quote = {
                "ltp": close,
                "last_price": close,
                "open": row.get("open"),
                "close": close,
                "high": row.get("high"),
                "low": row.get("low"),
                "bid": quote_proxy["bid"],
                "ask": quote_proxy["ask"],
                "bid_qty": quote_proxy["bid_qty"],
                "ask_qty": quote_proxy["ask_qty"],
                "volume": quote_proxy["volume"],
                "oi": quote_proxy["oi"],
                "premium_return_1": row.get("premium_return_1"),
                "premium_return_3": row.get("premium_return_3"),
                "relative_volume": row.get("relative_volume"),
                "option_vwap": row.get("vwap"),
                "upper_wick_pct": row.get("upper_wick_pct"),
                "option_atr14": row.get("atr14"),
                "atr14": row.get("atr14"),
                "historical_quote_proxy": quote_proxy["synthetic"],
                "candle": row.to_dict(),
            }
            candidates.append(candidate)
            quotes[token] = quote
            quotes[tradingsymbol.upper()] = quote
        return candidates, quotes

    def _try_fill_pending(self, pending: dict[str, Any], option_frames: list[pd.DataFrame], index: int, settings: dict[str, Any]) -> dict[str, Any]:
        if index > int(pending["valid_until"]):
            return {"cancelled": True, "reason": "Buy limit validity expired.", "tradingsymbol": (pending["decision"].get("selected_contract") or {}).get("tradingsymbol")}
        frame = option_frames[int(pending["frame_index"])]
        if index >= len(frame):
            return {}
        row = frame.iloc[index]
        limit_price = float(pending["limit_price"])
        low = float(row.get("low") or 0)
        close = float(row.get("close") or 0)
        open_ = float(row.get("open") or 0)
        conservative = bool(settings.get("backtest_conservative_limit_fill"))
        touched = low <= limit_price
        conservative_ok = close >= limit_price and close >= open_
        if touched and (not conservative or conservative_ok):
            fill_price = limit_price
            trade = self._open_trade(pending["decision"], int(pending["frame_index"]), index, fill_price, settings)
            return {
                "filled": True,
                "trade": trade,
                "decision": {
                    "tradingsymbol": trade["tradingsymbol"],
                    "entry_price": trade["entry_price"],
                    "target": trade["target"],
                    "stoploss": trade["stoploss"],
                    "quantity": trade["quantity"],
                    "reason": "BUY_LIMIT filled on option premium candle.",
                    "decision_snapshot": pending["decision"].get("decision_snapshot"),
                },
            }
        return {}

    def _open_trade(self, decision: dict[str, Any], frame_index: int, entry_index: int, fill_price: float, settings: dict[str, Any]) -> dict[str, Any]:
        selected = decision.get("selected_contract") or {}
        plan = dict(decision.get("trade_plan") or {})
        tick = float(selected.get("tick_size") or 0.05)
        stop_distance = float(plan.get("stop_distance") or max(float(selected.get("atr14") or 0), fill_price * 0.03, 2.0))
        target_distance = float(plan.get("target_distance") or stop_distance * 1.3)
        charges = float(settings.get("estimated_total_charges") or 40.0)
        return {
            "tradingsymbol": selected.get("tradingsymbol"),
            "frame_index": frame_index,
            "entry_index": entry_index,
            "entry_price": round_to_tick(fill_price, tick),
            "stoploss": round_to_tick(fill_price - stop_distance, tick),
            "target": round_to_tick(fill_price + target_distance, tick),
            "quantity": int(plan.get("quantity") or selected.get("lot_size") or 1),
            "charges": charges,
        }

    def _check_exit(self, trade: dict[str, Any], option_frames: list[pd.DataFrame], index: int, slippage: float, is_last: bool = False) -> dict[str, Any]:
        frame = option_frames[int(trade["frame_index"])]
        if index >= len(frame):
            return {}
        row = frame.iloc[index]
        high = float(row.get("high") or row.get("close") or 0)
        low = float(row.get("low") or row.get("close") or 0)
        close = float(row.get("close") or trade["entry_price"])
        exit_reason = ""
        exit_price = 0.0
        if index == int(trade["entry_index"]) and low <= float(trade["stoploss"]):
            exit_reason = "STOPLOSS_SAME_CANDLE"
            exit_price = float(trade["stoploss"]) - slippage
        elif low <= float(trade["stoploss"]):
            exit_reason = "STOPLOSS"
            exit_price = float(trade["stoploss"]) - slippage
        elif high >= float(trade["target"]):
            exit_reason = "TARGET"
            exit_price = float(trade["target"]) - slippage
        elif is_last:
            exit_reason = "DAY_END"
            exit_price = close
        if not exit_reason:
            return {}
        gross = (exit_price - float(trade["entry_price"])) * int(trade["quantity"])
        charges = float(trade.get("charges") or 40.0)
        return {
            "closed": True,
            "exit_price": round(exit_price, 2),
            "exit_reason": exit_reason,
            "gross_pnl": round(gross, 2),
            "charges": round(charges, 2),
            "net_pnl": round(gross - charges, 2),
            "exit_index": index,
        }

    def _result(self, settings: dict[str, Any], index_frame: pd.DataFrame, option_frames: list[pd.DataFrame], decisions: list[dict[str, Any]], trades: list[dict[str, Any]]) -> dict[str, Any]:
        winning_trades = len([trade for trade in trades if trade.get("net_pnl", 0) > 0])
        losing_trades = len([trade for trade in trades if trade.get("net_pnl", 0) < 0])
        total_pnl = sum(float(trade.get("net_pnl") or 0) for trade in trades)
        total_trades = len(trades)
        return {
            "mode": MODE_BACKTEST,
            "settings": settings,
            "rows": len(index_frame),
            "option_frames": len(option_frames),
            "decisions": decisions,
            "trades": trades,
            "metrics": {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": round(winning_trades / total_trades * 100, 2) if total_trades else 0,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl_per_trade": round(total_pnl / total_trades, 2) if total_trades else 0,
            },
            "orders_placed": total_trades,
            "real_orders_placed": 0,
        }

    def _empty_result(self, settings: dict[str, Any], option_frames: list[pd.DataFrame] | None = None) -> dict[str, Any]:
        return self._result(settings, pd.DataFrame(), option_frames or [], [], [])


def _normalize_frame(frame: pd.DataFrame | list[dict[str, Any]] | None) -> pd.DataFrame:
    if isinstance(frame, pd.DataFrame):
        result = frame.copy()
    elif isinstance(frame, list):
        result = pd.DataFrame(frame)
    else:
        result = pd.DataFrame()
    if result.empty:
        return result
    result.columns = [str(column).lower() for column in result.columns]
    return result.reset_index(drop=True)


def _prepare_option_frame(frame: pd.DataFrame, index: int) -> pd.DataFrame:
    result = enrich_technicals(_normalize_frame(frame))
    if result.empty:
        return result
    if "premium_return_1" not in result:
        result["premium_return_1"] = result["close"].pct_change(1).fillna(0.0) * 100
    if "premium_return_3" not in result:
        result["premium_return_3"] = result["close"].pct_change(3).fillna(0.0) * 100
    if "instrument_token" not in result:
        result["instrument_token"] = f"BT-{index}"
    return result


def _option_type(row: pd.Series) -> str:
    explicit = str(row.get("option_type") or row.get("instrument_type") or "").upper()
    if explicit in {SIDE_CE, SIDE_PE}:
        return explicit
    symbol = str(row.get("tradingsymbol") or "").upper()
    if symbol.endswith("CE"):
        return SIDE_CE
    if symbol.endswith("PE"):
        return SIDE_PE
    return ""


def _historical_quote_proxy(row: pd.Series, close: float, tick_size: float, lot_size: int) -> dict[str, Any]:
    volume = _safe_float(row.get("volume"))
    bid = _safe_float(row.get("bid"))
    ask = _safe_float(row.get("ask"))
    synthetic = False
    if close > 0 and (bid <= 0 or ask <= 0 or ask < bid):
        bid = max(tick_size, close - tick_size)
        ask = close + tick_size
        synthetic = True
    bid_qty = _safe_float(row.get("bid_qty"))
    ask_qty = _safe_float(row.get("ask_qty"))
    if bid_qty <= 0 or ask_qty <= 0:
        depth = max(float(lot_size * 20), min(max(volume / 5.0, 500.0), 10000.0))
        bid_qty = bid_qty if bid_qty > 0 else depth
        ask_qty = ask_qty if ask_qty > 0 else depth
        synthetic = True
    oi = _safe_float(row.get("oi"))
    if oi <= 0 and volume > 0:
        oi = max(volume * 10.0, 500000.0)
        synthetic = True
    return {
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "bid_qty": int(bid_qty),
        "ask_qty": int(ask_qty),
        "volume": volume,
        "oi": oi,
        "synthetic": synthetic,
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _phase_from_timestamp(timestamp: Any) -> str:
    text = str(timestamp or "")
    if "09:" in text or "08:" in text:
        return "PREMARKET"
    if any(marker in text for marker in ("13:", "14:", "15:")):
        return "AFTERNOON"
    return "LUNCH"
