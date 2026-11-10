"""
test_benchmark.py -- offline tests for benchmark.py (portfolio vs index) and
the comparison block in the real app on the dev/ sample data. No network.
"""

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["STOCKAPP_DISABLE_GEMINI"] = "1"
os.environ["STOCKAPP_DISABLE_BROWSER_STORAGE"] = "1"
os.environ["STOCKAPP_DISABLE_PASSWORD"] = "1"

import benchmark as B  # noqa: E402

TODAY = date(2026, 11, 10)


def d(n):
    """n days before TODAY, as an ISO date."""
    return (TODAY - timedelta(days=n)).isoformat()


def tx(sym, side, qty, price, when, cur="USD"):
    return {"symbol": sym, "side": side, "qty": qty, "price": price,
            "currency": cur, "date": when}


class TestCompare(unittest.TestCase):
    IDX = [(d(100), 100.0), (d(50), 110.0), (d(0), 120.0)]   # index +20%

    def test_one_buy_beats_index(self):
        r = B.compare([tx("A", "buy", 10, 50, d(100))],
                      {"A": [(d(100), 50), (d(50), 55), (d(0), 75)]},   # stock +50%
                      {"A": 1.0}, self.IDX, today=TODAY)
        self.assertAlmostEqual(r["my_pct"], 50.0)
        self.assertAlmostEqual(r["idx_pct"], 20.0)
        self.assertAlmostEqual(r["diff"], 30.0)
        self.assertEqual(r["start"], d(100))
        self.assertEqual(r["points"][0][1:], (0.0, 0.0))
        self.assertIn("ניצחת", B.verdict_he(r, "S&P 500"))

    def test_fx_cancels_out(self):
        a = B.compare([tx("A", "buy", 10, 50, d(100))], {"A": [(d(100), 50), (d(0), 40)]},
                      {"A": 1.0}, self.IDX, today=TODAY)
        b = B.compare([tx("A", "buy", 10, 50, d(100))], {"A": [(d(100), 50), (d(0), 40)]},
                      {"A": 3.7}, self.IDX, today=TODAY)
        self.assertAlmostEqual(a["my_pct"], -20.0)
        self.assertAlmostEqual(a["diff"], b["diff"])
        self.assertAlmostEqual(b["invested"], 500 * 3.7)
        self.assertIn("ניצח אותך", B.verdict_he(a, "ת״א 125"))

    def test_buy_in_middle_uses_index_of_that_day(self):
        # 1000 at d100 (idx 100) + 1100 at d50 (idx 110): shadow = 10 + 10 units.
        r = B.compare([tx("A", "buy", 10, 100, d(100)), tx("A", "buy", 10, 110, d(50))],
                      {"A": [(d(100), 100), (d(50), 110), (d(0), 120)]},
                      {"A": 1.0}, self.IDX, today=TODAY)
        self.assertAlmostEqual(r["invested"], 2100)
        self.assertAlmostEqual(r["idx_gain"], 20 * 120 - 2100)
        self.assertAlmostEqual(r["diff"], 0.0)          # stock == index
        self.assertTrue(B.verdict_he(r, "X").startswith("תיקו"))

    def test_sale_sells_same_share_of_shadow(self):
        # Buy 10 @100, sell 5 @110 at d50: half the shadow units go too.
        r = B.compare([tx("A", "buy", 10, 100, d(100)), tx("A", "sell", 5, 110, d(50))],
                      {"A": [(d(100), 100), (d(50), 110), (d(0), 90)]},
                      {"A": 1.0}, self.IDX, today=TODAY)
        self.assertAlmostEqual(r["my_gain"], 5 * 90 + 550 - 1000)        # 0
        self.assertAlmostEqual(r["idx_gain"], 5 * 120 + 5 * 110 - 1000)  # +150
        self.assertAlmostEqual(r["idx_pct"], 15.0)

    def test_holding_from_before_window_opens_at_window_start(self):
        r = B.compare([tx("A", "buy", 10, 10, d(800))],
                      {"A": [(d(100), 100), (d(0), 150)]}, {"A": 1.0},
                      self.IDX, today=TODAY)
        self.assertAlmostEqual(r["invested"], 1000)     # valued at the window start
        self.assertAlmostEqual(r["my_pct"], 50.0)
        self.assertEqual(r["start"], d(100))

    def test_trade_after_last_index_close_counts(self):
        r = B.compare([tx("A", "buy", 1, 100, TODAY.isoformat())],
                      {"A": [(d(0), 100)]}, {"A": 1.0},
                      [(d(100), 100.0), (d(1), 120.0)], today=TODAY)
        self.assertAlmostEqual(r["invested"], 100)
        self.assertAlmostEqual(r["diff"], 0.0)

    def test_missing_history_is_left_out(self):
        r = B.compare([tx("A", "buy", 10, 50, d(100)), tx("NOPE", "buy", 1, 5, d(100))],
                      {"A": [(d(100), 50), (d(0), 60)]}, {"A": 1.0, "NOPE": 1.0},
                      self.IDX, today=TODAY)
        self.assertEqual(r["missing"], ["NOPE"])
        self.assertAlmostEqual(r["invested"], 500)

    def test_nothing_to_compare(self):
        self.assertIsNone(B.compare([], {}, {}, self.IDX, today=TODAY))
        self.assertIsNone(B.compare([tx("A", "buy", 1, 1, d(5))], {"A": [(d(5), 1)]},
                                    {"A": 1.0}, [], today=TODAY))
        self.assertIsNone(B.compare([tx("A", "buy", 1, 1, d(5))], {}, {}, self.IDX,
                                    today=TODAY))


class TestInApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["STOCKAPP_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("STOCKAPP_DATA_DIR", None)
        self.tmp.cleanup()

    def test_vs_index_block(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(HERE, "dev", "demo_app.py"), default_timeout=60)
        at.run()
        at.text_input(key="search_box").set_value("NVDA").run()
        at.segmented_control(key="trade_side_NVDA").set_value("קנייה")
        at.number_input(key="trade_qty_NVDA").set_value(5)
        at.date_input(key="trade_date_NVDA").set_value(date(2026, 6, 1))
        [b for b in at.button if b.label == "שמור עסקה"][0].click().run()
        at.button(key="back_home").click().run()
        self.assertFalse(at.exception, at.exception)
        md = "\n".join(m.value for m in at.markdown)
        self.assertIn("מול המדד", md)
        self.assertIn("נקודות אחוז", md)
        at.segmented_control(key="bench_index").set_value("S&P 500").run()
        self.assertFalse(at.exception, at.exception)
        self.assertIn("S&amp;P 500 ניצח", "\n".join(m.value for m in at.markdown))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
