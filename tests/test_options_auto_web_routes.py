import unittest

from options_auto.web_routes import OptionsAutoWebRoutes


class FakeAppState:
    def __init__(self):
        self.zerodha_clients_by_mode = {"PAPER": object(), "LIVE": None}
        self.zerodha_auth_profiles = {"PAPER": {"user_id": "PAPER1"}, "LIVE": None}
        self.account_margins = {"PAPER": {"available": 20000}, "LIVE": {"available": None}}

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


class OptionsAutoWebRoutesTests(unittest.TestCase):
    def test_options_auto_page_route_uses_static_page(self):
        routes = OptionsAutoWebRoutes(FakeAppState(), "results")
        handler = FakeHandler()

        result = routes.handle_get(handler, "/options-auto", None)

        self.assertEqual(result["static"], "options_auto.html")

    def test_real_place_order_route_is_blocked(self):
        routes = OptionsAutoWebRoutes(FakeAppState(), "results")
        handler = FakeHandler()

        with self.assertRaisesRegex(PermissionError, "disabled"):
            routes.handle_post(handler, "/api/options-auto/real/place-order", {})


if __name__ == "__main__":
    unittest.main()

