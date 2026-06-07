import tempfile
import time
import unittest
from types import SimpleNamespace

from intraday.constants import SESSION_STATUS_RUNNING, SESSION_STATUS_STOPPED
from intraday.terminal_service import IntradayTerminalService


class FakeManager:
    def __init__(self):
        self.status = SESSION_STATUS_RUNNING
        self.pending_signal = None
        self.last_signal = None
        self.lifecycle = SimpleNamespace(active_trade=None, active_trades={})
        self.calls = []

    def evaluate(self, payload):
        self.calls.append(dict(payload))
        self.status = SESSION_STATUS_STOPPED
        return {"status": SESSION_STATUS_RUNNING, "pending_signal": None}


class IntradayPerformanceNoLogicChangeTests(unittest.TestCase):
    def test_engine_interval_default_active_is_one_second(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = IntradayTerminalService(temp_dir)
            service._refresh_engine_interval_config_locked({})

            self.assertEqual(service._engine_interval({}), 1.0)

    def test_explicit_engine_interval_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = IntradayTerminalService(temp_dir)
            service._refresh_engine_interval_config_locked({})

            self.assertEqual(service._engine_interval({"engine_interval_seconds": 5}), 5.0)

    def test_pending_approval_and_active_trade_are_fast(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = IntradayTerminalService(temp_dir)
            service.manager.status = SESSION_STATUS_RUNNING
            service.manager.pending_signal = object()

            self.assertLessEqual(service._next_engine_interval_locked({}), 1.0)
            self.assertEqual(service._engine_adaptive_reason, "PENDING_APPROVAL")

            service.manager.pending_signal = None
            service.manager.lifecycle = SimpleNamespace(active_trade={"symbol": "INFY"}, active_trades={})

            self.assertLessEqual(service._next_engine_interval_locked({}), 1.0)
            self.assertEqual(service._engine_adaptive_reason, "ACTIVE_TRADE")

    def test_idle_interval_is_slower_without_changing_decision_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = IntradayTerminalService(temp_dir)
            service.manager.status = SESSION_STATUS_RUNNING
            service.manager.pending_signal = None
            service.manager.last_signal = None
            service.manager.lifecycle = SimpleNamespace(active_trade=None, active_trades={})

            self.assertEqual(service._next_engine_interval_locked({}), 3.0)
            self.assertEqual(service._engine_adaptive_reason, "IDLE")

    def test_engine_loop_calls_manager_with_same_engine_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = IntradayTerminalService(temp_dir)
            fake = FakeManager()
            service.manager = fake
            payload = {"market_trend": "Bullish", "market_phase": "OPEN", "engine_interval_seconds": 1}

            with service._lock:
                service._start_engine_locked(payload)
            for _ in range(20):
                if fake.calls:
                    break
                time.sleep(0.05)
            service._engine_stop.set()

            self.assertEqual(fake.calls[0], service._engine_payload(payload))


if __name__ == "__main__":
    unittest.main()
