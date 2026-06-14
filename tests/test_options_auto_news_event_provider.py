import tempfile
import unittest

from options_auto.intelligence.news_event_provider import PulseNewsProvider


class OptionsAutoNewsEventProviderTests(unittest.TestCase):
    def test_pulse_provider_parses_headlines_without_network_in_tests(self):
        html = """
        <html><body>
          <a href="/news/nifty-rbi-rate-shock">Nifty drops as RBI delivers surprise rate hike</a>
          <span class="date">5 minutes ago</span>
          <a href="https://pulse.zerodha.com/news/crude">Crude oil spike weighs on Indian market</a>
          <time>1 hour ago</time>
        </body></html>
        """

        def fake_fetch(_url, _timeout):
            return html

        with tempfile.TemporaryDirectory() as temp_dir:
            provider = PulseNewsProvider(cache_path=f"{temp_dir}/pulse.json", fetcher=fake_fetch)
            result = provider.fetch({"news_event_max_items": 5})

        self.assertEqual(result.status, "OK")
        self.assertEqual(result.provider, "ZERODHA_PULSE")
        self.assertEqual(len(result.items), 2)
        self.assertIn("RBI", result.items[0]["title"])
        self.assertEqual(result.items[0]["age_minutes"], 5.0)


if __name__ == "__main__":
    unittest.main()
