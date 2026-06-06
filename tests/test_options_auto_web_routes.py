import unittest

from options_auto.web_routes import OptionsAutoWebRoutes


class FakeAppState:
    def __init__(self, paper_connected=True, live_connected=False):
        self.zerodha_clients_by_mode = {"PAPER": object() if paper_connected else None, "LIVE": object() if live_connected else None}
        self.zerodha_auth_profiles = {"PAPER": {"user_id": "PAPER1"} if paper_connected else None, "LIVE": {"user_id": "REAL1"} if live_connected else None}
        self.account_margins = {"PAPER": {"available": 20000} if paper_connected else {"available": None}, "LIVE": {"available": 50000} if live_connected else {"available": None}}

    def connection_status(self, mode):
        return {"connected": bool(self.zerodha_clients_by_mode.get(mode)), "label": mode}

    def blocking_connection_modes(self, _mode):
        return []

    def auth_label(self, mode):
        return "Real Money" if mode == "LIVE" else "Virtual/Paper Data"


class FakeHandler:
    def __init__(self):
        self.payload = None
        self.status = None

    def send_static_file(self, path):
        self.payload = {"static": path}
        return self.payload

    def send_json(self, payload, status=200):
        self.payload = payload
        self.status = status
        return payload


class FakeOptionsAutoService:
    def __init__(self):
        self.place_payload = None
        self.upload_payload = None

    def place_real_order(self, payload):
        self.place_payload = dict(payload)
        return {"allowed": False, "real_order_sent": False, "order_stage": "BLOCKED", "blockers": ["test blocker"]}

    def upload_fii_dii_csv(self, payload):
        self.upload_payload = dict(payload)
        return {"status": "UPLOADED", "score": 30, "file_name": payload.get("file_name") or "fii.csv"}


class OptionsAutoWebRoutesTests(unittest.TestCase):
    def test_options_auto_page_route_uses_static_page(self):
        routes = OptionsAutoWebRoutes(FakeAppState(), "results")
        handler = FakeHandler()

        result = routes.handle_get(handler, "/options-auto", None)

        self.assertEqual(result["static"], "options_auto.html")

    def test_real_place_order_route_blocks_paper_login(self):
        routes = OptionsAutoWebRoutes(FakeAppState(), "results")
        handler = FakeHandler()

        with self.assertRaisesRegex(PermissionError, "Paper mode is active"):
            routes.handle_post(handler, "/api/options-auto/real/place-order", {})

    def test_real_place_order_route_blocks_missing_real_login(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=False), "results")
        handler = FakeHandler()

        with self.assertRaisesRegex(PermissionError, "Connect Real Money Zerodha"):
            routes.handle_post(handler, "/api/options-auto/real/place-order", {})

    def test_real_place_order_route_reaches_service_when_live_connected(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=True), "results")
        routes.service = FakeOptionsAutoService()
        handler = FakeHandler()

        result = routes.handle_post(handler, "/api/options-auto/real/place-order", {"mode": "REAL"})

        self.assertEqual(result["order_stage"], "BLOCKED")
        self.assertEqual(routes.service.place_payload["kite_profile"], {"user_id": "REAL1"})
        self.assertTrue(result["account_status"]["real"]["connected"])

    def test_options_auto_service_provider_maps_real_and_live_to_live_client(self):
        app_state = FakeAppState(paper_connected=False, live_connected=True)
        live_client = app_state.zerodha_clients_by_mode["LIVE"]
        routes = OptionsAutoWebRoutes(app_state, "results")

        self.assertIs(routes.service.kite_client_provider("REAL"), live_client)
        self.assertIs(routes.service.kite_client_provider("LIVE"), live_client)

    def test_fii_dii_upload_route_updates_service_status(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=False), "results")
        routes.service = FakeOptionsAutoService()
        handler = FakeHandler()

        result = routes.handle_post(handler, "/api/options-auto/market-cue/fii-dii-upload", {"csv_text": "FII,1000\nDII,500", "file_name": "flows.csv"})

        self.assertEqual(result["status"], "UPLOADED")
        self.assertEqual(routes.service.upload_payload["file_name"], "flows.csv")
        self.assertIn("account_status", result)


if __name__ == "__main__":
    unittest.main()
