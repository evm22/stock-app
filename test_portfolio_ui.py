"""
test_portfolio_ui.py -- the portfolio screens, run offline with Streamlit's
AppTest on the FAKE sample data (dev/demo_data.py) and a temp data folder.

Run:  python test_portfolio_ui.py
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["STOCKAPP_DISABLE_BROWSER_STORAGE"] = "1"
os.environ["STOCKAPP_DISABLE_PASSWORD"] = "1"
os.environ["STOCKAPP_DISABLE_GEMINI"] = "1"

from streamlit.testing.v1 import AppTest  # noqa: E402

import portfolio  # noqa: E402

DEMO = os.path.join(HERE, "dev", "demo_app.py")


def texts(at):
    out = [m.value for m in at.markdown] + [c.value for c in at.caption]
    out += [b.label for b in at.button] + [e.value for e in at.error]
    joined = "\n".join(str(x) for x in out)
    return joined.replace("&#36;", "$").replace("\\$", "$")


class TestPortfolioUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["STOCKAPP_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("STOCKAPP_DATA_DIR", None)
        self.tmp.cleanup()

    def saved(self):
        path = os.path.join(self.tmp.name, "data", "portfolio.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def app(self):
        at = AppTest.from_file(DEMO, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, at.exception)
        return at

    def open_stock(self, at, symbol):
        at.text_input(key="search_box").set_value(symbol).run()
        self.assertFalse(at.exception, at.exception)

    def log_trade(self, at, symbol, side, qty, price=None):
        at.segmented_control(key=f"trade_side_{symbol}").set_value(side)
        at.number_input(key=f"trade_qty_{symbol}").set_value(qty)
        if price is not None:
            at.number_input(key=f"trade_price_{symbol}").set_value(price)
        [b for b in at.button if b.label == "שמור עסקה"][0].click().run()
        self.assertFalse(at.exception, at.exception)

    def test_empty_home(self):
        at = self.app()
        self.assertIn("עוד אין מניות בתיק", texts(at))

    def test_buy_shows_on_home_and_is_saved(self):
        at = self.app()
        self.open_stock(at, "NVDA")
        self.log_trade(at, "NVDA", "קנייה", 10, 150.0)
        tx = self.saved()["transactions"]
        self.assertEqual((tx[0]["symbol"], tx[0]["qty"], tx[0]["price"], tx[0]["currency"]),
                         ("NVDA", 10.0, 150.0, "USD"))
        self.assertIn("בתיק שלי", texts(at))
        at.button(key="back_home").click().run()
        page = texts(at)
        self.assertIn("שווי התיק", page)
        self.assertIn("**NVIDIA**", page)               # short name in the row
        # 10 x $182.40 x 3.72 = ₪6,785
        self.assertIn("₪6,785", page)

    def test_tel_aviv_price_in_shekels(self):
        at = self.app()
        self.open_stock(at, "TEVA.TA")
        self.assertIn("₪68.90", texts(at))
        self.assertAlmostEqual(at.number_input(key="trade_price_TEVA.TA").value, 68.9)
        self.log_trade(at, "TEVA.TA", "קנייה", 100)
        self.assertEqual(self.saved()["transactions"][0]["currency"], "ILS")

    def test_selling_too_much_is_refused(self):
        at = self.app()
        self.open_stock(at, "AAPL")
        self.log_trade(at, "AAPL", "קנייה", 2, 200.0)
        self.log_trade(at, "AAPL", "מכירה", 5, 230.0)
        self.assertIn("אי אפשר למכור", texts(at))
        self.assertEqual(len(self.saved()["transactions"]), 1)

    def test_sale_shows_realized_and_tax(self):
        at = self.app()
        self.open_stock(at, "AAPL")
        self.log_trade(at, "AAPL", "קנייה", 10, 200.0)
        self.log_trade(at, "AAPL", "מכירה", 4, 250.0)
        at.button(key="back_home").click().run()
        page = texts(at)
        self.assertIn("+$200.00", page)   # (250-200) x 4
        self.assertIn("$50.00", page)     # 25% of it

    def test_old_browser_portfolio_is_moved_once(self):
        at = AppTest.from_file(DEMO, default_timeout=60)
        at.session_state["_test_browser_portfolio"] = (
            '[{"symbol": "TEVA.TA", "qty": 10, "cost": 60}]')
        at.run()
        self.assertFalse(at.exception, at.exception)
        tx = self.saved()["transactions"]
        self.assertEqual((tx[0]["symbol"], tx[0]["qty"], tx[0]["price"], tx[0]["currency"],
                          tx[0]["note"]), ("TEVA.TA", 10.0, 60.0, "ILS", "opening"))
        page = texts(at) + "\n".join(s.value for s in at.success)
        self.assertIn("העברתי 1 מניות", page)
        self.assertIn("₪689", page)  # 10 x ₪68.90
        at.run()                      # a second run doesn't add it again
        self.assertEqual(len(self.saved()["transactions"]), 1)

    def test_backup_text_paste_restore_and_reminder(self):
        at = self.app()
        self.open_stock(at, "AAPL")
        self.log_trade(at, "AAPL", "קנייה", 3, 200.0)
        at.button(key="back_home").click().run()
        # Data + never backed up -> the reminder shows on the home screen.
        self.assertIn("כדאי לשמור גיבוי", "\n".join(w.value for w in at.warning))
        # The backup text block holds the trade; paste it back after a delete.
        text = at.code[0].value
        restored, _ = portfolio.parse_backup(text)
        self.assertEqual(restored["transactions"][0]["symbol"], "AAPL")
        with open(os.path.join(self.tmp.name, "data", "portfolio.json"), "w") as fh:
            json.dump(portfolio.empty(), fh)
        at.session_state["portfolio"] = portfolio.empty()
        at.text_area(key="restore_text").set_value(text).run()
        at.button(key="backup_restore_btn").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(len(self.saved()["transactions"]), 1)
        # A restore counts as a backup -> the reminder is gone.
        at.run()
        self.assertNotIn("כדאי לשמור גיבוי", "\n".join(w.value for w in at.warning))

    def test_garbage_paste_shows_error(self):
        at = self.app()
        at.text_area(key="restore_text").set_value("not a backup").run()
        self.assertTrue(at.error, "expected an error for a bad backup text")


class TestBrowserBackend(unittest.TestCase):
    """With no data repo and no STOCKAPP_DATA_DIR (= the live site today), the
    trades must NOT go to a server folder (shared by every visitor and wiped on
    redeploy); they stay with this browser session / device."""

    def test_trade_is_not_written_on_the_server(self):
        os.environ.pop("STOCKAPP_DATA_DIR", None)
        server_file = os.path.join(HERE, "local_data", "data", "portfolio.json")
        before = os.path.exists(server_file) and os.path.getmtime(server_file)
        at = AppTest.from_file(DEMO, default_timeout=60)
        at.run()
        at.text_input(key="search_box").set_value("AAPL").run()
        at.segmented_control(key="trade_side_AAPL").set_value("קנייה")
        at.number_input(key="trade_qty_AAPL").set_value(2)
        [b for b in at.button if b.label == "שמור עסקה"][0].click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(len(at.session_state["portfolio"]["transactions"]), 1)
        after = os.path.exists(server_file) and os.path.getmtime(server_file)
        self.assertEqual(before, after, "trades were written to a server folder")


if __name__ == "__main__":
    unittest.main()
