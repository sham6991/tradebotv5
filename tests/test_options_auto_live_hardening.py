import json
import tempfile
import time
import unittest
from datetime import date, datetime, time as dt_time, timedelta

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
                    "source": self.label,
                }
        return rows

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
        return list(self._orders)

    def positions(self):
        return {"net": list(self._positions)}

    def place_limit_order(self, **kwargs):
        self.limit_orders.append(dict(kwargs))
        order_id = f"REAL-{len(self.limit_orders)}"
        self._orders.append({**kwargs, "order_id": order_id, "status": "OPEN"})
        return order_id

    def place_stoploss_limit_order(self, **kwargs):
        self.stoploss_orders.append(dict(kwargs))
        return f"SL-{len(self.stoploss_orders)}"

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
            with service._lock:
                warm = service._run_live_scan_cycle_locked()
            warm_cycle_calls = client.quote_calls[warm_quote_calls:]
            self.assertLessEqual(len(warm_cycle_calls), 2)
            self.assertLessEqual(sum(len(call) for call in warm_cycle_calls), 3)
            self.assertIn("NFO:" + signal["trade_plan"]["tradingsymbol"], warm["requested_quote_keys"])

            entry_price = float(signal["trade_plan"]["entry_price"])
            start = time.perf_counter()
            filled = service.process_paper_market({"market": {"ltp": entry_price, "low": entry_price, "high": entry_price + 0.2, "bid": entry_price - 0.05, "ask": entry_price + 0.05}})
            timings["paper_fill_ms"] = (time.perf_counter() - start) * 1000
            self.assertEqual(filled["updates"][0]["action"], "ENTRY_FILLED")
            self.assertEqual(service.locked_contract_manager.state, "TRADE_ACTIVE")

            trade = service.paper_lifecycle.active_trades[0]
            old_lock_id = service.locked_contract_manager.lock["lock_id"]
            target_print = float(trade["target"]) + 50.0
            start = time.perf_counter()
            exited = service.process_paper_market({"market": {"ltp": target_print, "high": target_print, "low": target_print, "bid": target_print - 0.05, "ask": target_print + 0.05}})
            timings["paper_exit_ms"] = (time.perf_counter() - start) * 1000
            self.assertEqual(exited["updates"][0]["action"], "TARGET_FILLED")
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
