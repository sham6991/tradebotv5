import unittest

import pandas as pd

from execution_v2 import LivePaperSession


def frame():
    df = pd.DataFrame([
        {
            "datetime": "2026-05-10 09:15:00",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1,
        }
    ])
    df.attrs["instrument"] = "NIFTY25000CE"
    df.attrs["tradingsymbol"] = "NIFTY25000CE"
    df.attrs["option_type"] = "CE"
    return df


def session_with_updates(ui_update_interval=10):
    updates = []
    session = LivePaperSession(
        frame(),
        [frame(), frame()],
        {1: "NIFTY", 2: "OPTION_0", 3: "OPTION_1"},
        {
            "cooldown": 0,
            "balance": 100000,
            "lot_size": 1,
            "max_trades": 1,
            "square_off_time": "",
            "ui_update_interval": ui_update_interval,
        },
        save_path=None,
        mode="PAPER",
        on_order_update=updates.append,
    )
    return session, updates


class UiUpdateThrottleTests(unittest.TestCase):
    def test_snapshot_updates_are_throttled(self):
        session, updates = session_with_updates(ui_update_interval=10)

        session._emit_live_log_update()
        session._emit_live_log_update()
        session._emit_live_log_update()

        self.assertEqual(len(updates), 1)
        self.assertEqual(session.suppressed_ui_updates, 2)
        self.assertEqual(session.emitted_ui_updates, 1)
        self.assertEqual(updates[0]["ui_update_stats"]["interval_seconds"], 10)
        self.assertIn("health", updates[0])
        self.assertEqual(updates[0]["health"]["mode"], "PAPER")
        self.assertIn("candle_builder", updates[0]["health"])

    def test_order_events_bypass_snapshot_throttle(self):
        session, updates = session_with_updates(ui_update_interval=10)

        session._emit_live_log_update()
        session._emit_live_log_update({"Action": "BUY", "Order Status": "OPEN"})

        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[1]["order_event"], {"Action": "BUY", "Order Status": "OPEN"})
        self.assertEqual(session.emitted_ui_updates, 2)

    def test_health_snapshot_contains_live_operability_fields(self):
        session, _updates = session_with_updates(ui_update_interval=10)
        snapshot = session.health_snapshot()

        self.assertEqual(snapshot["session_id"], session.session_id)
        self.assertFalse(snapshot["open_position"])
        self.assertFalse(snapshot["pending_entry"])
        self.assertIn("invalid_ticks", snapshot["candle_builder"])
        self.assertIn("queue_size", snapshot["excel_writer"])
        self.assertIn("enabled", snapshot["store"])

    def test_force_bypasses_snapshot_throttle(self):
        session, updates = session_with_updates(ui_update_interval=10)

        session._emit_live_log_update()
        session._emit_live_log_update(force=True)

        self.assertEqual(len(updates), 2)


if __name__ == "__main__":
    unittest.main()
