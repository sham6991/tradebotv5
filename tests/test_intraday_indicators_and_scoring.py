import unittest

from intraday.formula_validator import formula_metadata
from intraday.indicators import atr, ema, relative_volume, rsi
from intraday.liquidity import score_liquidity
from intraday.models import IntradaySettings, StockSnapshot
from intraday.entry_structure import analyse_entry_structure
from intraday.scoring import score_snapshot
from intraday.vwap import calculate_vwap
from intraday.volume_profile import calculate_volume_profile


class IntradayIndicatorsAndScoringTests(unittest.TestCase):
    def candles(self):
        rows = []
        for index in range(30):
            price = 100 + index
            rows.append({
                "open": price - 0.5,
                "high": price + 1.5,
                "low": price - 1,
                "close": price,
                "volume": 1000 + index * 100,
            })
        return rows

    def settings(self):
        return IntradaySettings.from_payload({
            "stocks": ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"],
            "minimum_entry_score": 50,
        })

    def test_indicator_outputs_are_numeric(self):
        rows = self.candles()
        closes = [row["close"] for row in rows]
        self.assertEqual(len(ema(closes, 20)), len(rows))
        self.assertGreater(rsi(closes, 14)[-1], 50)
        self.assertEqual(len(atr(rows, 14)), len(rows))
        self.assertGreater(calculate_vwap(rows), 0)
        self.assertGreater(relative_volume(rows, 20), 1)
        profile = calculate_volume_profile(rows)
        self.assertGreater(profile["vah"], profile["val"])

    def test_wilder_rsi_matches_known_sample(self):
        closes = [
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10,
            45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
            46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21,
        ]
        values = rsi(closes, 14)
        self.assertAlmostEqual(values[14], 70.46, places=2)
        self.assertAlmostEqual(values[-1], 62.88, places=2)

    def test_formula_version_is_available_for_locked_settings(self):
        metadata = formula_metadata()
        self.assertIn("formula_version", metadata)
        self.assertIn("Wilder", metadata["formulas"]["RSI"])

    def test_ema_and_rsi_do_not_trigger_entry_without_structure(self):
        liquidity = score_liquidity(129, {
            "buy": [{"price": 128.95, "quantity": 15000}],
            "sell": [{"price": 129.05, "quantity": 14000}],
        })
        snapshot = StockSnapshot(
            symbol="INFY",
            ltp=129,
            open=127,
            high=130,
            low=126,
            close=129,
            ema20=118,
            ema50=110,
            rsi=68,
            vwap=122,
            poc=121,
            vah=125,
            val=112,
            relative_volume=2.0,
            liquidity_score=liquidity["liquidity_score"],
            news_score=5,
        )
        score_snapshot(snapshot, self.settings(), {"long_bonus": 2})
        self.assertEqual(snapshot.selected_side, "NO TRADE")
        blockers = snapshot.reason["score_breakdown"]["blockers"]
        self.assertIn("Long structure trigger missing.", blockers)

    def test_scoring_selects_long_only_after_structure_volume_and_liquidity_pass(self):
        rows = self.candles()
        rows[-1]["volume"] = 9000
        liquidity = score_liquidity(129, {
            "buy": [{"price": 128.95, "quantity": 15000}],
            "sell": [{"price": 129.05, "quantity": 14000}],
        })
        closes = [row["close"] for row in rows]
        profile = calculate_volume_profile(rows)
        snapshot = StockSnapshot(
            symbol="INFY",
            ltp=129,
            open=127,
            high=130,
            low=126,
            close=129,
            volume=rows[-1]["volume"],
            ema20=ema(closes, 20)[-1],
            ema50=ema(closes, 50)[-1],
            rsi=rsi(closes, 14)[-1],
            vwap=calculate_vwap(rows),
            poc=profile["poc"],
            vah=profile["vah"],
            val=profile["val"],
            relative_volume=relative_volume(rows, 20),
            liquidity_score=liquidity["liquidity_score"],
            spread=liquidity["spread"],
            spread_pct=liquidity["spread_pct"],
            bid_qty=liquidity["bid_qty"],
            ask_qty=liquidity["ask_qty"],
            depth_imbalance=liquidity["depth_imbalance"],
            news_score=5,
        )
        snapshot.reason["entry_structure"] = analyse_entry_structure(
            rows,
            snapshot,
            self.settings(),
            ema20_values=ema(closes, 20),
            ema50_values=ema(closes, 50),
            rsi_values=rsi(closes, 14),
            traps={"long": {"trap_warning": "NONE"}, "short": {"trap_warning": "NONE"}},
        )
        score_snapshot(snapshot, self.settings(), {"long_bonus": 2})
        self.assertGreater(snapshot.final_long_score, snapshot.final_short_score)
        self.assertEqual(snapshot.selected_side, "LONG")
        self.assertTrue(snapshot.reason["score_breakdown"]["gates"]["structure_trigger"])

    def test_scoring_selects_short_only_after_structure_volume_and_liquidity_pass(self):
        rows = []
        for index in range(30):
            price = 130 - index
            rows.append({
                "open": price + 0.5,
                "high": price + 1.5,
                "low": price - 1,
                "close": price,
                "volume": 1000 + index * 100,
            })
        rows[-1]["volume"] = 9000
        liquidity = score_liquidity(101, {
            "buy": [{"price": 100.95, "quantity": 12000}],
            "sell": [{"price": 101.05, "quantity": 18000}],
        })
        closes = [row["close"] for row in rows]
        profile = calculate_volume_profile(rows)
        settings = self.settings()
        snapshot = StockSnapshot(
            symbol="RELIANCE",
            ltp=101,
            open=101.5,
            high=102.5,
            low=100,
            close=101,
            volume=rows[-1]["volume"],
            ema20=ema(closes, 20)[-1],
            ema50=ema(closes, 50)[-1],
            rsi=rsi(closes, 14)[-1],
            vwap=calculate_vwap(rows),
            poc=profile["poc"],
            vah=profile["vah"],
            val=profile["val"],
            relative_volume=relative_volume(rows, 20),
            liquidity_score=liquidity["liquidity_score"],
            spread=liquidity["spread"],
            spread_pct=liquidity["spread_pct"],
            bid_qty=liquidity["bid_qty"],
            ask_qty=liquidity["ask_qty"],
            depth_imbalance=liquidity["depth_imbalance"],
        )
        snapshot.reason["entry_structure"] = analyse_entry_structure(
            rows,
            snapshot,
            settings,
            ema20_values=ema(closes, 20),
            ema50_values=ema(closes, 50),
            rsi_values=rsi(closes, 14),
            traps={"long": {"trap_warning": "NONE"}, "short": {"trap_warning": "NONE"}},
        )
        score_snapshot(snapshot, settings, {"short_bonus": 2})
        self.assertGreater(snapshot.final_short_score, snapshot.final_long_score)
        self.assertEqual(snapshot.selected_side, "SHORT")
        self.assertTrue(snapshot.reason["score_breakdown"]["gates"]["structure_trigger"])


if __name__ == "__main__":
    unittest.main()
