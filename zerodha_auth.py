import base64
import json
import os
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import keyring
except ImportError:
    keyring = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_STORE_PATH = os.path.join(BASE_DIR, "data", "zerodha_auth.json")
DEFAULT_REDIRECT_URL = "http://127.0.0.1:8000/zerodha/callback"
KEYRING_SERVICE = "TradeBotV3 Zerodha"


class ZerodhaAuthStore:
    def __init__(self, path=AUTH_STORE_PATH):
        self.path = path

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

    def save_access_token(self, access_token):
        data = self.load()
        for key in ("api_key", "api_secret", "redirect_url", "updated_at"):
            data.pop(key, None)
        data.update({
            "access_token": self._protect(access_token),
            "access_token_date": date.today().isoformat(),
            "access_token_saved_at": datetime.now().isoformat(timespec="seconds"),
        })
        self._write(data)

    def clear_access_token(self):
        data = self.load()
        for key in ("api_key", "api_secret", "redirect_url", "updated_at"):
            data.pop(key, None)
        data.pop("access_token", None)
        data.pop("access_token_date", None)
        data.pop("access_token_saved_at", None)
        self._write(data)

    def api_settings(self):
        return {
            "api_key": "",
            "api_secret": "",
            "redirect_url": DEFAULT_REDIRECT_URL,
        }

    def todays_access_token(self):
        data = self.load()
        if data.get("access_token_date") != date.today().isoformat():
            return ""
        return self._unprotect(data.get("access_token", ""))

    def _write(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _protect(self, value):
        raw = (value or "").encode("utf-8")
        if not raw:
            return ""
        if keyring is not None:
            key_name = f"{KEYRING_SERVICE}:{datetime.now().timestamp()}"
            try:
                keyring.set_password(KEYRING_SERVICE, key_name, value)
                return "keyring:" + key_name
            except Exception:
                pass
        return "b64:" + base64.b64encode(raw).decode("ascii")

    def _unprotect(self, value):
        if not value:
            return ""
        if isinstance(value, str) and value.startswith("keyring:") and keyring is not None:
            try:
                return keyring.get_password(KEYRING_SERVICE, value[8:]) or ""
            except Exception:
                return ""
        if isinstance(value, str) and value.startswith("b64:"):
            try:
                return base64.b64decode(value[4:].encode("ascii")).decode("utf-8")
            except Exception:
                return ""
        return value


class ZerodhaCallbackServer:
    def __init__(self, redirect_url):
        self.redirect_url = redirect_url or DEFAULT_REDIRECT_URL
        parsed = urlparse(self.redirect_url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        self.request_token = ""
        self.error = ""
        self._server = None
        self._thread = None

    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != outer.path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Not found")
                    return

                params = parse_qs(parsed.query)
                status = (params.get("status") or [""])[0]
                request_token = (params.get("request_token") or [""])[0]
                if status == "success" and request_token:
                    outer.request_token = request_token
                    body = b"Request token received. You can return to TradeBotV3."
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    threading.Thread(target=outer.stop, daemon=True).start()
                    return

                outer.error = "Zerodha callback did not include a successful request_token."
                body = b"Zerodha login callback failed. Return to TradeBotV3."
                self.send_response(400)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        self._server = HTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="tradebot_zerodha_callback",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def extract_request_token_from_url(url):
    parsed = urlparse(url.strip())
    params = parse_qs(parsed.query)
    token = (params.get("request_token") or [""])[0]
    if not token:
        raise ValueError("No request_token found in the redirected URL.")
    return token

