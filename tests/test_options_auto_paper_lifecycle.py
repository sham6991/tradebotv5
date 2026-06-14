import tempfile
import unittest

from options_auto.execution.paper_broker import PaperBroker
from options_auto.execution.paper_lifecycle import PaperLifecycleEngine
from options_auto.terminal_service import OptionsAutoTerminalService
from tests.test_options_auto_auto_spot import FakeOptionsZerodha


def allowed_decision():
    return {
        "allowed": True,
        "settings": {"limit_order_timeout_seconds": 30},
        "trade_plan": {
            "tradingsymbol": "NIFTY26JUN22500CE",
            "side": "CE",
            "entry_price": 40.0,
            "stoploss": 32.0,
            "target": 52.0,
            "quantity": 50,
            "lots": 1,
        },
        "selection": {"selected": {"tradingsymbol": "NIFTY26JUN22500CE"}},
    }


def sample_payload():
    return {
        "mode": "PAPER",
        "timestamp": "2026-06-04 10:00:00",
        "spot": 22520,
        "settings": {
            "mode": "PAPER",
            "underlying": "NIFTY",
            "buy_score_threshold": 35,
            "atm_scan_strike_span": 0,
            "premium_expansion_required": False,
            "max_capital_per_trade_pct": 100,
            "max_risk_per_trade_pct": 10,
            "paper_starting_balance": 20000,
            "approval_timeout_seconds": 30,
        },
        "market_cue": {"phase": "LUNCH", "technical_score": 58, "option_oi_score": 25, "news_score": 1},
        "features": {"ema_alignment_score": 25, "vwap_score": 18, "rsi_slope_score": 15, "volume_score": 12, "depth_score": 8},
        "instruments": [
            {"name": "NIFTY", "tradingsymbol": "NIFTY26JUN22500CE", "instrument_token": "1", "instrument_type": "CE", "strike": 22500, "expiry": "2026-06-25", "lot_size": 50},
        ],
        "quotes": {
            "1": {"ltp": 40, "bid": 39.95, "ask": 40.05, "bid_qty": 1500, "ask_qty": 1400, "volume": 90000, "oi": 950000, "premium_return_1": 1.2, "premium_return_3": 4.5, "relative_volume": 1.6, "option_vwap": 39, "option_atr14": 5, "momentum_score": 80},
        },
    }


