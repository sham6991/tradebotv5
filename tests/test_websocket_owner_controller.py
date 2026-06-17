import os
import tempfile
import unittest

from websocket_owner_controller import (
    ALLOWED_OWNERS,
    OWNER_INTRADAY,
    OWNER_MAIN_APP,
    OWNER_NONE,
    WebSocketOwnerController,
)


class WebSocketOwnerControllerTests(unittest.TestCase):
    def test_allowed_owners_are_main_intraday_or_none_only(self):
        self.assertEqual(ALLOWED_OWNERS, {OWNER_NONE, OWNER_MAIN_APP, OWNER_INTRADAY})

    def test_preferred_owner_can_be_selected_before_login(self):
        controller = WebSocketOwnerController()
        state = controller.set_preferred_owner(OWNER_MAIN_APP)
        self.assertEqual(state["preferred_owner"], OWNER_MAIN_APP)
        self.assertEqual(state["active_owner"], "NONE")
        waiting = controller.get_state(zerodha_connected=False)
        self.assertTrue(waiting["zerodha_login_required"])
        self.assertIn("Zerodha login is required", waiting["blockers"][0])

    def test_websocket_activation_requires_zerodha_login(self):
        controller = WebSocketOwnerController()
        result = controller.acquire_owner(OWNER_MAIN_APP, mode="PAPER", zerodha_connected=False)
        self.assertFalse(result["allowed"])
        self.assertIn("Zerodha login is required", result["blockers"][0])
        self.assertEqual(controller.get_state()["active_owner"], "NONE")

    def test_main_app_can_acquire_owner_when_none(self):
        controller = WebSocketOwnerController()
        result = controller.acquire_owner(OWNER_MAIN_APP, mode="PAPER", ticker_name="default", tokens=[1, 2], zerodha_connected=True)
        self.assertTrue(result["acquired"])
        self.assertEqual(result["active_owner"], OWNER_MAIN_APP)
        self.assertEqual(result["active_token_count"], 2)

    def test_supported_owners_can_acquire_when_none(self):
        for owner in (OWNER_MAIN_APP, OWNER_INTRADAY):
            with self.subTest(owner=owner):
                controller = WebSocketOwnerController()
                result = controller.acquire_owner(owner, mode="PAPER", zerodha_connected=True)
                self.assertTrue(result["allowed"])
                self.assertEqual(result["active_owner"], owner)

    def test_main_app_owner_blocks_intraday(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_MAIN_APP, mode="PAPER", zerodha_connected=True)
        intraday = controller.can_start_owner(OWNER_INTRADAY, "PAPER", True)
        self.assertFalse(intraday["allowed"])
        self.assertIn("Main App currently owns", intraday["blockers"][0])

    def test_intraday_owner_blocks_main_app(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_INTRADAY, mode="PAPER", zerodha_connected=True)
        self.assertFalse(controller.can_start_owner(OWNER_MAIN_APP, "PAPER", True)["allowed"])

    def test_same_owner_can_reconnect_without_self_block(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_INTRADAY, mode="PAPER", zerodha_connected=True)
        result = controller.acquire_owner(OWNER_INTRADAY, mode="PAPER", reason="reconnect", zerodha_connected=True)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["active_owner"], OWNER_INTRADAY)

    def test_activation_without_tokens_is_reserved_not_active_subscription(self):
        controller = WebSocketOwnerController()
        result = controller.acquire_owner(OWNER_INTRADAY, mode="PAPER", ticker_name="intraday_paper", tokens=[], zerodha_connected=True)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["owner_status"], "RESERVED")
        self.assertEqual(result["active_token_count"], 0)
        self.assertIn("waiting for websocket subscription tokens", result["next_action"])

    def test_wrong_owner_cannot_release_lock(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_MAIN_APP, mode="PAPER", zerodha_connected=True)
        result = controller.release_owner(OWNER_INTRADAY, "wrong owner")
        self.assertFalse(result["released"])
        self.assertEqual(controller.get_state(True)["active_owner"], OWNER_MAIN_APP)

    def test_stop_active_owner_releases_lock(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_MAIN_APP, mode="PAPER", zerodha_connected=True)
        result = controller.release_owner(OWNER_MAIN_APP, "stopped")
        self.assertTrue(result["released"])
        self.assertEqual(result["active_owner"], "NONE")

    def test_stale_persisted_owner_clears_on_startup_without_ticker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "owner.json")
            first = WebSocketOwnerController(path)
            first.acquire_owner(OWNER_INTRADAY, mode="PAPER", zerodha_connected=True)
            second = WebSocketOwnerController(path, active_ticker_provider=lambda _owner: False)
            state = second.get_state(True)
            self.assertEqual(state["active_owner"], "NONE")
            self.assertIn("stale", " ".join(state["warnings"]).lower())

    def test_switch_owner_requires_stop_first(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_MAIN_APP, mode="PAPER", zerodha_connected=True)
        controller.set_preferred_owner(OWNER_INTRADAY)
        blocked = controller.can_start_owner(OWNER_INTRADAY, "PAPER", True)
        self.assertFalse(blocked["allowed"])
        self.assertIn("Main App currently owns", blocked["blockers"][0])


if __name__ == "__main__":
    unittest.main()
