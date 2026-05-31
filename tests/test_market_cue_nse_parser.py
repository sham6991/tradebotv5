import unittest

from market_cue.nse_fii_dii import parse_manual_entry, parse_nse_fii_dii_csv


class MarketCueNseParserTests(unittest.TestCase):
    def test_parser_reads_standard_fii_dii_csv(self):
        csv_text = """Date,Category,Buy Value,Sell Value,Net Value
2026-05-26,FII/FPI,1000,3407.87,-2407.87
2026-05-26,DII,2500,1138.57,1361.43
"""
        parsed = parse_nse_fii_dii_csv(csv_text, file_name="fii_dii.csv")

        self.assertEqual(parsed["status"], "OK")
        self.assertEqual(parsed["data_date"], "2026-05-26")
        self.assertEqual(parsed["fii_net"], -2407.87)
        self.assertEqual(parsed["dii_net"], 1361.43)

    def test_parser_handles_structure_change_with_purchase_sale(self):
        csv_text = """Trade Date,Investor Type,Purchase,Sale
26-05-2026,Foreign Portfolio Investors,4000,5000
26-05-2026,Domestic Institutional Investors,3000,1000
"""
        parsed = parse_nse_fii_dii_csv(csv_text)

        self.assertEqual(parsed["status"], "OK")
        self.assertEqual(parsed["fii_net"], -1000)
        self.assertEqual(parsed["dii_net"], 2000)

    def test_manual_entry_marks_incomplete_partial(self):
        parsed = parse_manual_entry("-100", "", "2026-05-26")

        self.assertEqual(parsed["status"], "PARTIAL")
        self.assertEqual(parsed["fii_net"], -100)
        self.assertIsNone(parsed["dii_net"])

    def test_bad_csv_does_not_crash(self):
        parsed = parse_nse_fii_dii_csv("not,a,valid\nsingle")

        self.assertIn(parsed["status"], {"FAILED", "PARTIAL"})


if __name__ == "__main__":
    unittest.main()
