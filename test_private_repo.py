"""
test_private_repo.py -- mockup item 22: data in a private GitHub repo + a
password. Offline: the "repo" is a temp folder (data_store's read/write are
redirected there), so no network and no token.
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["STOCKAPP_DISABLE_GEMINI"] = "1"
os.environ["STOCKAPP_DISABLE_BROWSER_STORAGE"] = "1"
os.environ["STOCKAPP_DISABLE_PASSWORD"] = "1"

import data_store  # noqa: E402
import portfolio as P  # noqa: E402


def tx(sym, side, qty, price, when, id_=None):
    t = {"symbol": sym, "side": side, "qty": qty, "price": price,
         "currency": "USD", "date": when}
    if id_:
        t["id"] = id_
    return t


class TestMerge(unittest.TestCase):
    def test_into_empty_repo(self):
        new, n = P.merge_trades(P.empty(), {"transactions": [tx("AAPL", "buy", 5, 200, "2026-05-05")]})
        self.assertEqual(n, 1)
        self.assertEqual(new["transactions"][0]["symbol"], "AAPL")

    def test_same_trades_not_doubled(self):
        a = {"transactions": [tx("AAPL", "buy", 5, 200, "2026-05-05", "x1")]}
        b = {"transactions": [tx("aapl", "buy", 5.0, 200.0, "2026-05-05", "other"),
                              tx("MSFT", "buy", 1, 400, "2026-06-01", "x1")]}   # same id, new trade
        new, n = P.merge_trades(a, b)
        self.assertEqual(n, 1)
        self.assertEqual(len(new["transactions"]), 2)
        self.assertEqual(len({t["id"] for t in new["transactions"]}), 2)
        again, n2 = P.merge_trades(new, b)
        self.assertEqual((n2, len(again["transactions"])), (0, 2))

    def test_bad_result_raises(self):
        with self.assertRaises(P.PortfolioError):   # a sale bigger than the holding
            P.merge_trades(P.empty(), {"transactions": [tx("A", "sell", 5, 1, "2026-01-01")]})

    def test_watchlists(self):
        self.assertEqual(P.merge_watchlists(["MSFT", "TEVA.TA"], ["teva.ta", "AAPL", " "]),
                         ["MSFT", "TEVA.TA", "AAPL"])


class _FakeRepo:
    """data_store with a 'repo' that is really a temp folder."""

    def __init__(self, folder):
        self.folder = folder
        self.orig = data_store.read_text, data_store.write_text

    def __enter__(self):
        folder, (rd, wr) = self.folder, self.orig
        data_store.read_text = lambda path, **kw: rd(path, local_dir=folder)
        data_store.write_text = lambda path, text, message="", **kw: wr(path, text, local_dir=folder)
        return self

    def __exit__(self, *exc):
        data_store.read_text, data_store.write_text = self.orig

    def read(self, path):
        with open(os.path.join(self.folder, path), encoding="utf-8") as fh:
            return json.load(fh)


class TestInApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _app(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(HERE, "dev", "demo_app.py"), default_timeout=60)
        at.secrets["DATA_REPO"] = "someone/stock-app-data"
        at.secrets["DATA_REPO_TOKEN"] = "not-a-real-token"
        return at

    def test_browser_data_moves_into_repo_once(self):
        with _FakeRepo(self.tmp.name) as repo:
            at = self._app()
            at.session_state["_test_browser_trades"] = json.dumps(
                {"version": 1, "transactions": [tx("AAPL", "buy", 10, 200, "2026-05-05")]})
            at.session_state["_test_browser_watchlist"] = json.dumps(["MSFT"])
            at.run()
            self.assertFalse(at.exception, at.exception)
            self.assertIn("העברתי מהדפדפן לקובץ הפרטי 1 עסקאות ו-1",
                          "\n".join(m.value for m in at.success))
            self.assertEqual([t["symbol"] for t in repo.read("data/portfolio.json")["transactions"]],
                             ["AAPL"])
            self.assertEqual(repo.read("data/watchlist.json"), ["MSFT"])
            self.assertIn("נשמרים אוטומטית בקובץ פרטי", "\n".join(c.value for c in at.caption))
            # The watchlist is saved to the repo when it changes.
            at.text_input(key="search_box").set_value("NVDA").run()
            at.button(key="wl_toggle").click().run()
            self.assertEqual(repo.read("data/watchlist.json"), ["MSFT", "NVDA"])
            # A second device with the same trades doesn't double them.
            at2 = self._app()
            at2.session_state["_test_browser_trades"] = json.dumps(
                {"version": 1, "transactions": [tx("AAPL", "buy", 10, 200, "2026-05-05")]})
            at2.run()
            self.assertFalse(at2.exception, at2.exception)
            self.assertEqual(len(repo.read("data/portfolio.json")["transactions"]), 1)
            self.assertEqual(at2.session_state["watchlist"], ["MSFT", "NVDA"])


class TestPassword(unittest.TestCase):
    def test_remember_token(self):
        import app   # bare mode, like test_app.py
        self.assertEqual(app.remember_token("abc"), app.remember_token("abc"))
        self.assertNotEqual(app.remember_token("abc"), app.remember_token("abd"))
        self.assertNotIn("abc", app.remember_token("abc"))

    def test_lock_screen(self):
        from streamlit.testing.v1 import AppTest
        os.environ.pop("STOCKAPP_DISABLE_PASSWORD", None)
        try:
            at = AppTest.from_file(os.path.join(HERE, "dev", "demo_app.py"), default_timeout=60)
            at.secrets["APP_PASSWORD"] = "שלום123"
            at.run()
            self.assertFalse(at.exception, at.exception)
            self.assertIn("כניסה", "\n".join(m.value for m in at.markdown))
            self.assertEqual(len(at.text_input), 1)          # nothing else is shown
            at.text_input(key="app_password_input").set_value("wrong")
            at.button[0].click().run()
            self.assertIn("הסיסמה לא נכונה", "\n".join(e.value for e in at.error))
            at.text_input(key="app_password_input").set_value("שלום123")
            at.button[0].click().run()
            self.assertFalse(at.exception, at.exception)
            self.assertTrue(at.session_state["auth_ok"])
            self.assertTrue(any(b.key == "forget_device" for b in at.button))
            at.button(key="forget_device").click().run()
            self.assertIn("יצאת", "\n".join(i.value for i in at.info))
            self.assertEqual(len(at.text_input), 1)          # locked again
        finally:
            os.environ["STOCKAPP_DISABLE_PASSWORD"] = "1"


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