class OptionsAutoPaperLifecycleTests(unittest.TestCase):
    def test_paper_ledger_opening_balance(self):
        broker = PaperBroker(20000)

        self.assertEqual(broker.ledger[0]["type"], "OPENING_BALANCE")
        self.assertEqual(broker.ledger[0]["amount"], 20000.0)
        self.assertEqual(broker.ledger[0]["balance"], 20000.0)
        self.assertEqual(broker.ledger[0]["reserved_balance"], 0.0)

    def test_paper_ledger_buy_reserved_and_filled_release(self):
        broker = PaperBroker(20000)

        order = broker.place_limit_buy("NIFTY26JUN22500CE", 10, 100)
        filled = broker.fill_limit_buy(order["order_id"], 98)
        types = [row["type"] for row in broker.ledger]

        self.assertEqual(filled["average_price"], 98.0)
        self.assertIn("BUY_RESERVED", types)
        self.assertIn("BUY_FILLED", types)
        self.assertIn("BUY_RELEASED", types)
        self.assertEqual(broker.available_balance, 19020.0)
        self.assertEqual(broker.reserved_balance, 0.0)

    def test_paper_ledger_cancel_release(self):
        broker = PaperBroker(20000)

        order = broker.place_limit_buy("NIFTY26JUN22500CE", 10, 100)
        status = broker.cancel_order(order["order_id"])
        types = [row["type"] for row in broker.ledger]

        self.assertEqual(status, "CANCELLED")
        self.assertIn("BUY_RESERVED", types)
        self.assertIn("CANCEL_RELEASE", types)
        self.assertEqual(broker.available_balance, 20000.0)
        self.assertEqual(broker.reserved_balance, 0.0)

    def test_paper_ledger_sell_exit_charges_and_invariant(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        pending = lifecycle.create_pending(allowed_decision(), timeout_seconds=10, now_epoch=100)
        lifecycle.approve(pending["approval_id"], now_epoch=105)
        lifecycle.process_market({"ltp": 39.5, "high": 41, "low": 39.5, "now_epoch": 106})
        lifecycle.process_market({"ltp": 53, "high": 53, "low": 50, "now_epoch": 107})
        snapshot = lifecycle.broker.snapshot()
        types = [row["type"] for row in snapshot["ledger"]]

        self.assertIn("SELL_EXIT", types)
        self.assertIn("ENTRY_CHARGES", types)
        self.assertIn("EXIT_CHARGES", types)
        self.assertEqual(snapshot["charges"], 40.0)
        self.assertEqual(
            snapshot["realized_pnl"],
            round(snapshot["available_balance"] + snapshot["reserved_balance"] - snapshot["opening_balance"], 2),
        )
        self.assertEqual(len(lifecycle.active_trades), 0)
        self.assertEqual(len(lifecycle.closed_trades), 1)

    def test_approval_expires_without_order(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        pending = lifecycle.create_pending(allowed_decision(), timeout_seconds=10, now_epoch=100)

        result = lifecycle.approve(pending["approval_id"], now_epoch=111)

        self.assertEqual(result["status"], "APPROVAL_EXPIRED")
        self.assertEqual(lifecycle.broker.orders, [])
        self.assertIsNone(lifecycle.pending_approval)

    def test_approval_creates_pending_entry_without_instant_fill(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        pending = lifecycle.create_pending(allowed_decision(), timeout_seconds=10, now_epoch=100)

        result = lifecycle.approve(pending["approval_id"], now_epoch=105)

        self.assertEqual(result["status"], "ENTRY_PENDING")
        self.assertIsNone(result["trade"])
        self.assertEqual(result["entry_order"]["status"], "OPEN")
        self.assertEqual(len(lifecycle.pending_entries), 1)
        self.assertEqual(len(lifecycle.active_trades), 0)
        self.assertEqual(len(lifecycle.broker.orders), 1)

    def test_pending_buy_fills_only_when_touched_and_creates_oco(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        pending = lifecycle.create_pending(allowed_decision(), timeout_seconds=10, now_epoch=100)
        lifecycle.approve(pending["approval_id"], now_epoch=105)

        untouched = lifecycle.process_market({"ltp": 41, "high": 42, "low": 40.5, "now_epoch": 106})
        self.assertEqual(untouched["updates"], [])
        self.assertEqual(len(lifecycle.pending_entries), 1)
        touched = lifecycle.process_market({"ltp": 39.5, "high": 41, "low": 39.5, "now_epoch": 107})

        self.assertEqual(touched["updates"][0]["action"], "ENTRY_FILLED")
        self.assertTrue(touched["updates"][0]["trade"]["position_protected"])
        self.assertEqual(len(lifecycle.active_trades), 1)
        self.assertEqual(len(lifecycle.broker.orders), 3)

    def test_market_target_closes_trade_and_settles_balance(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        pending = lifecycle.create_pending(allowed_decision(), timeout_seconds=10, now_epoch=100)
        lifecycle.approve(pending["approval_id"], now_epoch=105)
        lifecycle.process_market({"ltp": 39.5, "high": 41, "low": 39.5, "now_epoch": 106})

        result = lifecycle.process_market({"ltp": 53, "high": 53, "low": 50, "now_epoch": 107})

        self.assertEqual(result["updates"][0]["action"], "TARGET_FILLED")
        self.assertEqual(len(lifecycle.active_trades), 0)
        self.assertEqual(len(lifecycle.closed_trades), 1)
        self.assertEqual(lifecycle.closed_trades[0]["exit_reason"], "TARGET_FILLED")
        stop_order = [order for order in lifecycle.broker.orders if order["order_id"] == lifecycle.closed_trades[0]["stoploss_order_id"]][0]
        self.assertEqual(stop_order["status"], "CANCELLED")
        self.assertGreater(lifecycle.broker.available_balance, 20000)

    def test_service_paper_approval_approve_and_process_flow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OptionsAutoTerminalService(temp_dir, kite_client_provider=lambda _mode: FakeOptionsZerodha(spot=22520, option_price=40))
            pending = service.request_paper_approval(sample_payload())

            approved = service.approve_paper({"approval_id": pending["approval"]["approval_id"]})
            filled = service.process_paper_market({"market": {"ltp": 39.5, "high": 41, "low": 39.5}})
            processed = service.process_paper_market({"market": {"ltp": 31, "high": 35, "low": 31}})

            self.assertEqual(approved["status"], "ENTRY_PENDING")
            self.assertEqual(filled["updates"][0]["action"], "ENTRY_FILLED")
            self.assertEqual(processed["updates"][0]["action"], "SL_FILLED")
            self.assertEqual(processed["session"]["status"], "PAPER_IDLE")


if __name__ == "__main__":
    unittest.main()
