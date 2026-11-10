"""
test_manual.py -- holdings with a value typed by hand (Israeli funds that
have no price in the free data). Offline, no network.
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

import portfolio as P  # noqa: E402

TODAY = date(2026, 11, 10)


class TestManual(unittest.TestCase):
    def test_add_update_remove(self):
        d = P.add_manual(P.empty(), "הראל מחקה ת״א 35", 23675.62, 26143.36, today=TODAY)
        m = d["manual"][0]
        self.assertEqual((m["name"], m["updated"]), ("הראל מחקה ת״א 35", "2026-11-10"))
        with self.assertRaises(P.PortfolioError):
            P.add_manual(d, "הראל מחקה ת״א 35", 1, 1)            # same name twice
        with self.assertRaises(P.PortfolioError):
            P.add_manual(d, "  ", 1, 1)                           # no name
        d2 = P.update_manual(d, m["id"], 24000, today=date(2026, 11, 20))
        self.assertEqual((d2["manual"][0]["value"], d2["manual"][0]["cost"]), (24000, 26143.36))
        self.assertEqual(d["manual"][0]["value"], 23675.62)      # the input isn't changed
        self.assertNotIn("manual", P.remove_manual(d2, m["id"]))

    def test_row_counts_in_totals(self):
        d = P.add_manual(P.empty(), "קרן", 1200, 1000)
        r = P.manual_row(d["manual"][0])
        self.assertAlmostEqual(r["pnl_pct"], 20.0)
        self.assertTrue(P.is_manual(r["symbol"]))
        stock = P.position({"symbol": "A", "qty": 1, "cost": 100}, 150, "ILS")
        t = P.summarize([stock, r])
        self.assertEqual((t["value"], t["cost"], t["missing"]), (1350, 1100, []))

    def test_trades_keep_manual_and_backup_roundtrip(self):
        d = P.add_manual(P.empty(), "קרן", 1200, 1000)
        d = P.add_transaction(d, "AAPL", "buy", 1, 200, "USD", "2026-05-05", today=TODAY)
        d = P.remove_transaction(d, d["transactions"][0]["id"])
        self.assertEqual(len(d["manual"]), 1)                    # trades don't drop it
        back, _ = P.parse_backup(P.make_backup(d, []))
        self.assertEqual(back["manual"][0]["name"], "קרן")
        self.assertEqual(P.clean_data(back), back)

    def test_merge_brings_manual_once(self):
        a = P.add_manual(P.empty(), "קרן", 1200, 1000)
        new, n = P.merge_trades(P.empty(), a)
        self.assertEqual((n, len(new["manual"])), (1, 1))
        self.assertEqual(P.merge_trades(new, a)[1], 0)


class TestInApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["STOCKAPP_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("STOCKAPP_DATA_DIR", None)
        self.tmp.cleanup()

    def test_add_and_update_on_home(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(HERE, "dev", "demo_app.py"), default_timeout=60)
        at.run()
        at.text_input(key="manual_new_name").set_value("הראל מחקה ת״א 35")
        at.number_input(key="manual_new_value").set_value(1200.0)
        at.number_input(key="manual_new_cost").set_value(1000.0)
        [b for b in at.button if b.label == "הוסף לתיק"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        labels = [b.label for b in at.button if b.key and b.key.startswith("pf_manual_")]
        self.assertEqual(len(labels), 1)
        self.assertIn("+20.0%", labels[0])
        self.assertIn("₪1,200", "\n".join(m.value for m in at.markdown))   # in the total
        at.button(key=labels and [b.key for b in at.button
                                  if b.key and b.key.startswith("pf_manual_")][0]).click().run()
        pick = at.session_state["manual_pick"]
        at.number_input(key=f"manual_value_{pick}").set_value(1500.0)
        [b for b in at.button if b.label == "שמור עדכון"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertIn("₪1,500", "\n".join(m.value for m in at.markdown))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
