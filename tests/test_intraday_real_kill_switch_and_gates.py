import tempfile
import unittest
from datetime import datetime, timedelta

from intraday.session_manager import IntradaySessionManager


SYMBOLS = ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"]


class FakeKite:
    def __init__(self):
        self.order_rows = [
            {
                "order_id": "OPEN1",
                "tradingsymbol": "INFY",
                "exchange": "NSE",
                "product": "MIS",
                "status": "OPEN",
                "transaction_type": "BUY",
                "quantity": 10,
            }
        ]
        self.position_rows = [
            {
                "tradingsymbol": "INFY",
                "exchange": "NSE",
                "product": "MIS",
                "quantity": 10,
            }
        ]
        self.placed = []

    def place_order(self, **params):
        self.placed.append(dict(params))
        if params.get("tradingsymbol") == "INFY" and params.get("order_type") == "MARKET":
            self.position_rows[0]["quantity"] = 0
            self.order_rows.append({
                "order_id": "EMG1",
                "tradingsymbol": "INFY",
                "exchange": "NSE",
                "product": "MIS",
                "status": "COMPLETE",
                "transaction_type": params.get("transaction_type"),
                "quantity": params.get("quantity"),
            })
        return "EMG1"

    def positions(self):
        return {"net": list(self.position_rows), "day": []}

    def trades(self):
        return []

    def order_history(self, order_id):
        return [row for row in self.order_rows if row.get("order_id") == order_id]

    def order_margins(self, _orders):
        return [{"total": 1}]


class FakeZerodhaClient:
    def __init__(self):
        self.kite = FakeKite()
        self.cancelled = []

    def profile(self):
        return {"user_name": "Test"}

    def available_margin(self):
        return 100000

    def instruments(self, _exchange=None):
        return [
            {
                "exchange": "NSE",
                "tradingsymbol": symbol,
                "name": symbol,
                "tick_size": 0.05,
                "segment": "NSE",
                "mis_allowed": 1,
            }
            for symbol in SYMBOLS
        ]

    def orders(self):
        return list(self.kite.order_rows)

    def cancel_order(self, order_id, variety="regular"):
        self.cancelled.append({"order_id": order_id, "variety": variety})
        for row in self.kite.order_rows:
            if row.get("order_id") == order_id:
                row["status"] = "CANCELLED"
        return {"order_id": order_id, "status": "CANCELLED"}


def upload_fii_dii(manager):
    return manager.upload_fii_dii_csv({
        "csv_text": "Date,Category,Buy Value,Sell Value,Net Value\n2026-06-02,FII/FPI,1000,1300,-300\n2026-06-02,DII,1400,900,500\n"
    })


def payload(**overrides):
    row = {
        "mode": "PAPER",
        "stocks": SYMBOLS,
        "minimum_entry_score": 1,
        "minimum_risk_reward": 1.1,
        "ask_permission_before_entry": False,
        "max_quantity_per_trade": 1,
    }
    row.update(overrides)
    return row


def market_data():
    data = {}
    for offset, symbol in enumerate(SYMBOLS):
        candles = []
        for index in range(35):
            base = 100 + offset * 20 + index
            candles.append({
                "timestamp": (datetime.now() - timedelta(minutes=35 - index)).isoformat(timespec="seconds"),
                "open": base - 0.2,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + 0.4,
                "volume": 90000 if index == 34 else 10000 + index * 1000,
            })
        data[symbol] = {
            "ltp": candles[-1]["close"],
            "candles": candles,
            "depth": {
                "buy": [{"price": candles[-1]["close"] - 0.05, "quantity": 25000}],
                "sell": [{"price": candles[-1]["close"] + 0.05, "quantity": 22000}],
            },
        }
    return data


class IntradayRealKillSwitchAndGateTests(unittest.TestCase):
    def test_real_kill_switch_cancels_orders_squares_off_and_verifies_flat(self):
        fake_client = FakeZerodhaClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir, zerodha_client_provider=lambda _mode: fake_client)
            upload_fii_dii(manager)
            manager.start_session(payload(mode="REAL", confirm_real_mode=True))
            killed = manager.kill_switch()
            report = killed["kill_switch_report"]
            self.assertEqual(killed["status"], "KILLED")
            self.assertTrue(report["attempted"])
            self.assertTrue(report["flat_verified"])
            self.assertEqual(fake_client.cancelled[0]["order_id"], "OPEN1")
            self.assertEqual(fake_client.kite.placed[0]["order_type"], "MARKET")
            self.assertEqual(fake_client.kite.placed[0]["transaction_type"], "SELL")

    def test_event_blackout_blocks_new_intraday_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            upload_fii_dii(manager)
            manager.start_session(payload(event_blackout_windows=[{
                "start": "09:00",
                "end": "23:59",
                "reason": "RBI policy event",
            }]))
            status = manager.evaluate({
                "market_data": market_data(),
                "market_trend": "Bullish",
                "current_time": "2026-06-03T10:00:00",
            })
            self.assertIn("Event blackout active", "; ".join(status["last_signal"]["blockers"]))
            self.assertFalse(status["last_signal"]["score_breakdown"]["hard_gates"]["trade_allowed"])

    def test_spread_eligibility_gate_blocks_entry_across_modes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = IntradaySessionManager(temp_dir)
            upload_fii_dii(manager)
            manager.start_session(payload(max_allowed_spread_pct=0.000001))
            status = manager.evaluate({"market_data": market_data(), "market_trend": "Bullish"})
            self.assertIn("Spread", "; ".join(status["last_signal"]["blockers"]))
            self.assertFalse(status["last_signal"]["score_breakdown"]["hard_gates"]["trade_allowed"])


if __name__ == "__main__":
    unittest.main()
