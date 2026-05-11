import unittest
from datetime import datetime

from risk_guard import LiveRiskGuard


class LiveRiskGuardTests(unittest.TestCase):
    def test_daily_loss_limit_blocks_new_entries(self):
        guard = LiveRiskGuard({"max_daily_loss": 500}, starting_balance=10000)

        blocked, reason = guard.is_blocked(9499)

        self.assertTrue(blocked)
        self.assertEqual(reason, "DAILY LOSS LIMIT HIT")

    def test_daily_profit_target_blocks_new_entries(self):
        guard = LiveRiskGuard({"max_daily_profit": 750}, starting_balance=10000)

        blocked, reason = guard.is_blocked(10750)

        self.assertTrue(blocked)
        self.assertEqual(reason, "DAILY PROFIT TARGET HIT")

    def test_consecutive_loss_limit_tracks_reset_on_win(self):
        guard = LiveRiskGuard({"max_consecutive_losses": 2}, starting_balance=10000)

        guard.record_trade_result(-10)
        self.assertFalse(guard.is_blocked(9990)[0])
        guard.record_trade_result(25)
        self.assertEqual(guard.consecutive_losses, 0)
        guard.record_trade_result(-10)
        guard.record_trade_result(-15)

        blocked, reason = guard.is_blocked(10000)

        self.assertTrue(blocked)
        self.assertEqual(reason, "CONSECUTIVE LOSS LIMIT HIT")

    def test_square_off_time_reached_blocks(self):
        guard = LiveRiskGuard(
            {"square_off_time": "15:20"},
            starting_balance=10000,
            now_provider=lambda: datetime(2026, 5, 10, 15, 21),
        )

        blocked, reason = guard.is_blocked(10000)

        self.assertTrue(blocked)
        self.assertEqual(reason, "SQUARE OFF TIME REACHED")

    def test_invalid_or_empty_square_off_time_is_ignored(self):
        guard = LiveRiskGuard(
            {"square_off_time": "bad"},
            starting_balance=10000,
            now_provider=lambda: datetime(2026, 5, 10, 15, 21),
        )

        self.assertFalse(guard.square_off_time_reached())
        self.assertFalse(guard.is_blocked(10000)[0])

    def test_kill_switch_blocks_immediately_and_preserves_reason(self):
        guard = LiveRiskGuard({}, starting_balance=10000)

        reason = guard.activate_kill_switch("manual test")
        blocked, blocked_reason = guard.is_blocked(10000)

        self.assertTrue(blocked)
        self.assertEqual(reason, "KILL SWITCH ACTIVE: manual test")
        self.assertEqual(blocked_reason, "KILL SWITCH ACTIVE: manual test")

    def test_restore_kill_switch_blocks(self):
        guard = LiveRiskGuard({}, starting_balance=10000)

        guard.restore_kill_switch(active=True, reason="restored")

        self.assertTrue(guard.is_blocked(10000)[0])
        self.assertEqual(guard.blocked_reason, "KILL SWITCH ACTIVE: restored")


if __name__ == "__main__":
    unittest.main()
