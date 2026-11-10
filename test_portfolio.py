"""
test_portfolio.py -- offline tests for portfolio.py (no network, no Streamlit).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import portfolio as P


class TestUnits(unittest.TestCase):
    def test_agorot_and_pence_to_major(self):
        self.assertEqual(P.to_major(13580.0, "ILA"), (135.8, "ILS"))
        self.assertEqual(P.to_major(250.0, "GBp"), (2.5, "GBP"))
        self.assertEqual(P.to_major(182.4, "USD"), (182.4, "USD"))
        self.assertEqual(P.to_major(None, "ILA"), (None, "ILS"))

    def test_money(self):
        self.assertEqual(P.money(1234.5, "ILS"), "₪1,234.50")
        self.assertEqual(P.money(-12.0, "USD"), "-$12.00")
        self.assertEqual(P.money(5.0, "CHF"), "5.00 CHF")
        self.assertEqual(P.money(None, "USD"), "—")
        self.assertEqual(P.money(1234.5, "ILS", 0), "₪1,234")

    def test_fx_symbol(self):
        self.assertEqual(P.fx_symbol("USD"), "USDILS=X")
        self.assertIsNone(P.fx_symbol("ILS"))


class TestEditing(unittest.TestCase):
    def test_parse_skips_bad_rows(self):
        raw = ('[{"symbol":"nvda","qty":20,"cost":118},{"symbol":"X"},'
               '{"symbol":"Y","qty":-1,"cost":5},{"symbol":"Z","qty":"a","cost":1}]')
        self.assertEqual(P.parse(raw), [{"symbol": "NVDA", "qty": 20.0, "cost": 118.0}])
        self.assertEqual(P.parse("not json"), [])
        self.assertEqual(P.parse(None), [])

    def test_upsert_add_update_remove(self):
        h = P.upsert([], "nvda", 20, 118)
        h = P.upsert(h, "TEVA.TA", 300, 52.4)
        self.assertEqual([x["symbol"] for x in h], ["NVDA", "TEVA.TA"])
        h = P.upsert(h, "NVDA", 25, 120)           # update keeps order
        self.assertEqual(h[0], {"symbol": "NVDA", "qty": 25.0, "cost": 120.0})
        self.assertEqual(len(h), 2)
        h = P.upsert(h, "NVDA", 0, 120)            # qty 0 removes
        self.assertEqual([x["symbol"] for x in h], ["TEVA.TA"])
        self.assertEqual(P.remove(h, "teva.ta"), [])
        with self.assertRaises(ValueError):
            P.upsert([], "A", 1, -5)

    def test_round_trip(self):
        h = P.upsert([], "AAPL", 3, 201.5)
        self.assertEqual(P.parse(P.dumps(h)), h)


class TestValuation(unittest.TestCase):
    def test_position_usd_with_fx(self):
        row = P.position({"symbol": "NVDA", "qty": 20, "cost": 100.0},
                         150.0, "USD", change_pct=2.0, fx_to_home=3.7)
        self.assertAlmostEqual(row["value"], 3000.0)
        self.assertAlmostEqual(row["pnl"], 1000.0)
        self.assertAlmostEqual(row["pnl_pct"], 50.0)
        self.assertAlmostEqual(row["value_home"], 11100.0)
        self.assertAlmostEqual(row["day"], 3000.0 - 3000.0 / 1.02)

    def test_position_ils_needs_no_fx(self):
        row = P.position({"symbol": "TEVA.TA", "qty": 300, "cost": 52.4},
                         135.8, "ILS")
        self.assertAlmostEqual(row["value_home"], 300 * 135.8)

    def test_missing_price_or_fx(self):
        no_price = P.position({"symbol": "A", "qty": 1, "cost": 1}, None, "USD", fx_to_home=3.7)
        self.assertIsNone(no_price["value"])
        no_fx = P.position({"symbol": "B", "qty": 1, "cost": 1}, 10.0, "USD")
        self.assertEqual(no_fx["value"], 10.0)
        self.assertIsNone(no_fx["value_home"])

    def test_summarize(self):
        rows = [
            P.position({"symbol": "NVDA", "qty": 10, "cost": 100.0}, 150.0, "USD",
                       change_pct=0.0, fx_to_home=4.0),          # 6000 ₪, cost 4000
            P.position({"symbol": "TEVA.TA", "qty": 100, "cost": 50.0}, 40.0, "ILS",
                       change_pct=0.0),                          # 4000 ₪, cost 5000
            P.position({"symbol": "GONE", "qty": 1, "cost": 1.0}, None, "USD"),
        ]
        t = P.summarize(rows)
        self.assertAlmostEqual(t["value"], 10000.0)
        self.assertAlmostEqual(t["cost"], 9000.0)
        self.assertAlmostEqual(t["pnl"], 1000.0)
        self.assertAlmostEqual(t["pnl_pct"], 100 * (10000 / 9000 - 1))
        self.assertAlmostEqual(t["day"], 0.0)
        self.assertEqual(t["missing"], ["GONE"])
        self.assertEqual(t["count"], 3)

    def test_summarize_empty(self):
        t = P.summarize([])
        self.assertIsNone(t["value"])
        self.assertEqual(t["count"], 0)


class TestAppFlow(unittest.TestCase):
    """Drive the REAL app (AppTest) on the offline sample data in dev/:
    open a stock, add it to the portfolio, see it on the home screen."""

    @classmethod
    def setUpClass(cls):
        os.environ["STOCKAPP_DISABLE_BROWSER_STORAGE"] = "1"
        os.environ["STOCKAPP_DISABLE_PASSWORD"] = "1"
        os.environ["STOCKAPP_DISABLE_GEMINI"] = "1"

    def _app(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "dev", "demo_app.py"),
                               default_timeout=60)
        at.run()
        self.assertFalse(at.exception, at.exception)
        return at

    def _md(self, at):
        return "\n".join(m.value for m in at.markdown)

    def test_add_holding_then_home_shows_it(self):
        at = self._app()
        self.assertIn("עוד אין מניות בתיק", "\n".join(c.value for c in at.caption))
        at.text_input(key="search_box").set_value("TEVA.TA").run()
        self.assertFalse(at.exception, at.exception)
        # The sample TEVA price is 68.90 ILS; the cost field defaults to it.
        self.assertAlmostEqual(at.number_input(key="pf_cost_TEVA.TA").value, 68.90)
        at.number_input(key="pf_qty_TEVA.TA").set_value(10)
        at.number_input(key="pf_cost_TEVA.TA").set_value(60.0)
        at.button(key="FormSubmitter:pf_form_TEVA.TA-שמור").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.session_state["portfolio"],
                         [{"symbol": "TEVA.TA", "qty": 10.0, "cost": 60.0}])
        self.assertIn("נשמר בתיק", "\n".join(x.value for x in at.success))
        # Back home: the portfolio card shows 10 x 68.90 = ₪689 and the row.
        at.button(key="back_home").click().run()
        md = self._md(at)
        self.assertIn("שווי התיק", md)
        self.assertIn("₪689", md)
        labels = [b.label for b in at.button]
        self.assertTrue(any("10" in l and "מניות" in l for l in labels), labels)

    def test_remove_holding(self):
        at = self._app()
        at.session_state["portfolio"] = [{"symbol": "TEVA.TA", "qty": 5.0, "cost": 50.0}]
        at.text_input(key="search_box").set_value("TEVA.TA").run()
        at.button(key="pf_rm_TEVA.TA").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.session_state["portfolio"], [])


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
