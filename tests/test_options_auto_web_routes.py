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
        self.start_payload = None
        self.reset_payload = None
        self.upload_payload = None
        self.real_approve_payload = None
        self.real_reject_payload = None

    def status(self):
        return {
            "settings": {"mode": "REAL"},
            "session": {"active_trades": []},
            "options_live_feed": {"health": {"stale": False}},
            "scan_scheduler": {"running": True, "event_driven_min_scan_interval_ms": 500},
            "stale_diagnostics": {"last_tick_at": "2026-06-13T10:00:00", "reason": "healthy"},
            "contract_lock": {"lock": {"ce": {"tradingsymbol": "NIFTY26JUN23500CE"}, "pe": {"tradingsymbol": "NIFTY26JUN23400PE"}}},
            "real_order_lifecycle": {"state": "IDLE", "protected_state": "FLAT"},
            "real_safety": {"safe_mode": False},
            "paper_account": {},
        }

    def place_real_order(self, payload):
        self.place_payload = dict(payload)
        return {"allowed": False, "real_order_sent": False, "order_stage": "BLOCKED", "blockers": ["test blocker"]}

    def start_real_engine(self, payload):
        self.start_payload = dict(payload)
        return {"allowed": True, "real_engine_started": True, "real_order_sent": False}

    def approve_real_entry(self, payload):
        self.real_approve_payload = dict(payload)
        return {"allowed": True, "real_order_sent": True, "orders_sent": 1}

    def reject_real_entry(self, payload):
        self.real_reject_payload = dict(payload)
        return {"status": "REJECTED", "real_order_sent": False}

    def reset_paper_account(self, payload):
        self.reset_payload = dict(payload)
        return {"reset": True, "paper_account": {"available_balance": payload.get("paper_starting_balance")}}

    def upload_fii_dii_csv(self, payload):
        self.upload_payload = dict(payload)
        return {"status": "UPLOADED", "score": 30, "file_name": payload.get("file_name") or "fii.csv"}


