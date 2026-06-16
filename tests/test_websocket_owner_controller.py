import os
import tempfile
import unittest

from websocket_owner_controller import (
    OWNER_INTRADAY,
    OWNER_MAIN_APP,
    OWNER_OPTIONS_AUTO,
    WebSocketOwnerController,
)


class WebSocketOwnerControllerTests(unittest.TestCase):
    def test_preferred_owner_can_be_selected_before_login(self):
        controller = WebSocketOwnerController()
        state = controller.set_preferred_owner(OWNER_OPTIONS_AUTO)
        self.assertEqual(state["preferred_owner"], OWNER_OPTIONS_AUTO)
        self.assertEqual(state["active_owner"], "NONE")
        waiting = controller.get_state(zerodha_connected=False)
        self.assertTrue(waiting["zerodha_login_required"])
        self.assertIn("Zerodha login is required", waiting["blockers"][0])

    def test_websocket_activation_requires_zerodha_login(self):
        controller = WebSocketOwnerController()
        result = controller.acquire_owner(OWNER_OPTIONS_AUTO, mode="PAPER", zerodha_connected=False)
        self.assertFalse(result["allowed"])
        self.assertIn("Zerodha login is required", result["blockers"][0])
        self.assertEqual(controller.get_state()["active_owner"], "NONE")

    def test_main_app_can_acquire_owner_when_none(self):
        controller = WebSocketOwnerController()
        result = controller.acquire_owner(OWNER_MAIN_APP, mode="PAPER", ticker_name="default", tokens=[1, 2], zerodha_connected=True)
        self.assertTrue(result["acquired"])
        self.assertEqual(result["active_owner"], OWNER_MAIN_APP)
        self.assertEqual(result["active_token_count"], 2)

    def test_all_supported_owners_can_acquire_when_none(self):
        for owner in (OWNER_MAIN_APP, OWNER_OPTIONS_AUTO, OWNER_INTRADAY):
            with self.subTest(owner=owner):
                controller = WebSocketOwnerController()
                result = controller.acquire_owner(owner, mode="PAPER", zerodha_connected=True)
                self.assertTrue(result["allowed"])
                self.assertEqual(result["active_owner"], owner)

    def test_main_app_owner_blocks_options_auto_and_intraday(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_MAIN_APP, mode="PAPER", zerodha_connected=True)
        options = controller.can_start_owner(OWNER_OPTIONS_AUTO, "PAPER", True)
        intraday = controller.can_start_owner(OWNER_INTRADAY, "PAPER", True)
        self.assertFalse(options["allowed"])
        self.assertFalse(intraday["allowed"])
        self.assertIn("Main App currently owns", options["blockers"][0])

    def test_options_auto_owner_blocks_main_app_and_intraday(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_OPTIONS_AUTO, mode="PAPER", zerodha_connected=True)
        self.assertFalse(controller.can_start_owner(OWNER_MAIN_APP, "PAPER", True)["allowed"])
        self.assertFalse(controller.can_start_owner(OWNER_INTRADAY, "PAPER", True)["allowed"])

    def test_intraday_owner_blocks_main_app_and_options_auto(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_INTRADAY, mode="PAPER", zerodha_connected=True)
        self.assertFalse(controller.can_start_owner(OWNER_MAIN_APP, "PAPER", True)["allowed"])
        self.assertFalse(controller.can_start_owner(OWNER_OPTIONS_AUTO, "PAPER", True)["allowed"])

    def test_same_owner_can_reconnect_without_self_block(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_OPTIONS_AUTO, mode="PAPER", zerodha_connected=True)
        result = controller.acquire_owner(OWNER_OPTIONS_AUTO, mode="PAPER", reason="reconnect", zerodha_connected=True)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["active_owner"], OWNER_OPTIONS_AUTO)

    def test_wrong_owner_cannot_release_lock(self):
        controller = WebSocketOwnerController()
        controller.acquire_owner(OWNER_MAIN_APP, mode="PAPER", zerodha_connected=True)
        result = controller.release_owner(OWNER_OPTIONS_AUTO, "wrong owner")
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
        controller.set_preferred_owner(OWNER_OPTIONS_AUTO)
        blocked = controller.can_start_owner(OWNER_OPTIONS_AUTO, "PAPER", True)
        self.assertFalse(blocked["allowed"])
        self.assertIn("Main App currently owns", blocked["blockers"][0])


if __name__ == "__main__":
    unittest.main()
