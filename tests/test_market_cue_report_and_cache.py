import os
import tempfile
import unittest
from unittest.mock import patch

from market_cue.database import MarketCueDatabase
from market_cue.global_data import _cached_or_failed, _history_fallback_row, _info_get
from market_cue.report_generator import generate_report
from market_cue.router import MarketCueService
from market_cue.scoring import score_market_cues
from market_cue.validator import validate_market_data


class MarketCueReportAndCacheTests(unittest.TestCase):
    def test_report_generation_with_partial_data(self):
        raw = {
            "indian_market": {
                "NIFTY 50": {"value": 22500, "previous_close": 22500, "percent_change": 0, "status": "OK", "timestamp": "2099-01-01 09:20:00"},
                "BANK NIFTY": {"value": None, "previous_close": None, "status": "FAILED", "timestamp": ""},
            },
            "global_market": {},
            "institutional_flow": {"fii_net": None, "dii_net": None, "status": "FAILED", "fetch_mode": "auto_download", "data_date": None},
        }
        validation = validate_market_data(raw)
        scoring = score_market_cues(raw, validation)
        report = generate_report(raw, validation, scoring)

        self.assertIn("Data Reliability", report["sections"])
        self.assertIn("decision-support", report["report_text"])

    def test_cached_stale_value_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MarketCueDatabase(os.path.join(tmp, "cue.sqlite3"))
            db.cache_value("yfinance", "NQ=F", {"name": "Nasdaq Futures", "value": 100, "percent_change": 0.5, "timestamp": "2026-05-26 10:00:00", "status": "OK"})

            cached = _cached_or_failed(db, "Nasdaq Futures", "NQ=F", "network down")

            self.assertEqual(cached["status"], "STALE")
            self.assertTrue(cached["stale"])

    def test_expired_cache_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MarketCueDatabase(os.path.join(tmp, "cue.sqlite3"))
            db.cache_value("yfinance", "NQ=F", {"name": "Nasdaq Futures", "value": 100, "percent_change": 0.5, "timestamp": "2026-05-26 10:00:00", "status": "OK"})
            with db.connect() as conn:
                conn.execute("UPDATE market_cue_cache SET created_at = '2020-01-01 00:00:00'")

            cached = _cached_or_failed(db, "Nasdaq Futures", "NQ=F", "network down")

            self.assertEqual(cached["status"], "FAILED")

    def test_yfinance_fast_info_accepts_camel_case_keys(self):
        info = {"lastPrice": 101.5, "previousClose": 100.0}

        self.assertEqual(_info_get(info, "last_price", "lastPrice"), 101.5)
        self.assertEqual(_info_get(info, "previous_close", "previousClose"), 100.0)

    def test_yfinance_history_fallback_builds_partial_row(self):
        class FakeTicker:
            def history(self, period=None, interval=None):
                import pandas as pd
                return pd.DataFrame({"Close": [100.0, 103.0]}, index=pd.to_datetime(["2026-05-25", "2026-05-26"]))

        row = _history_fallback_row(FakeTicker(), "Nasdaq Futures", "NQ=F")

        self.assertEqual(row["status"], "PARTIAL")
        self.assertEqual(row["fetch_mode"], "history_fallback")
        self.assertAlmostEqual(row["percent_change"], 3.0)

    def test_manual_override_warning(self):
        raw = {
            "indian_market": {"NIFTY 50": {"value": 1, "previous_close": 1, "status": "OK", "timestamp": "2099-01-01 09:20:00"}},
            "global_market": {},
            "institutional_flow": {"fii_net": 1, "dii_net": 1, "status": "OK", "data_date": "2099-01-01"},
        }
        validation = validate_market_data(raw, [{"field_name": "fii_net", "original_value": 1, "override_value": 2}])

        self.assertTrue(any("Manual override" in warning for warning in validation["warnings"]))

    def test_unknown_manual_override_is_ignored_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = MarketCueService(kite_client_provider=lambda: None, db=MarketCueDatabase(os.path.join(tmp, "cue.sqlite3")))
            raw = {
                "indian_market": {"NIFTY 50": {"value": 1, "previous_close": 1, "status": "OK"}},
                "global_market": {},
                "institutional_flow": {},
            }

            result = service.analyze({
                "raw_data": raw,
                "manual_overrides": [{"field_name": "NIFTY 50.unknown_field", "override_value": "99"}],
            })

            self.assertFalse(result["manual_overrides"][0]["applied"])
            self.assertEqual(result["raw_data"]["indian_market"]["NIFTY 50"]["value"], 1)
            self.assertTrue(any("ignored" in warning.lower() for warning in result["validated_data"]["warnings"]))

    def test_named_manual_override_updates_one_cue(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = MarketCueService(kite_client_provider=lambda: None, db=MarketCueDatabase(os.path.join(tmp, "cue.sqlite3")))
            raw = {
                "indian_market": {
                    "NIFTY 50": {"value": 1, "previous_close": 1, "status": "OK"},
                    "BANK NIFTY": {"value": 2, "previous_close": 2, "status": "OK"},
                },
                "global_market": {},
                "institutional_flow": {},
            }

            updated = service.apply_manual_overrides(raw, [{"field_name": "NIFTY 50.previous_close", "override_value": "22500"}])

            self.assertEqual(updated["indian_market"]["NIFTY 50"]["previous_close"], 22500)
            self.assertEqual(updated["indian_market"]["BANK NIFTY"]["previous_close"], 2)

    def test_uploaded_csv_scope_hint_overrides_ambiguous_file_name(self):
        csv_text = """Date,Category,Buy Value,Sell Value,Net Value
2026-05-26,FII/FPI,1000,3407.87,-2407.87
2026-05-26,DII,2500,1138.57,1361.43
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fii_dii.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(csv_text)
            service = MarketCueService(kite_client_provider=lambda: None, db=MarketCueDatabase(os.path.join(tmp, "cue.sqlite3")))

            parsed = service.upload_fii_dii(path, scope_hint="combined")

            self.assertEqual(parsed["status"], "OK")
            self.assertEqual(parsed["scope"], "NSE+BSE+MSEI")

    def test_fetch_requires_manual_fii_dii_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = MarketCueService(kite_client_provider=lambda: None, db=MarketCueDatabase(os.path.join(tmp, "cue.sqlite3")))
            with patch("market_cue.router.fetch_kite_index_data", return_value={}), patch("market_cue.router.fetch_global_cues", return_value={}):
                raw = service.fetch()

            flow = raw["institutional_flow"]
            self.assertEqual(flow["fetch_mode"], "manual_upload")
            self.assertEqual(flow["status"], "FAILED")
            self.assertTrue(any("Upload" in warning for warning in flow["warnings"]))

    def test_saved_report_history_keeps_latest_sixty_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = MarketCueService(kite_client_provider=lambda: None, db=MarketCueDatabase(os.path.join(tmp, "cue.sqlite3")))
            for index in range(61):
                service.db.save_report({
                    "raw_data": {
                        "indian_market": {},
                        "global_market": {},
                        "institutional_flow": {"fii_net": index, "dii_net": index, "status": "OK"},
                    },
                    "validated_data": {"data_reliability": "Good"},
                    "scoring": {"final_score": index, "bias": f"Report {index}", "confidence": index, "risk_level": "Low"},
                    "report_text": f"Report {index}",
                    "source_logs": [{"source": "test", "symbol": str(index), "status": "OK"}],
                    "manual_overrides": [{"field_name": "fii_net", "original_value": "", "override_value": str(index), "reason": "test"}],
                })

            reports = service.history()

            self.assertEqual(len(reports), 60)
            self.assertEqual(reports[0]["bias"], "Report 60")
            self.assertEqual(reports[-1]["bias"], "Report 1")
            self.assertNotIn("Report 0", {row["bias"] for row in reports})
            with service.db.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_cue_source_logs").fetchone()[0], 60)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_cue_manual_overrides").fetchone()[0], 60)

    def test_latest_bias_does_not_fetch_when_no_report_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = MarketCueService(
                kite_client_provider=lambda: (_ for _ in ()).throw(AssertionError("should not fetch")),
                db=MarketCueDatabase(os.path.join(tmp, "cue.sqlite3")),
            )

            latest = service.latest_bias()

            self.assertEqual(latest["status"], "NO_REPORT")

    def test_save_requires_prior_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = MarketCueService(kite_client_provider=lambda: None, db=MarketCueDatabase(os.path.join(tmp, "cue.sqlite3")))

            with self.assertRaisesRegex(ValueError, "Analyze market cues"):
                service.save({})


if __name__ == "__main__":
    unittest.main()