class SummaryOnlyOptionsAutoService:
    def __init__(self, mode="REAL", live_scan=None, session=None, real_order_lifecycle=None):
        self.mode = mode
        self.live_scan = live_scan or {}
        self.session = session if session is not None else {"active_trades": [], "last_decision": {}}
        self.real_order_lifecycle = real_order_lifecycle or {"state": "IDLE", "protected_state": "FLAT"}

    def status(self):
        raise AssertionError("ui-summary must not call full status when lightweight summary exists")

    def ui_summary_snapshot(self):
        return {
            "settings": {"mode": self.mode},
            "session": self.session,
            "live_scan": self.live_scan,
            "scan_scheduler": {"running": bool(self.live_scan.get("running")), "event_driven_min_scan_interval_ms": 500},
            "stale_diagnostics": {"last_tick_at": "2026-06-13T10:00:00", "reason": "healthy"},
            "options_live_feed": {"health": {"stale": False}},
            "contract_lock": {"lock": {"ce": {"tradingsymbol": "NIFTY26JUN23500CE"}, "pe": {"tradingsymbol": "NIFTY26JUN23400PE"}}},
            "real_order_lifecycle": self.real_order_lifecycle,
            "real_safety": {"safe_mode": False},
            "paper_account": {},
            "latency": {"options_auto.ui_summary": {"count": 1, "p95_ms": 1.0}},
        }


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

    def test_real_start_engine_route_reaches_service_when_live_connected(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=True), "results")
        routes.service = FakeOptionsAutoService()
        handler = FakeHandler()

        result = routes.handle_post(handler, "/api/options-auto/real/start-engine", {"mode": "REAL"})

        self.assertTrue(result["real_engine_started"])
        self.assertEqual(routes.service.start_payload["kite_profile"], {"user_id": "REAL1"})
        self.assertTrue(result["account_status"]["real"]["connected"])

    def test_real_approval_routes_reach_service_when_live_connected(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=True), "results")
        routes.service = FakeOptionsAutoService()
        handler = FakeHandler()

        approved = routes.handle_post(handler, "/api/options-auto/real/approve-entry", {"approval_id": "OA-REAL-1"})
        rejected = routes.handle_post(handler, "/api/options-auto/real/reject-entry", {"approval_id": "OA-REAL-1"})

        self.assertTrue(approved["real_order_sent"])
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(routes.service.real_approve_payload["kite_profile"], {"user_id": "REAL1"})

    def test_options_auto_service_provider_maps_real_and_live_to_live_client(self):
        app_state = FakeAppState(paper_connected=False, live_connected=True)
        live_client = app_state.zerodha_clients_by_mode["LIVE"]
        routes = OptionsAutoWebRoutes(app_state, "results")

        self.assertIs(routes.service.kite_client_provider("REAL"), live_client)
        self.assertIs(routes.service.kite_client_provider("LIVE"), live_client)

    def test_paper_reset_account_route_reaches_service(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=True, live_connected=False), "results")
        routes.service = FakeOptionsAutoService()
        handler = FakeHandler()

        result = routes.handle_post(handler, "/api/options-auto/paper/reset-account", {"paper_starting_balance": 18000})

        self.assertTrue(result["reset"])
        self.assertEqual(routes.service.reset_payload["paper_starting_balance"], 18000)
        self.assertTrue(result["account_status"]["paper"]["connected"])

    def test_fii_dii_upload_route_updates_service_status(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=False), "results")
        routes.service = FakeOptionsAutoService()
        handler = FakeHandler()

        result = routes.handle_post(handler, "/api/options-auto/market-cue/fii-dii-upload", {"csv_text": "FII,1000\nDII,500", "file_name": "flows.csv"})

        self.assertEqual(result["status"], "UPLOADED")
        self.assertEqual(routes.service.upload_payload["file_name"], "flows.csv")
        self.assertIn("account_status", result)

    def test_options_auto_ui_summary_blocks_real_when_locked(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=False), "results")
        routes.service = FakeOptionsAutoService()

        result = routes.handle_get(FakeHandler(), "/api/options-auto/ui-summary", None)

        self.assertFalse(result["can_trade"])
        self.assertIn("Real money locked", result["blockers"])
        self.assertEqual(result["real_money_state"], "LOCKED")

    def test_options_auto_lifecycle_route_is_read_only(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=True), "results")
        routes.service = FakeOptionsAutoService()

        result = routes.handle_get(FakeHandler(), "/api/options-auto/lifecycle", None)

        self.assertEqual(result["state"], "IDLE")

    def test_options_auto_ui_summary_uses_lightweight_service_snapshot(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=True), "results")
        routes.service = SummaryOnlyOptionsAutoService(live_scan={"running": True, "mode": "REAL"})

        result = routes.handle_get(FakeHandler(), "/api/options-auto/ui-summary", None)

        self.assertTrue(result["can_trade"])
        self.assertTrue(result["session_started"])
        self.assertEqual(result["session_state"], "RUNNING")
        self.assertIn("latency", result)
        self.assertIn("scan_scheduler", result)
        self.assertIn("stale_diagnostics", result)
        self.assertEqual(result["scan_scheduler"]["event_driven_min_scan_interval_ms"], 500)
        self.assertEqual(result["contract_lock"]["lock"]["ce"]["tradingsymbol"], "NIFTY26JUN23500CE")

    def test_options_auto_ui_summary_connected_but_stopped_is_not_started(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=True), "results")
        routes.service = SummaryOnlyOptionsAutoService(
            mode="REAL",
            session={"active_trades": [{"tradingsymbol": "NIFTY26JUN23500CE"}], "last_decision": {}},
        )

        result = routes.handle_get(FakeHandler(), "/api/options-auto/ui-summary", None)

        self.assertFalse(result["can_trade"])
        self.assertFalse(result["session_started"])
        self.assertEqual(result["session_state"], "SESSION_NOT_STARTED")
        self.assertIn("Session not started", result["blockers"])
        self.assertEqual(result["position"], "FLAT")
        self.assertEqual(result["active_instrument"], "")

    def test_scanner_stopped_does_not_hide_broker_position(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=True), "results")
        routes.service = SummaryOnlyOptionsAutoService(
            mode="REAL",
            real_order_lifecycle={
                "state": "OCO_ACTIVE",
                "protected_state": "PROTECTIVE_EXIT_ACTIVE",
                "broker_open_positions": [{"tradingsymbol": "NIFTY26JUN23500CE", "quantity": 50}],
            },
        )

        result = routes.handle_get(FakeHandler(), "/api/options-auto/ui-summary", None)

        self.assertFalse(result["session_started"])
        self.assertEqual(result["position"], "OPEN")
        self.assertEqual(result["active_instrument"], "NIFTY26JUN23500CE")
        self.assertEqual(result["protection"], "PROTECTED")
        self.assertIn("Broker-open real position exists while scanner is stopped", result["blockers"])

    def test_options_auto_ui_summary_preserves_paper_connection_status(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=True, live_connected=False), "results")
        routes.service = SummaryOnlyOptionsAutoService(mode="PAPER")

        result = routes.handle_get(FakeHandler(), "/api/options-auto/ui-summary", None)

        self.assertEqual(result["settings"]["mode"], "PAPER")
        self.assertTrue(result["account_status"]["paper"]["connected"])
        self.assertFalse(result["account_status"]["real"]["connected"])
        self.assertEqual(result["kite"], "CONNECTED")
        self.assertNotIn("Paper data Zerodha not connected", result["blockers"])

    def test_options_auto_ui_summary_preserves_real_connection_status(self):
        routes = OptionsAutoWebRoutes(FakeAppState(paper_connected=False, live_connected=True), "results")
        routes.service = SummaryOnlyOptionsAutoService(mode="REAL")

        result = routes.handle_get(FakeHandler(), "/api/options-auto/ui-summary", None)

        self.assertEqual(result["settings"]["mode"], "REAL")
        self.assertTrue(result["account_status"]["real"]["connected"])
        self.assertFalse(result["account_status"]["paper"]["connected"])
        self.assertEqual(result["kite"], "CONNECTED")
        self.assertNotIn("Real money locked", result["blockers"])


if __name__ == "__main__":
    unittest.main()
