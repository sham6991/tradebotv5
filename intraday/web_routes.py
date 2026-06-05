from __future__ import annotations

from .constants import MODE_REAL, SESSION_STATUS_RUNNING
from .terminal_service import IntradayTerminalService


class IntradayWebRoutes:
    def __init__(self, app_state, base_result_folder: str):
        self.app_state = app_state
        self.service = IntradayTerminalService(
            base_result_folder,
            zerodha_client_provider=lambda mode: app_state.zerodha_clients_by_mode.get("LIVE" if str(mode).upper() == "REAL" else "PAPER"),
        )

    def can_handle_get(self, path: str) -> bool:
        return path == "/intraday" or path.startswith("/api/intraday")

    def can_handle_post(self, path: str) -> bool:
        return path.startswith("/api/intraday")

    def handle_get(self, handler, path: str, _parsed):
        if path == "/intraday":
            return handler.send_static_file("intraday.html")
        if path == "/api/intraday/defaults":
            return handler.send_json(self.service.defaults())
        if path == "/api/intraday/status":
            return handler.send_json(self.service.status())
        if path == "/api/intraday/account-status":
            return handler.send_json(self.account_status())
        return handler.send_json({"error": "Intraday route not found"}, status=404)

    def handle_post(self, handler, path: str, payload: dict):
        if path == "/api/intraday/paper-account":
            blockers = self._mode_blockers("PAPER")
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self.service.update_paper_account(payload))
        if path == "/api/intraday/upload-fii-dii":
            return handler.send_json(self.service.upload_fii_dii(payload))
        if path == "/api/intraday/start":
            require_connection = not (self._requested_app_mode(payload) == "PAPER" and _bool((payload or {}).get("allow_simulated_fallback")))
            blockers = self._mode_blockers(self._requested_app_mode(payload), require_connection=require_connection)
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self.service.start(payload))
        if path == "/api/intraday/evaluate":
            return handler.send_json(self.service.evaluate(payload))
        if path == "/api/intraday/process-orders":
            return handler.send_json(self.service.process_orders(payload))
        if path == "/api/intraday/paper-backtest":
            blockers = self._mode_blockers("PAPER", require_connection=False)
            if blockers:
                raise ValueError(blockers[0])
            return handler.send_json(self.service.paper_backtest(payload))
        if path == "/api/intraday/approve":
            return handler.send_json(self.service.approve(payload))
        if path == "/api/intraday/reject":
            return handler.send_json(self.service.reject(payload))
        if path == "/api/intraday/kill-switch":
            return handler.send_json(self.service.kill_switch())
        if path == "/api/intraday/stop":
            return handler.send_json(self.service.stop())
        return handler.send_json({"error": "Intraday route not found"}, status=404)

    def account_status(self) -> dict:
        paper_connection = self.app_state.connection_status("PAPER")
        live_connection = self.app_state.connection_status("LIVE")
        return {
            "mode_lock": self._mode_lock(paper_connection, live_connection),
            "connection_page_url": "/#zerodha",
            "paper": {
                "connected": bool(paper_connection.get("connected")),
                "label": "Paper account",
                "funds": self.service.paper_account(),
                "zerodha_data_connection": paper_connection,
            },
            "real": {
                "connected": bool(live_connection.get("connected")),
                "label": "Real Money Zerodha",
                "zerodha": live_connection,
                "funds": self.app_state.account_margins.get("LIVE"),
            },
        }

    def _requested_app_mode(self, payload: dict | str | None) -> str:
        if isinstance(payload, str):
            mode = payload
        else:
            mode = (payload or {}).get("mode") or "PAPER"
        mode = str(mode or "PAPER").upper()
        return "LIVE" if mode in {"REAL", "LIVE"} else "PAPER"

    def _mode_lock(self, paper_connection: dict | None = None, live_connection: dict | None = None) -> dict:
        manager = self.service.manager
        if manager.status == SESSION_STATUS_RUNNING and manager.settings:
            mode = "REAL" if manager.settings.mode == MODE_REAL else "PAPER"
            return {"mode": mode, "reason": f"{mode} intraday session is running."}
        paper_connection = paper_connection if paper_connection is not None else self.app_state.connection_status("PAPER")
        live_connection = live_connection if live_connection is not None else self.app_state.connection_status("LIVE")
        if live_connection.get("connected"):
            return {"mode": "REAL", "reason": "Real Money Zerodha is connected."}
        if paper_connection.get("connected"):
            return {"mode": "PAPER", "reason": "Paper Data Zerodha is connected."}
        return {"mode": "", "reason": ""}

    def _mode_blockers(self, requested_mode: str, require_connection: bool = False) -> list[str]:
        requested_mode = self._requested_app_mode(requested_mode)
        lock = self._mode_lock()
        locked_mode = str(lock.get("mode") or "").upper()
        if locked_mode and locked_mode != ("REAL" if requested_mode == "LIVE" else requested_mode):
            return [f"{lock.get('reason')} Stop or disconnect it before using {self._label_for_mode(requested_mode)}."]
        blockers = []
        if hasattr(self.app_state, "blocking_connection_modes"):
            blockers = list(self.app_state.blocking_connection_modes(requested_mode) or [])
        if blockers:
            return [f"{self.app_state.auth_label(blockers[0])} is already connected."]
        if require_connection:
            connection = self.app_state.connection_status(requested_mode)
            if not connection.get("connected"):
                return [f"Connect {self.app_state.auth_label(requested_mode)} in the main app Connections page first."]
        return []

    def _label_for_mode(self, mode: str) -> str:
        return "Real Money" if self._requested_app_mode(mode) == "LIVE" else "Paper Trading"


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
