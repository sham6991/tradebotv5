import unittest

from options_auto.execution.paper_broker import PaperBroker
from options_auto.execution.paper_lifecycle import PaperLifecycleEngine
from options_auto.terminal_service import OptionsAutoTerminalService


def allowed_decision():
    return {
        "allowed": True,
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
        "spot": 22520,
        "settings": {
            "mode": "PAPER",
            "underlying": "NIFTY",
            "buy_score_threshold": 35,
            "max_capital_per_trade_pct": 100,
            "paper_starting_balance": 20000,
            "approval_timeout_seconds": 30,
        },
        "market_cue": {"phase": "LUNCH", "technical_score": 50, "option_oi_score": 25, "news_score": 1},
        "features": {"ema_alignment_score": 25, "vwap_score": 18, "rsi_slope_score": 15, "volume_score": 12, "depth_score": 8},
        "instruments": [
            {"name": "NIFTY", "tradingsymbol": "NIFTY26JUN22500CE", "instrument_token": "1", "instrument_type": "CE", "strike": 22500, "lot_size": 50},
        ],
        "quotes": {
            "1": {"ltp": 40, "bid": 39.95, "ask": 40.05, "bid_qty": 1500, "ask_qty": 1400, "volume": 90000, "oi": 950000, "momentum_score": 80},
        },
    }


class OptionsAutoPaperLifecycleTests(unittest.TestCase):
    def test_approval_expires_without_order(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        pending = lifecycle.create_pending(allowed_decision(), timeout_seconds=10, now_epoch=100)

        result = lifecycle.approve(pending["approval_id"], now_epoch=111)

        self.assertEqual(result["status"], "EXPIRED")
        self.assertEqual(lifecycle.broker.orders, [])
        self.assertIsNone(lifecycle.pending_approval)

    def test_approval_creates_protected_active_trade(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        pending = lifecycle.create_pending(allowed_decision(), timeout_seconds=10, now_epoch=100)

        result = lifecycle.approve(pending["approval_id"], now_epoch=105)

        self.assertEqual(result["status"], "APPROVED")
        self.assertTrue(result["trade"]["position_protected"])
        self.assertTrue(result["trade"]["oco_active"])
        self.assertEqual(len(lifecycle.active_trades), 1)
        self.assertEqual(len(lifecycle.broker.orders), 3)

    def test_market_target_closes_trade_and_settles_balance(self):
        lifecycle = PaperLifecycleEngine(PaperBroker(20000))
        pending = lifecycle.create_pending(allowed_decision(), timeout_seconds=10, now_epoch=100)
        lifecycle.approve(pending["approval_id"], now_epoch=105)

        result = lifecycle.process_market({"ltp": 53, "high": 53, "low": 50})

        self.assertEqual(result["updates"][0]["action"], "TARGET")
        self.assertEqual(len(lifecycle.active_trades), 0)
        self.assertEqual(len(lifecycle.closed_trades), 1)
        self.assertEqual(lifecycle.closed_trades[0]["exit_reason"], "TARGET")
        self.assertGreater(lifecycle.broker.available_balance, 20000)

    def test_service_paper_approval_approve_and_process_flow(self):
        service = OptionsAutoTerminalService("results")
        pending = service.request_paper_approval(sample_payload())

        approved = service.approve_paper({"approval_id": pending["approval"]["approval_id"]})
        processed = service.process_paper_market({"market": {"ltp": 31, "high": 35, "low": 31}})

        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(processed["updates"][0]["action"], "STOPLOSS")
        self.assertEqual(processed["session"]["status"], "PAPER_IDLE")


if __name__ == "__main__":
    unittest.main()

