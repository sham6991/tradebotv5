import tempfile
import unittest

from intraday.web_routes import IntradayWebRoutes


class DummyApp:
    def __init__(self):
        self.zerodha_clients_by_mode = {"PAPER": None, "LIVE": None}
        self.account_margins = {"LIVE": {"available": None}}

    def connection_status(self, mode):
        return {"connected": bool(self.zerodha_clients_by_mode.get(mode)), "blocked": False}

    def blocking_connection_modes(self, mode):
        if mode == "LIVE" and self.zerodha_clients_by_mode.get("PAPER"):
            return ["PAPER"]
        if mode == "PAPER" and self.zerodha_clients_by_mode.get("LIVE"):
            return ["LIVE"]
        return []

    def auth_label(self, mode):
        return "Real Money" if mode == "LIVE" else "Paper Data"


class IntradayWebRoutesTests(unittest.TestCase):
    def test_route_matchers_only_claim_intraday_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            routes = IntradayWebRoutes(DummyApp(), temp_dir)
            self.assertTrue(routes.can_handle_get("/intraday"))
            self.assertTrue(routes.can_handle_get("/api/intraday/status"))
            self.assertFalse(routes.can_handle_get("/api/status"))
            self.assertTrue(routes.can_handle_post("/api/intraday/start"))
            self.assertFalse(routes.can_handle_post("/api/live/start"))

    def test_real_start_is_blocked_when_paper_data_is_connected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DummyApp()
            app.zerodha_clients_by_mode["PAPER"] = object()
            routes = IntradayWebRoutes(app, temp_dir)
            blockers = routes._mode_blockers("LIVE")
            self.assertTrue(blockers)
            self.assertIn("Paper", blockers[0])

    def test_intraday_start_requires_main_app_connection_for_requested_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DummyApp()
            routes = IntradayWebRoutes(app, temp_dir)
            blockers = routes._mode_blockers("PAPER", require_connection=True)
            self.assertTrue(blockers)
            self.assertIn("main app Connections", blockers[0])

            app.zerodha_clients_by_mode["PAPER"] = object()
            self.assertEqual(routes._mode_blockers("PAPER", require_connection=True), [])

    def test_backtest_can_use_simulated_data_without_paper_connection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DummyApp()
            routes = IntradayWebRoutes(app, temp_dir)
            self.assertEqual(routes._mode_blockers("PAPER", require_connection=False), [])

            app.zerodha_clients_by_mode["LIVE"] = object()
            blockers = routes._mode_blockers("PAPER", require_connection=False)
            self.assertTrue(blockers)
            self.assertIn("Real Money", blockers[0])

    def test_account_status_reflects_main_app_paper_connection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DummyApp()
            routes = IntradayWebRoutes(app, temp_dir)
            self.assertFalse(routes.account_status()["paper"]["connected"])

            app.zerodha_clients_by_mode["PAPER"] = object()
            self.assertTrue(routes.account_status()["paper"]["connected"])

    def test_service_provider_maps_real_and_live_to_live_client(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = DummyApp()
            app.zerodha_clients_by_mode["LIVE"] = object()
            routes = IntradayWebRoutes(app, temp_dir)

            self.assertIs(routes.service.manager.zerodha_client_provider("REAL"), app.zerodha_clients_by_mode["LIVE"])
            self.assertIs(routes.service.manager.zerodha_client_provider("LIVE"), app.zerodha_clients_by_mode["LIVE"])


if __name__ == "__main__":
    unittest.main()
