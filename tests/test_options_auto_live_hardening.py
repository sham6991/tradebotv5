import json
import os
import tempfile
import time
import unittest
from datetime import date, datetime, time as dt_time, timedelta

import pandas as pd

from options_auto.constants import MODE_PAPER, MODE_REAL
from options_auto.data.live_index_candles import LiveIndexCandleStore
from options_auto.execution.kite_api_manager import KiteApiManager, RateLimiter
from options_auto.execution.real_execution_controller import RealExecutionController
from options_auto.terminal_service import OptionsAutoTerminalService


def _expiry_date() -> date:
    return date.today() + timedelta(days=20)


def _expiry_text() -> str:
    return _expiry_date().isoformat()


def _strong_index_history(count=70, start_price=22380.0):
    start = datetime.combine(date.today(), dt_time(9, 15))
    rows = []
    for index in range(count):
        base = start_price + index * 3.0
        rows.append({
            "datetime": start + timedelta(minutes=index * 3),
            "open": base,
            "high": base + 18,
            "low": base - 6,
            "close": base + 15,
            "volume": 25000 + index * 600,
        })
    return rows


class StreamingOptionsZerodha:
    def __init__(self, label="PAPER"):
        self.label = label
        self.spot = 22540.0
        self.ce_price = 142.40
        self.pe_price = 118.20
        self.tick_time = datetime.combine(date.today(), dt_time(10, 20))
        self.tick_index = 0
        self.quote_calls = []
        self.historical_calls = []
        self.limit_orders = []
        self.stoploss_orders = []
        self.orders_calls = 0
        self.positions_calls = 0
        self.started_tickers = {}
        self.stopped_tickers = []
        self._orders = []
        self._positions = []
        self.margin = 250000.0
        self.options = self._option_instruments()

    def set_tick(self, *, spot=None, ce_price=None, pe_price=None, minutes=1):
        self.tick_index += 1
        self.tick_time += timedelta(minutes=minutes)
        if spot is not None:
            self.spot = float(spot)
        if ce_price is not None:
            self.ce_price = float(ce_price)
        if pe_price is not None:
            self.pe_price = float(pe_price)

    def instruments(self, exchange=None):
        if exchange == "NSE":
            return [{"tradingsymbol": "NIFTY 50", "name": "NIFTY 50", "instrument_token": 256265}]
        if exchange == "NFO":
            return list(self.options)
        return []

    def quote(self, keys):
        keys = list(keys or [])
        self.quote_calls.append(keys)
        rows = {}
        for key in keys:
            if key == "NSE:NIFTY 50":
                rows[key] = {
                    "last_price": self.spot,
                    "timestamp": self.tick_time,
                    "age_seconds": 0,
                    "volume": 200000 + self.tick_index * 1000,
                    "source": self.label,
                }
                continue
            if key.startswith("NFO:"):
                symbol = key.split(":", 1)[1]
                option_type = "CE" if symbol.endswith("CE") else "PE"
                price = self.ce_price if option_type == "CE" else self.pe_price
                rows[key] = {
                    "last_price": price,
                    "bid": round(price - 0.05, 2),
                    "ask": round(price + 0.05, 2),
                    "bid_qty": 3200,
                    "ask_qty": 3100,
                    "volume": 150000 + self.tick_index * 2000,
                    "oi": 900000,
                    "relative_volume": 1.6,
                    "premium_return_1": 1.1,
                    "premium_return_3": 3.4,
                    "option_vwap": price - 2,
                    "option_atr14": 6.0,
                    "timestamp": self.tick_time,
                    "age_seconds": 0,
                    "source": self.label,
                }
        return rows

    def start_named_ticker(self, name, instrument_tokens, on_ticks, on_connect=None, on_close=None, on_error=None, on_reconnect=None, on_noreconnect=None, on_order_update=None):
        self.started_tickers[str(name)] = {
            "tokens": list(instrument_tokens or []),
            "on_ticks": on_ticks,
            "on_connect": on_connect,
            "on_close": on_close,
            "on_error": on_error,
            "on_reconnect": on_reconnect,
            "on_noreconnect": on_noreconnect,
            "on_order_update": on_order_update,
        }
        if on_connect:
            on_connect({"connected": True})
        return {"name": name, "tokens": list(instrument_tokens or [])}

    def stop_named_ticker(self, name):
        self.stopped_tickers.append(str(name))
        self.started_tickers.pop(str(name), None)

    def emit_options_ticks(self, lock, *, spot=None, ce_price=None, pe_price=None, when=None):
        ticker = next(iter(self.started_tickers.values()))
        when = when or datetime.now()
        ce = dict((lock or {}).get("ce") or {})
        pe = dict((lock or {}).get("pe") or {})
        spot_value = float(self.spot if spot is None else spot)
        ce_value = float(self.ce_price if ce_price is None else ce_price)
        pe_value = float(self.pe_price if pe_price is None else pe_price)
        ticks = [
            {
                "instrument_token": 256265,
                "last_price": spot_value,
                "timestamp": when,
                "volume_traded": 200000 + self.tick_index * 1000,
            },
            self._option_tick(ce, ce_value, when),
            self._option_tick(pe, pe_value, when),
        ]
        ticker["on_ticks"](ticks)

    def emit_order_update(self, order):
        ticker = next(iter(self.started_tickers.values()))
        callback = ticker.get("on_order_update")
        if callback:
            callback(dict(order or {}))

    def _option_tick(self, contract, price, when):
        return {
            "instrument_token": contract.get("instrument_token"),
            "tradingsymbol": contract.get("tradingsymbol"),
            "exchange": contract.get("exchange") or "NFO",
            "last_price": price,
            "depth": {
                "buy": [{"price": round(price - 0.05, 2), "quantity": 3200}],
                "sell": [{"price": round(price + 0.05, 2), "quantity": 3100}],
            },
            "volume_traded": 150000 + self.tick_index * 2000,
            "oi": 900000,
            "timestamp": when,
        }

    def get_nifty50_token(self):
        return 256265

    def historical_candles(self, instrument_token, from_dt, to_dt, interval="3minute"):
        self.historical_calls.append((instrument_token, from_dt, to_dt, interval))
        return _strong_index_history()

    def profile(self):
        return {"user_id": "REAL1"}

    def available_margin(self):
        return self.margin

    def orders(self):
        self.orders_calls += 1
        return list(self._orders)

    def positions(self):
        self.positions_calls += 1
        return {"net": list(self._positions)}

    def place_limit_order(self, **kwargs):
        self.limit_orders.append(dict(kwargs))
        order_id = f"REAL-{len(self.limit_orders)}"
        self._orders.append({**kwargs, "order_id": order_id, "status": "OPEN"})
        return order_id

    def place_stoploss_limit_order(self, **kwargs):
        self.stoploss_orders.append(dict(kwargs))
        order_id = f"SL-{len(self.stoploss_orders)}"
        self._orders.append({**kwargs, "order_id": order_id, "status": "TRIGGER PENDING"})
        return order_id

    def _option_instruments(self):
        rows = []
        for strike in range(22300, 22901, 100):
            for option_type in ("CE", "PE"):
                rows.append({
                    "tradingsymbol": f"NIFTY26JUN{strike}{option_type}",
                    "name": "NIFTY",
                    "underlying": "NIFTY",
                    "exchange": "NFO",
                    "segment": "NFO-OPT",
                    "instrument_token": int(f"{strike}{1 if option_type == 'CE' else 2}"),
                    "instrument_type": option_type,
                    "strike": strike,
                    "expiry": _expiry_date(),
                    "lot_size": 50,
                    "tick_size": 0.05,
                })
        return rows


