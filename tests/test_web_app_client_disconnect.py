import unittest

from web_app import TradeBotRequestHandler, _is_client_disconnect


class WinError(Exception):
    def __init__(self, winerror):
        super().__init__("win error")
        self.winerror = winerror


class ErrnoError(Exception):
    def __init__(self, errno):
        super().__init__("errno error")
        self.errno = errno


class DisconnectingWriter:
    def write(self, _body):
        raise ConnectionAbortedError("client closed")


class RecordingHandler(TradeBotRequestHandler):
    def __init__(self):
        pass

    def send_response(self, status):
        self.sent_status = status

    def send_header(self, key, value):
        self.headers_sent = getattr(self, "headers_sent", []) + [(key, value)]

    def end_headers(self):
        self.headers_ended = True


class WebAppClientDisconnectTests(unittest.TestCase):
    def test_is_client_disconnect_detects_common_abort_errors(self):
        self.assertTrue(_is_client_disconnect(ConnectionAbortedError()))
        self.assertTrue(_is_client_disconnect(ConnectionResetError()))
        self.assertTrue(_is_client_disconnect(BrokenPipeError()))
        self.assertTrue(_is_client_disconnect(WinError(10053)))
        self.assertTrue(_is_client_disconnect(WinError(10054)))
        self.assertTrue(_is_client_disconnect(ErrnoError(32)))
        self.assertFalse(_is_client_disconnect(RuntimeError("real bug")))

    def test_send_json_suppresses_client_disconnect_write_failure(self):
        handler = RecordingHandler()
        handler.wfile = DisconnectingWriter()

        self.assertIsNone(handler.send_json({"ok": True}))
        self.assertEqual(handler.sent_status, 200)

    def test_do_get_suppresses_disconnect_when_error_response_write_fails(self):
        handler = RecordingHandler()
        handler.wfile = DisconnectingWriter()
        handler.route_get = lambda: (_ for _ in ()).throw(RuntimeError("route failed"))

        self.assertIsNone(handler.do_GET())

    def test_normal_server_error_still_returns_json_500(self):
        class Writer:
            body = b""

            def write(self, body):
                self.body += body

        handler = RecordingHandler()
        handler.wfile = Writer()
        handler.route_get = lambda: (_ for _ in ()).throw(RuntimeError("route failed"))

        handler.do_GET()

        self.assertEqual(handler.sent_status, 500)
        self.assertIn(b"route failed", handler.wfile.body)


if __name__ == "__main__":
    unittest.main()
