import unittest

import zerodha_client
from zerodha_client import ZerodhaClient


class FakeTicker:
    MODE_FULL = "full"

    instances = []

    def __init__(self, api_key, access_token):
        self.api_key = api_key
        self.access_token = access_token
        self.on_ticks = None
        self.on_connect = None
        self.on_close = None
        self.on_error = None
        self.on_reconnect = None
        self.on_noreconnect = None
        self.reconnect_max_tries = 50
        self.reconnect_max_delay = 60
        self.subscribed = []
        self.mode = None
        self.closed = False
        self.stopped = False
        FakeTicker.instances.append(self)

    def connect(self, threaded=True):
        self.threaded = threaded

    def subscribe(self, tokens):
        self.subscribed = list(tokens)

    def set_mode(self, mode, tokens):
        self.mode = (mode, list(tokens))

    def close(self):
        self.closed = True

    def stop(self):
        self.stopped = True


class FakeSocket:
    def __init__(self):
        self.stopped = False

    def subscribe(self, tokens):
        self.subscribed = list(tokens)

    def set_mode(self, mode, tokens):
        self.mode = (mode, list(tokens))

    def stop(self):
        self.stopped = True


class ZerodhaTickerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.original_ticker = zerodha_client.KiteTicker
        FakeTicker.instances = []
        zerodha_client.KiteTicker = FakeTicker

    def tearDown(self):
        zerodha_client.KiteTicker = self.original_ticker

    def client(self):
        client = object.__new__(ZerodhaClient)
        client.api_key = "api-key"
        client.access_token = "access-token"
        client.ticker = None
        client._named_tickers = {}
        return client

    def test_close_callback_does_not_stop_reactor(self):
        closed = []
        client = self.client()

        client.start_ticker([256265], on_ticks=lambda ticks: None, on_close=lambda code, reason: closed.append((code, reason)))
        socket = FakeSocket()
        FakeTicker.instances[0].on_close(socket, 1006, "dropped")

        self.assertEqual(closed, [(1006, "dropped")])
        self.assertFalse(socket.stopped)

    def test_stop_ticker_closes_socket_without_stopping_reactor(self):
        client = self.client()
        client.ticker = FakeTicker("api-key", "access-token")

        client.stop_ticker()

        self.assertTrue(FakeTicker.instances[0].closed)
        self.assertFalse(FakeTicker.instances[0].stopped)
        self.assertIsNone(client.ticker)

    def test_named_ticker_does_not_replace_default_ticker(self):
        client = self.client()
        client.start_ticker([256265], on_ticks=lambda ticks: None)
        default = client.ticker

        client.start_named_ticker("intraday_paper", [111, 222], on_ticks=lambda ticks: None)
        named = client._named_tickers["intraday_paper"]

        self.assertIs(client.ticker, default)
        self.assertIsNot(named, default)
        self.assertEqual(named.api_key, "api-key")
        self.assertEqual(named.access_token, "access-token")

        client.stop_named_ticker("intraday_paper")

        self.assertTrue(named.closed)
        self.assertFalse(default.closed)
        self.assertIs(client.ticker, default)

    def test_reconnect_policy_is_applied_to_kite_ticker(self):
        client = self.client()

        ticker = client.start_ticker(
            [256265],
            on_ticks=lambda ticks: None,
            reconnect_max_attempts=7,
            reconnect_backoff_seconds=3,
        )

        self.assertEqual(ticker.reconnect_max_tries, 7)
        self.assertEqual(ticker.reconnect_max_delay, 3)
        self.assertEqual(ticker.reconnect_policy["applied"]["reconnect_max_tries"], 7)


if __name__ == "__main__":
    unittest.main()
