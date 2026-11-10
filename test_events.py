"""
test_events.py -- offline tests for events.py ("this week in my portfolio")
and the card on the home screen of the real app (dev/ sample data). No network.
"""

import os
import sys
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["STOCKAPP_DISABLE_GEMINI"] = "1"
os.environ["STOCKAPP_DISABLE_BROWSER_STORAGE"] = "1"
os.environ["STOCKAPP_DISABLE_PASSWORD"] = "1"

import events as E  # noqa: E402

TODAY = date(2026, 11, 10)   # a Tuesday


def cal(earnings=None, ex=None, pay=None, per_share=None):
    return {"earnings": earnings, "ex_dividend": ex, "dividend_pay": pay,
            "dividend_per_share": per_share}


class TestUpcoming(unittest.TestCase):
    def test_only_next_7_days_sorted(self):
        cals = {"MSFT": cal("2027-01-27", "2026-11-19", "2026-12-10", 0.91),
                "AAPL": cal("2027-01-28", "2026-11-12", "2026-11-16", 0.27),
                "ESLT.TA": cal("2026-11-17", "2026-10-13"),
                "OLD": cal("2026-11-09")}
        items = E.upcoming(cals, {"AAPL": 20, "MSFT": 5, "ESLT.TA": 15}, today=TODAY)
        self.assertEqual([(e["symbol"], e["kind"]) for e in items],
                         [("AAPL", "ex_dividend"), ("AAPL", "dividend_pay"),
                          ("ESLT.TA", "earnings")])
        self.assertEqual(items[0]["when"], "יום ה׳ 12.11")
        self.assertIn("$0.27", items[0]["what"])
        self.assertAlmostEqual(items[1]["amount"][0], 0.27 * 20)
        self.assertEqual(items[2]["what"], "דוח רבעוני")

    def test_tase_dividend_has_no_amount(self):
        items = E.upcoming({"LUMI.TA": cal(None, "2026-11-11", "2026-11-13", 77.0)},
                           {"LUMI.TA": 100}, today=TODAY)
        self.assertEqual([e["amount"] for e in items], [None, None])
        self.assertNotIn("$", items[0]["what"])
        self.assertEqual(items[0]["when"], "מחר")

    def test_today_and_bad_values(self):
        items = E.upcoming({"A": cal(TODAY), "B": cal("garbage"), "C": None}, {}, today=TODAY)
        self.assertEqual([(e["symbol"], e["when"]) for e in items], [("A", "היום")])
        self.assertEqual(E.upcoming({}, {}, today=TODAY), [])

    def test_next_event(self):
        cals = {"A": cal("2026-12-01"), "B": cal("2026-11-20"), "C": cal("2026-01-01")}
        self.assertEqual(E.next_event(cals, today=TODAY), (date(2026, 11, 20), "B", "דוח רבעוני"))
        cals["D"] = cal("2027-01-01", ex="2026-11-19")          # a dividend counts too
        self.assertEqual(E.next_event(cals, today=TODAY),
                         (date(2026, 11, 19), "D", "יום אקס דיבידנד"))
        self.assertIsNone(E.next_event({}, today=TODAY))

    def test_day_he(self):
        self.assertEqual(E.day_he(date(2026, 11, 15), TODAY), "יום א׳ 15.11")


class TestInApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["STOCKAPP_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("STOCKAPP_DATA_DIR", None)
        self.tmp.cleanup()

    def _buy(self, at, sym):
        at.text_input(key="search_box").set_value(sym).run()
        at.segmented_control(key=f"trade_side_{sym}").set_value("קנייה")
        at.number_input(key=f"trade_qty_{sym}").set_value(10)
        [b for b in at.button if b.label == "שמור עסקה"][0].click().run()
        at.button(key="back_home").click().run()

    def test_week_card(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(HERE, "dev", "demo_app.py"), default_timeout=60)
        at.run()
        self._buy(at, "TEVA.TA")                     # report in 30 days -> nothing this week
        self.assertFalse(at.exception, at.exception)
        caps = "\n".join(c.value for c in at.caption)
        self.assertIn("אין השבוע", caps)
        self.assertIn("הבא בתור: דוח רבעוני", caps)
        self._buy(at, "AAPL")                        # ex-div + payment this week
        labels = [b.label for b in at.button if b.key and b.key.startswith("ev_")]
        self.assertEqual(len(labels), 2, labels)
        self.assertTrue(any("תשלום דיבידנד" in l and "$2.70" in l for l in labels), labels)
        [b for b in at.button if b.key and b.key.startswith("ev_")][0].click().run()
        self.assertIn("ציון קנייה", "\n".join(m.value for m in at.markdown))   # opens the stock


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
