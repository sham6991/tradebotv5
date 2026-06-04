import unittest

from intraday.news_engine import (
    NewsEngine,
    ZerodhaPulseNewsAdapter,
    parse_zerodha_pulse_html,
    sentiment_score_for_symbol,
)


PULSE_HTML = """
<ul id="news">
  <li class="box item" id="item-1">
    <h2 class="title"><a href="https://example.com/infosys">Infosys wins large AI order</a></h2>
    <div class="desc">INFY growth outlook improves after the deal.</div>
    <span class="date" title="10:15 AM, 03 Jun 2026">1 hour ago</span>
    <span class="feed">&mdash; Economic Times</span>
  </li>
  <li class="box item" id="item-2">
    <h2 class="title"><a href="https://example.com/market">Sensex falls as Nifty stays weak</a></h2>
    <div class="desc">Stock market breadth remains negative.</div>
    <span class="date" title="10:05 AM, 03 Jun 2026">1 hour ago</span>
    <span class="feed">&mdash; The Hindu Business</span>
  </li>
</ul>
"""


class IntradayNewsEngineTests(unittest.TestCase):
    def test_zerodha_pulse_parser_tags_symbols_and_market_context(self):
        rows = parse_zerodha_pulse_html(
            PULSE_HTML,
            ["INFY", "RELIANCE"],
            max_items_per_symbol=2,
            include_market_items=1,
        )

        self.assertEqual([row.symbol for row in rows], ["INFY", "MARKET"])
        self.assertEqual(rows[0].source, "Zerodha Pulse / Economic Times")
        self.assertEqual(rows[0].timestamp, "2026-06-03T10:15:00")
        self.assertGreater(sentiment_score_for_symbol(rows, "INFY")["score"], 0)
        self.assertEqual(sentiment_score_for_symbol(rows, "RELIANCE")["sentiment"], "Unavailable")

    def test_zerodha_pulse_adapter_respects_live_news_toggle(self):
        adapter = ZerodhaPulseNewsAdapter()
        self.assertEqual(adapter.collect(["INFY"], {"live_news_enabled": False}), [])

    def test_news_engine_includes_zerodha_pulse_by_default(self):
        engine = NewsEngine()
        adapter_names = [adapter.__class__.__name__ for adapter in engine.adapters]
        self.assertIn("ZerodhaPulseNewsAdapter", adapter_names)


if __name__ == "__main__":
    unittest.main()