def live_settings(mode):
    return {
        "mode": mode,
        "underlying": "NIFTY",
        "expiry": _expiry_text(),
        "chart_interval": "3minute",
        "strategy_profile": "AGGRESSIVE",
        "entry_dependency_mode": "OHLCV_VOLUME_PROFILE",
        "buy_score_threshold": 50,
        "simple_ohlcv_score_threshold": 50,
        "simple_ohlcv_side_score_threshold": 35,
        "simple_ohlcv_min_relative_volume": 0,
        "paper_starting_balance": 250000,
        "number_of_lots": 1,
        "max_capital_per_trade_pct": 100,
        "max_risk_per_trade_pct": 10,
        "max_daily_loss": 100000,
        "max_daily_profit_lock": 100000,
        "avoid_first_minutes": 0,
        "auto_entry_enabled": True,
        "ask_permission_before_entry": False,
        "premium_expansion_required": False,
        "market_cue_alignment_required": False,
        "cooldown_after_trade_seconds": 0,
        "reselect_after_exit_cooldown": True,
        "contract_reselection_minutes": 60,
        "adaptive_scan_seconds_aggressive": 1,
        "quote_stale_seconds": 3,
        "static_ip_confirmed": mode == MODE_REAL,
        "confirm_real_mode": mode == MODE_REAL,
        "real_orders_enabled": mode == MODE_REAL,
        "real_auto_entry_enabled": mode == MODE_REAL,
        "dry_run_real_only": False if mode == MODE_REAL else True,
    }


def live_payload(mode):
    return {
        "mode": mode,
        "expiry": _expiry_text(),
        "settings": live_settings(mode),
        "timestamp": datetime.combine(date.today(), dt_time(10, 30)).isoformat(timespec="seconds"),
        "market_cue": {"phase": "LUNCH", "technical_score": 70, "option_oi_score": 20},
        "risk_state": {},
        "kite_profile": {"user_id": "REAL1"} if mode == MODE_REAL else {},
        "broker_orders": [],
        "positions": [],
        "market_open": True,
        "instruments_valid": True,
    }


class OptionsAutoLiveHardeningTests(unittest.TestCase):
    def _complete_paper_trade(self, service, *, entry=100.0, exit_price=90.0, target=120.0, stoploss=90.0, quantity=10):
        service.settings["pending_entry_dynamic_cancel_enabled"] = False
        service.settings["pending_entry_dynamic_modify_enabled"] = False
        decision = {
            "allowed": True,
            "settings": dict(service.settings),
            "trade_plan": {
                "tradingsymbol": "NIFTY26JUN22600CE",
                "side": "CE",
                "quantity": quantity,
                "lot_size": 1,
                "entry_price": entry,
                "target": target,
                "stoploss": stoploss,
            },
        }
        pending = service.paper_lifecycle.create_pending(decision, timeout_seconds=30, now_epoch=time.time())
        approved = service.paper_lifecycle.approve(pending["approval_id"], now_epoch=time.time())
        service.session.orders.append(approved["entry_order"])
        service.session.status = "PAPER_ENTRY_PENDING"
        service.process_paper_market({"market": {"ltp": entry, "high": entry, "low": entry, "now_epoch": time.time()}})
        service.process_paper_market({"market": {"ltp": exit_price, "high": exit_price, "low": exit_price, "now_epoch": time.time()}})
        return service.paper_broker.snapshot()

    def test_settings_persist_across_service_instances(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)

            service.configure({
                "mode": MODE_PAPER,
                "underlying": "SENSEX",
                "expiry": _expiry_text(),
                "paper_starting_balance": 15000,
                "buy_score_threshold": 63,
                "auto_entry_enabled": True,
            })
            restored = OptionsAutoTerminalService(temp_dir)

            self.assertEqual(restored.settings["underlying"], "SENSEX")
            self.assertEqual(restored.settings["expiry"], _expiry_text())
            self.assertEqual(restored.settings["paper_starting_balance"], 15000.0)
            self.assertEqual(restored.settings["buy_score_threshold"], 63.0)
            self.assertTrue(restored.settings["auto_entry_enabled"])

    def test_configure_does_not_reset_established_paper_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            service.reset_paper_account({"paper_starting_balance": 10000})
            service.paper_broker.available_balance = 9860
            service.paper_broker.ledger = [{"type": "SELL", "amount": 900, "balance": 9860}]
            service._persist_runtime_state_locked("paper_market_processed")

            status = service.configure({"mode": MODE_PAPER, "paper_starting_balance": 25000})
            restored = OptionsAutoTerminalService(temp_dir)

            self.assertEqual(status["settings"]["paper_starting_balance"], 25000.0)
            self.assertEqual(status["paper_account"]["opening_balance"], 10000)
            self.assertEqual(status["paper_account"]["available_balance"], 9860)
            self.assertEqual(restored.paper_broker.starting_balance, 10000)
            self.assertEqual(restored.paper_broker.available_balance, 9860)

    def test_explicit_paper_account_reset_updates_persisted_balance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            service.reset_paper_account({"paper_starting_balance": 10000})
            service.paper_broker.available_balance = 9860
            service.paper_broker.ledger = [{"type": "SELL", "amount": 900, "balance": 9860}]

            result = service.reset_paper_account({"paper_starting_balance": 18000})
            restored = OptionsAutoTerminalService(temp_dir)

            self.assertTrue(result["reset"])
            self.assertEqual(result["paper_account"]["opening_balance"], 18000)
            self.assertEqual(result["paper_account"]["available_balance"], 18000)
            self.assertEqual(restored.paper_broker.starting_balance, 18000)
            self.assertEqual(restored.paper_broker.available_balance, 18000)
            self.assertFalse(restored.paper_broker.ledger)

    def test_paper_loss_exit_persists_balance_for_next_session_and_trade(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            service.reset_paper_account({"paper_starting_balance": 10000})

            snapshot = self._complete_paper_trade(service, entry=100, exit_price=90, target=120, stoploss=90, quantity=10)
            restored = OptionsAutoTerminalService(temp_dir)
            status = restored.configure({"mode": MODE_PAPER, "paper_starting_balance": 20000})

            self.assertEqual(snapshot["available_balance"], 9860)
            self.assertEqual(snapshot["realized_pnl"], -140)
            self.assertEqual(restored.paper_broker.available_balance, 9860)
            self.assertEqual(status["paper_account"]["available_balance"], 9860)
            self.assertEqual(status["paper_account"]["opening_balance"], 10000)

    def test_paper_profit_exit_persists_balance_for_next_day(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            service.reset_paper_account({"paper_starting_balance": 10000})

            snapshot = self._complete_paper_trade(service, entry=100, exit_price=120, target=120, stoploss=90, quantity=10)
            restored = OptionsAutoTerminalService(temp_dir)

            self.assertEqual(snapshot["available_balance"], 10160)
            self.assertEqual(snapshot["realized_pnl"], 160)
            self.assertEqual(snapshot["charges"], 40)
            self.assertEqual(restored.paper_broker.available_balance, 10160)
            self.assertEqual(restored.paper_broker.snapshot()["realized_pnl"], 160)

    def test_runtime_state_restores_paper_account_with_active_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir)
            service._reset_paper_lifecycle_locked(10000, reason="test")
            service.paper_broker.available_balance = 4700
            service.paper_broker.orders = [
                {"order_id": "PAPER-E1", "tradingsymbol": "NIFTY26JUN22600CE", "transaction_type": "BUY", "status": "COMPLETE"},
                {"order_id": "PAPER-T1", "tradingsymbol": "NIFTY26JUN22600CE", "transaction_type": "SELL", "status": "OPEN"},
                {"order_id": "PAPER-S1", "tradingsymbol": "NIFTY26JUN22600CE", "transaction_type": "SELL", "status": "OPEN"},
            ]
            service.paper_lifecycle.active_trades = [{
                "trade_id": "OA-PAPER-RESTORE",
                "status": "OCO_ACTIVE",
                "tradingsymbol": "NIFTY26JUN22600CE",
                "entry_order_id": "PAPER-E1",
                "target_order_id": "PAPER-T1",
                "stoploss_order_id": "PAPER-S1",
            }]
            service.session.status = "PAPER_TRADE_ACTIVE"
            service._persist_runtime_state_locked("paper_market_processed")

            restored = OptionsAutoTerminalService(temp_dir)

            self.assertEqual(restored.paper_broker.starting_balance, 10000)
            self.assertEqual(restored.paper_broker.available_balance, 4700)
            self.assertEqual(len(restored.paper_broker.orders), 3)
            self.assertEqual(len(restored.paper_lifecycle.active_trades), 1)

    def test_incoherent_persisted_paper_lifecycle_does_not_block_new_balance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = os.path.join(temp_dir, "options_auto")
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "runtime_state.json"), "w", encoding="utf-8") as handle:
                json.dump({
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                    "reason": "stop_live_scan",
                    "session": {"status": "BACKTEST_COMPLETE", "active_trades": []},
                    "paper_lifecycle": {
                        "active_trades": [{
                            "trade_id": "OA-PAPER-STALE",
                            "status": "OCO_ACTIVE",
                            "tradingsymbol": "NIFTY26JUN22600CE",
                            "entry_order_id": "PAPER-MISSING-E1",
                            "target_order_id": "PAPER-MISSING-T1",
                            "stoploss_order_id": "PAPER-MISSING-S1",
                        }],
                        "pending_entries": [],
                        "closed_trades": [],
                        "account": {"opening_balance": 20000, "available_balance": 20000, "orders": []},
                    },
                }, handle)

            service = OptionsAutoTerminalService(temp_dir)
            status = service.configure({"mode": MODE_PAPER, "paper_starting_balance": 10000})

            self.assertFalse(service._paper_lifecycle_active())
            self.assertEqual(status["paper_account"]["opening_balance"], 10000)
            self.assertEqual(status["paper_account"]["available_balance"], 10000)
            with open(os.path.join(state_dir, "runtime_state.json"), "r", encoding="utf-8") as handle:
                persisted = json.load(handle)
            self.assertEqual(persisted["settings"]["paper_starting_balance"], 10000.0)
            self.assertEqual(persisted["paper_account"]["opening_balance"], 10000.0)
            self.assertFalse(persisted["paper_lifecycle"]["active_trades"])

    def test_paper_live_stream_builds_candles_enters_exits_and_scans_next_lock_with_timing(self):
        client = StreamingOptionsZerodha("PAPER")
        timings = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            payload = live_payload(MODE_PAPER)

            start = time.perf_counter()
            started = service.start_paper(payload)
            timings["start_ms"] = (time.perf_counter() - start) * 1000
            service._live_scan_stop.set()
            self.assertTrue(started["allowed"], started.get("blockers"))
            self.assertGreaterEqual(started["live_index_candle_count"], 3)

            client.set_tick(spot=22548, ce_price=142.40)
            start = time.perf_counter()
            with service._lock:
                signal = service._run_live_scan_cycle_locked()
            timings["buy_signal_ms"] = (time.perf_counter() - start) * 1000
            self.assertEqual(signal["live_scan_action"]["action"], "PAPER_ENTRY_PENDING")

            warm_quote_calls = len(client.quote_calls)
            client.set_tick(spot=22552, ce_price=142.35)
            start = time.perf_counter()
            with service._lock:
                warm = service._run_live_scan_cycle_locked()
            timings["paper_fill_ms"] = (time.perf_counter() - start) * 1000
            warm_cycle_calls = client.quote_calls[warm_quote_calls:]
            self.assertLessEqual(len(warm_cycle_calls), 2)
            self.assertLessEqual(sum(len(call) for call in warm_cycle_calls), 3)
            self.assertIn("NFO:" + signal["trade_plan"]["tradingsymbol"], warm["requested_quote_keys"])
            self.assertEqual(warm["live_scan_action"]["action"], "PAPER_MARKET_PROCESSED")
            self.assertTrue(any(update.get("action") == "ENTRY_FILLED" for update in warm["live_scan_action"]["paper_market_update"]["updates"]))
            self.assertEqual(service.locked_contract_manager.state, "TRADE_ACTIVE")

            trade = service.paper_lifecycle.active_trades[0]
            old_lock_id = service.locked_contract_manager.lock["lock_id"]
            target_print = float(trade["target"]) + 50.0
            client.set_tick(spot=22560, ce_price=target_print)
            start = time.perf_counter()
            with service._lock:
                exited = service._run_live_scan_cycle_locked()
            timings["paper_exit_ms"] = (time.perf_counter() - start) * 1000
            self.assertEqual(exited["live_scan_action"]["action"], "PAPER_MARKET_PROCESSED")
            self.assertTrue(any(update.get("action") == "TARGET_FILLED" for update in exited["live_scan_action"]["paper_market_update"]["updates"]))
            self.assertEqual(service.locked_contract_manager.state, "TRADE_EXITED")

            client.set_tick(spot=22620, ce_price=138.25)
            start = time.perf_counter()
            with service._lock:
                next_scan = service._run_live_scan_cycle_locked()
            timings["next_scan_ms"] = (time.perf_counter() - start) * 1000

            new_lock = service.locked_contract_manager.lock
            self.assertNotEqual(new_lock["lock_id"], old_lock_id)
            self.assertEqual(new_lock["ce"]["strike"], 22700)
            self.assertEqual(new_lock["pe"]["strike"], 22600)
            self.assertIn(next_scan["live_scan_action"]["action"], {"PAPER_ENTRY_PENDING", "HOLD"})
            service.stop_live_scan({"mode": MODE_PAPER})

        self.assertLess(timings["start_ms"], 2000)
        self.assertLess(timings["buy_signal_ms"], 1500)
        self.assertLess(timings["paper_fill_ms"], 300)
        self.assertLess(timings["paper_exit_ms"], 300)
        self.assertLess(timings["next_scan_ms"], 1500)
        print("OPTIONS_AUTO_PAPER_HARD_TIMING", json.dumps({key: round(value, 2) for key, value in timings.items()}, sort_keys=True))

    def test_start_paper_clears_previous_session_decision_before_first_live_scan(self):
        client = StreamingOptionsZerodha("PAPER")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            service.session.last_decision = {
                "mode": MODE_PAPER,
                "timestamp": "2026-06-08T09:59:00",
                "allowed": True,
                "trade_plan": {"tradingsymbol": "STALE-PAPER-TRADE"},
                "selection": {"selected": {"tradingsymbol": "STALE-PAPER-TRADE"}},
            }

            started = service.start_paper(live_payload(MODE_PAPER))
            service._live_scan_stop.set()

            self.assertEqual(started["session"]["last_decision"], {})
            self.assertEqual(service.session.last_decision, {})

    def test_paper_live_scan_processes_pending_and_active_trade_from_locked_quote(self):
        client = StreamingOptionsZerodha("PAPER")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            payload = live_payload(MODE_PAPER)
            started = service.start_paper(payload)
            service._live_scan_stop.set()
            self.assertTrue(started["allowed"], started.get("blockers"))

            client.set_tick(spot=22548, ce_price=142.40)
            with service._lock:
                signal = service._run_live_scan_cycle_locked()
            self.assertEqual(signal["live_scan_action"]["action"], "PAPER_ENTRY_PENDING")
            self.assertEqual(len(service.paper_lifecycle.pending_entries), 1)

            entry_price = float(signal["trade_plan"]["entry_price"])
            client.set_tick(spot=22550, ce_price=entry_price)
            with service._lock:
                filled = service._run_live_scan_cycle_locked()
            self.assertEqual(filled["live_scan_action"]["action"], "PAPER_MARKET_PROCESSED")
            self.assertEqual(len(service.paper_lifecycle.pending_entries), 0)
            self.assertEqual(len(service.paper_lifecycle.active_trades), 1)

            target = float(service.paper_lifecycle.active_trades[0]["target"]) + 1.0
            client.set_tick(spot=22555, ce_price=target)
            with service._lock:
                closed = service._run_live_scan_cycle_locked()
            self.assertEqual(closed["live_scan_action"]["action"], "PAPER_MARKET_PROCESSED")
            self.assertTrue(closed["live_scan_action"]["paper_market_update"]["closed"])
            self.assertEqual(len(service.paper_lifecycle.active_trades), 0)
            self.assertTrue(service.paper_lifecycle.closed_trades)

    def test_paper_live_scan_prefers_websocket_ticks_over_quote_polling_when_warm(self):
        client = StreamingOptionsZerodha("PAPER")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            payload = live_payload(MODE_PAPER)
            started = service.start_paper(payload)
            service._live_scan_stop.set()
            self.assertTrue(started["allowed"], started.get("blockers"))
            self.assertIn("options_auto_paper", client.started_tickers)

            lock = service.locked_contract_manager.lock
            base_time = datetime.now()
            for index in range(4):
                client.emit_options_ticks(
                    lock,
                    spot=22548 + index,
                    ce_price=142.40 + index,
                    pe_price=118.20 - index,
                    when=base_time + timedelta(minutes=index * 3),
                )

            client.quote_calls.clear()
            with service._lock:
                result = service._run_live_scan_cycle_locked()

            self.assertEqual(client.quote_calls, [])
            self.assertIn(result["quote_source"], {"zerodha_websocket_tick", "zerodha_websocket_tick+snapshot_fallback"})
            self.assertEqual(service.status()["options_live_feed"]["health"]["data_mode"], "WEBSOCKET_TICKS")
            service.stop_live_scan({"mode": MODE_PAPER})

    def test_event_driven_ticks_persist_runtime_and_expose_budget_status(self):
        client = StreamingOptionsZerodha("PAPER")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "PAPER" else None)
            payload = live_payload(MODE_PAPER)
            payload["settings"] = {
                **payload["settings"],
                "adaptive_scan_seconds_aggressive": 30,
                "event_driven_min_scan_interval_ms": 1,
            }
            started = service.start_paper(payload)
            self.assertTrue(started["allowed"], started.get("blockers"))

            client.emit_options_ticks(service.locked_contract_manager.lock, when=datetime.now())
            status = service.status()
            event_names = [event.get("name") for event in status["performance"]["events"]]

            self.assertIn("event_driven_scan_wake", event_names)
            self.assertTrue(status["reference_cache"]["warmed"])
            self.assertTrue(os.path.exists(status["runtime_persistence"]["path"]))
            self.assertIn("websocket_connected", status["api_budget"])
            self.assertEqual(status["scan_scheduler"]["event_driven_min_scan_interval_ms"], 1)
            self.assertIn("last_tick_at", status["stale_diagnostics"])
            service.stop_live_scan({"mode": MODE_PAPER})

    def test_incremental_index_feature_cache_reuses_same_live_frame(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: None)
            history = pd.DataFrame(_strong_index_history())

            first = service._cached_index_features(history, MODE_PAPER)
            second = service._cached_index_features(history, MODE_PAPER)
            status = service.status()

            self.assertTrue(first)
            self.assertEqual(first, second)
            self.assertEqual(status["feature_cache"]["misses"], 1)
            self.assertEqual(status["feature_cache"]["hits"], 1)
            self.assertEqual(status["performance"]["summary"]["index_feature_build"]["count"], 1)

    def test_live_tick_candle_builder_stays_fast_under_stream_load(self):
        store = LiveIndexCandleStore(max_candles=120)
        started = time.perf_counter()
        tick_time = datetime.combine(date.today(), dt_time(10, 0))
        for index in range(250):
            result = store.update(
                client=None,
                instrument_token=256265,
                underlying="NIFTY",
                mode=MODE_REAL,
                interval="3minute",
                spot=22500 + index * 0.25,
                timestamp=tick_time + timedelta(seconds=index),
                volume=100000 + index,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        avg_ms = elapsed_ms / 250

        self.assertGreaterEqual(result["candle_count"], 2)
        self.assertLess(avg_ms, 5.0)
        print("OPTIONS_AUTO_CANDLE_BUILDER_TIMING", json.dumps({"ticks": 250, "avg_ms": round(avg_ms, 4), "total_ms": round(elapsed_ms, 2)}, sort_keys=True))

    def test_real_live_stream_sends_zerodha_compliant_buy_limit_and_waits_for_fill_before_protection(self):
        client = StreamingOptionsZerodha("REAL")
        timings = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = live_payload(MODE_REAL)

            start = time.perf_counter()
            result = service.place_real_order(payload)
            timings["real_order_ms"] = (time.perf_counter() - start) * 1000
            service._live_scan_stop.set()

            self.assertTrue(result["real_order_sent"], result.get("blockers"))
            self.assertEqual(result["order_stage"], "ENTRY_ORDER_OPEN")
            self.assertEqual(len(client.limit_orders), 1)
            self.assertEqual(client.stoploss_orders, [])

            order = client.limit_orders[0]
            self.assertEqual(order["transaction_type"], "BUY")
            self.assertEqual(order["tradingsymbol"], result["trade_plan"]["tradingsymbol"])
            self.assertEqual(order["exchange"], "NFO")
            self.assertEqual(order["product"], "NRML")
            self.assertEqual(order["variety"], "regular")
            self.assertEqual(order["validity"], "DAY")
            self.assertEqual(order["tag"], "OPTIONS_AUTO")
            self.assertEqual(order["quantity"], result["trade_plan"]["quantity"])
            self.assertEqual(result["entry_order"]["status"], "OPEN")

            protection = RealExecutionController().protection_orders_from_fill(
                result["trade_plan"],
                {"average_price": result["entry_order"]["price"], "filled_quantity": result["entry_order"]["quantity"]},
                service.settings,
            )
            self.assertEqual(protection["target_order"]["transaction_type"], "SELL")
            self.assertEqual(protection["target_order"]["order_type"], "LIMIT")
            self.assertEqual(protection["stoploss_order"]["transaction_type"], "SELL")
            self.assertEqual(protection["stoploss_order"]["order_type"], "SL")
            service.stop_live_scan({"mode": MODE_REAL})

        self.assertLess(timings["real_order_ms"], 2500)
        print("OPTIONS_AUTO_REAL_HARD_TIMING", json.dumps({key: round(value, 2) for key, value in timings.items()}, sort_keys=True))

    def test_start_real_engine_scans_first_then_places_real_buy_on_valid_setup(self):
        client = StreamingOptionsZerodha("REAL")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = live_payload(MODE_REAL)

            started = service.start_real_engine(payload)
            self.assertTrue(started["real_engine_started"], started.get("blockers"))
            self.assertEqual(len(client.limit_orders), 0)

            with service._lock:
                cycle = service._run_live_scan_cycle_locked()
            service.stop_live_scan({"mode": MODE_REAL})

        action = cycle["live_scan_action"]
        self.assertEqual(action["action"], "REAL_ENTRY_ORDER_SENT")
        self.assertEqual(action["orders_sent"], 1)
        self.assertEqual(len(client.limit_orders), 1)
        self.assertEqual(client.limit_orders[0]["transaction_type"], "BUY")
        self.assertEqual(service.session.status, "REAL_STOPPED")

    def test_real_live_scan_clears_stale_lifecycle_when_broker_is_flat(self):
        client = StreamingOptionsZerodha("REAL")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = live_payload(MODE_REAL)
            service.real_lifecycle.submit_entry(
                {
                    "order_id": "STALE-REAL-ENTRY",
                    "tradingsymbol": "NIFTY26JUN22500CE",
                    "exchange": "NFO",
                    "transaction_type": "BUY",
                    "quantity": 50,
                    "price": 100,
                    "status": "OPEN",
                },
                {"tradingsymbol": "NIFTY26JUN22500CE", "quantity": 50, "entry_price": 100},
                live_settings(MODE_REAL),
            )
            service.session.status = "REAL_ENTRY_ORDER_OPEN"

            started = service.start_real_engine(payload)
            self.assertTrue(started["real_engine_started"], started.get("blockers"))
            with service._lock:
                cycle = service._run_live_scan_cycle_locked()
            service.stop_live_scan({"mode": MODE_REAL})

        self.assertEqual(cycle["live_scan_action"]["action"], "REAL_ENTRY_ORDER_SENT")
        self.assertEqual(len(client.limit_orders), 1)
        self.assertNotEqual(client.limit_orders[0].get("order_id"), "STALE-REAL-ENTRY")

    def test_real_live_scan_does_not_clear_lifecycle_when_broker_order_is_open(self):
        client = StreamingOptionsZerodha("REAL")
        open_order = {
            "order_id": "LIVE-REAL-ENTRY",
            "tradingsymbol": "NIFTY26JUN22500CE",
            "exchange": "NFO",
            "transaction_type": "BUY",
            "quantity": 50,
            "price": 100,
            "status": "OPEN",
        }
        client._orders = [dict(open_order)]
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = live_payload(MODE_REAL)
            service.real_lifecycle.submit_entry(open_order, {"tradingsymbol": open_order["tradingsymbol"], "quantity": 50, "entry_price": 100}, live_settings(MODE_REAL))
            service.session.status = "REAL_ENTRY_ORDER_OPEN"

            started = service.start_real_engine(payload)
            self.assertTrue(started["real_engine_started"], started.get("blockers"))
            with service._lock:
                cycle = service._run_live_scan_cycle_locked()
            service.stop_live_scan({"mode": MODE_REAL})

        self.assertEqual(cycle["live_scan_action"]["action"], "HOLD")
        self.assertEqual(cycle["live_scan_action"]["reason"], "Real entry lifecycle is already active. New real entries are blocked.")
        self.assertEqual(len(client.limit_orders), 0)

    def test_real_websocket_fill_places_target_and_stoploss_from_actual_fill(self):
        client = StreamingOptionsZerodha("REAL")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = live_payload(MODE_REAL)
            result = service.place_real_order(payload)
            self.assertTrue(result["real_order_sent"], result.get("blockers"))

            entry_order = result["entry_order"]
            actual_fill = round(float(entry_order["price"]) + 2.0, 2)
            fill_update = {
                **entry_order,
                "status": "COMPLETE",
                "filled_quantity": entry_order["quantity"],
                "average_price": actual_fill,
            }
            client.emit_order_update(fill_update)
            lifecycle = service.real_lifecycle.snapshot()

            self.assertEqual(lifecycle["fill"]["average_price"], actual_fill)
            self.assertEqual(lifecycle["fill"]["filled_quantity"], entry_order["quantity"])
            self.assertEqual(lifecycle["state"], "PROTECTION_PENDING")
            self.assertEqual(lifecycle["protected_state"], "PROTECTIVE_EXIT_PLACING")
            self.assertEqual(len(client.limit_orders), 2)
            self.assertEqual(len(client.stoploss_orders), 1)
            target_order = client.limit_orders[-1]
            stoploss_order = client.stoploss_orders[0]
            self.assertEqual(target_order["transaction_type"], "SELL")
            self.assertEqual(stoploss_order["transaction_type"], "SELL")
            self.assertEqual(target_order["quantity"], entry_order["quantity"])
            self.assertEqual(stoploss_order["quantity"], entry_order["quantity"])
            self.assertGreater(target_order["price"], actual_fill)
            self.assertLess(stoploss_order["trigger_price"], actual_fill)
            confirmed = service.real_lifecycle_poll({"positions": []})["real_order_lifecycle"]
            self.assertEqual(confirmed["state"], "OCO_ACTIVE")
            self.assertEqual(confirmed["protected_state"], "PROTECTIVE_EXIT_ACTIVE")
            service.stop_live_scan({"mode": MODE_REAL})

    def test_real_live_broker_snapshot_is_reused_for_sync_and_lifecycle(self):
        client = StreamingOptionsZerodha("REAL")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = live_payload(MODE_REAL)
            service.place_real_order(payload)
            service._live_scan_stop.set()
            client.orders_calls = 0
            client.positions_calls = 0

            with service._lock:
                broker_payload = service._real_live_broker_payload_locked({key: value for key, value in payload.items() if key not in {"broker_orders", "positions"}})
                service._sync_real_option_positions_locked(broker_payload)
                service.real_lifecycle_poll(broker_payload)

        self.assertEqual(client.orders_calls, 1)
        self.assertEqual(client.positions_calls, 1)

    def test_real_live_broker_payload_merges_websocket_order_updates_and_throttles_reconciliation(self):
        client = StreamingOptionsZerodha("REAL")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = live_payload(MODE_REAL)
            result = service.place_real_order(payload)
            service._live_scan_stop.set()
            self.assertTrue(result["real_order_sent"], result.get("blockers"))

            client.orders_calls = 0
            client.positions_calls = 0
            update = {
                **result["entry_order"],
                "status": "COMPLETE",
                "filled_quantity": result["entry_order"]["quantity"],
                "average_price": result["entry_order"]["price"],
            }
            client.emit_order_update(update)
            clean_payload = {key: value for key, value in payload.items() if key not in {"broker_orders", "positions"}}

            with service._lock:
                first = service._real_live_broker_payload_locked(clean_payload)
                second = service._real_live_broker_payload_locked(clean_payload)

            self.assertEqual(client.orders_calls, 1)
            self.assertEqual(client.positions_calls, 1)
            self.assertGreaterEqual(len(first["broker_orders"]), 1)
            entry_snapshot = next(order for order in second["broker_orders"] if order.get("order_id") == result["entry_order"]["order_id"])
            self.assertEqual(entry_snapshot["status"], "COMPLETE")
            self.assertEqual(entry_snapshot["source"], "zerodha_websocket_order_update")

    def test_real_live_broker_payload_prefers_newer_websocket_order_update(self):
        client = StreamingOptionsZerodha("REAL")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = live_payload(MODE_REAL)
            result = service.place_real_order(payload)
            service._live_scan_stop.set()
            entry = result["entry_order"]
            client._orders = [{
                **entry,
                "status": "OPEN",
                "filled_quantity": 0,
                "exchange_update_timestamp": "2026-06-07T10:00:00",
            }]
            client.emit_order_update({
                **entry,
                "status": "COMPLETE",
                "filled_quantity": entry["quantity"],
                "average_price": entry["price"],
                "exchange_update_timestamp": "2026-06-07T10:00:05",
            })

            with service._lock:
                merged = service._real_live_broker_payload_locked({key: value for key, value in payload.items() if key not in {"broker_orders", "positions"}})

            entry_snapshot = next(order for order in merged["broker_orders"] if order.get("order_id") == entry["order_id"])
            self.assertEqual(entry_snapshot["status"], "COMPLETE")
            self.assertEqual(entry_snapshot["source"], "zerodha_websocket_order_update")

    def test_real_live_broker_payload_keeps_newer_polling_order_update(self):
        client = StreamingOptionsZerodha("REAL")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda mode: client if str(mode).upper() == "LIVE" else None)
            payload = live_payload(MODE_REAL)
            result = service.place_real_order(payload)
            service._live_scan_stop.set()
            entry = result["entry_order"]
            client._orders = [{
                **entry,
                "status": "OPEN",
                "filled_quantity": 0,
                "exchange_update_timestamp": "2026-06-07T10:00:05",
            }]
            client.emit_order_update({
                **entry,
                "status": "COMPLETE",
                "filled_quantity": entry["quantity"],
                "average_price": entry["price"],
                "exchange_update_timestamp": "2026-06-07T10:00:00",
            })

            with service._lock:
                merged = service._real_live_broker_payload_locked({key: value for key, value in payload.items() if key not in {"broker_orders", "positions"}})

            entry_snapshot = next(order for order in merged["broker_orders"] if order.get("order_id") == entry["order_id"])
            self.assertEqual(entry_snapshot["status"], "OPEN")

    def test_kite_api_manager_blocks_bursts_before_zerodha_limit_errors(self):
        api = KiteApiManager(limiter=RateLimiter(max_calls=3, per_seconds=1.0))

        first = [api.call(f"call_{index}", lambda index=index: index, priority="QUOTE") for index in range(3)]
        fourth = api.call("call_4", lambda: 4, priority="QUOTE")

        self.assertTrue(all(item["ok"] for item in first))
        self.assertFalse(fourth["ok"])
        self.assertEqual(fourth["error_category"], "rate_limit")
        self.assertEqual(api.health()["recent_failures"], 1)


if __name__ == "__main__":
    unittest.main()
