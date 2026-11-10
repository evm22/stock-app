"""
test_valuation.py -- valuation.py (portfolio value shared by the app and the
morning message) and the portfolio part of daily_job. Offline, no network.
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["STOCKAPP_DISABLE_GEMINI"] = "1"

import data_store  # noqa: E402
import daily_job  # noqa: E402
import portfolio as P  # noqa: E402
import valuation as V  # noqa: E402

TODAY = date(2026, 11, 20)


def fund(name="הראל מחקה ת״א 35", value=1000.0, cost=800.0, updated="2026-11-10", **kw):
    return {"id": "f1", "name": name, "value": value, "cost": cost, "updated": updated, **kw}


class TestIndexMove(unittest.TestCase):
    CLOSES = {"TA35.TA": [("2026-11-09", 98.0), ("2026-11-10", 100.0), ("2026-11-19", 108.0)]}

    def test_value_follows_the_index_since_the_update(self):
        f, day = V.index_move(fund(), lambda s: (110.0, 2.0), self.CLOSES.get, TODAY)
        self.assertAlmostEqual(f, 1.10)
        self.assertEqual(day, 2.0)
        r = P.manual_row(fund(), f, day)
        self.assertAlmostEqual(r["value"], 1100.0)
        self.assertTrue(r["estimated"])
        self.assertAlmostEqual(r["day_home"], 1100 - 1100 / 1.02)

    def test_update_day_and_no_index(self):
        f, _ = V.index_move(fund(updated=TODAY.isoformat()), lambda s: (110.0, 2.0),
                            self.CLOSES.get, TODAY)
        self.assertEqual(f, 1.0)                                  # typed today: as typed
        f, day = V.index_move(fund(tracks=""), lambda s: (110.0, 2.0), self.CLOSES.get, TODAY)
        self.assertEqual((f, day), (1.0, None))
        self.assertFalse(P.manual_row(fund(), 1.0, None)["estimated"])

    def test_several_symbols_average(self):
        m = fund(tracks="A,B")
        closes = {"A": [("2026-11-10", 100.0)], "B": [("2026-11-10", 50.0)]}
        f, day = V.index_move(m, lambda s: {"A": (120.0, 1.0), "B": (50.0, -1.0)}[s],
                              closes.get, TODAY)
        self.assertAlmostEqual(f, 1.10)
        self.assertAlmostEqual(day, 0.0)


class TestRowsAndMessage(unittest.TestCase):
    def setUp(self):
        d = P.add_transaction(P.empty(), "MSFT", "buy", 10, 400, "USD", "2026-05-01", today=TODAY)
        d["manual"] = [fund()]
        self.data = d
        self.quotes = {"MSFT": (500.0, 1.0, "USD", "Microsoft"), "TA35.TA": (110.0, 0.5, "ILS", "TA35")}

    def rows(self):
        return V.value_rows(self.data, self.quotes.get, lambda c: 3.0 if c == "USD" else 1.0,
                            lambda s: [("2026-11-10", 100.0)], TODAY)

    def test_value_rows(self):
        rows, names = self.rows()
        self.assertEqual([r["symbol"] for r in rows], ["MSFT", "manual:f1"])
        self.assertAlmostEqual(P.summarize(rows)["value"], 10 * 500 * 3 + 1100)
        self.assertEqual(names["MSFT"], "Microsoft")

    def test_symbols_to_score(self):
        self.assertEqual(V.symbols_to_score(self.data, ["NVDA", "msft"]), ["MSFT", "TA35.TA", "NVDA"])

    def test_telegram_lines(self):
        rows, names = self.rows()
        week = [{"when": "מחר", "symbol": "MSFT", "what": "דוח רבעוני"}]
        changes = V.score_changes({"MSFT": 10}, {"MSFT": 40}, names)
        lines = V.telegram_lines(rows, names, week, changes)
        text = "\n".join(lines)
        self.assertIn("₪16,100", text)                       # 15,000 + 1,100
        self.assertIn("ביום המסחר האחרון", text)
        self.assertIn("Microsoft +1.0%", text)
        self.assertIn("📅 מחר: Microsoft – דוח רבעוני", text)
        self.assertIn("+10 ← +40", text)
        self.assertEqual(V.score_changes({"A": 10}, {"A": 20}), [])   # under 15 points
        self.assertEqual(V.telegram_lines([], {}), [])
        self.assertEqual(V.short_name("Eli Lilly and Company"), "Eli Lilly")
        self.assertEqual(V.short_name("JPMorgan Chase & Co."), "JPMorgan Chase")
        self.assertEqual(V.short_name("NVIDIA Corporation"), "NVIDIA")


class FakeFetch:
    def quote(self, s):
        prices = {"MSFT": (500.0, 1.0, "USD"), "TA35.TA": (110.0, 0.5, "ILS"),
                  "ILS=X": (3.0, 0.0, "ILS")}
        p = prices.get(s)
        return SimpleNamespace(found=bool(p), price=p[0] if p else None,
                               change_pct=p[1] if p else None, currency=p[2] if p else "",
                               name=s)

    def verdict(self, s):
        return None

    def analyst(self, s):
        return SimpleNamespace(found=True, has_coverage=True, mean=2.0, upside_pct=10.0)

    def history(self, s):
        import pandas as pd
        return SimpleNamespace(found=True, data=pd.DataFrame(
            {"Date": pd.to_datetime(["2026-11-10"]), "Close": [100.0]}))

    def calendar(self, s):
        return {"earnings": "2026-11-21", "ex_dividend": None, "dividend_pay": None,
                "dividend_per_share": None}


class TestDailyJobPart(unittest.TestCase):
    def test_writes_scores_and_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(daily_job.portfolio_part(tmp, "2026-11-20", FakeFetch()), [])
            d = P.add_transaction(P.empty(), "MSFT", "buy", 10, 400, "USD", "2026-05-01",
                                  today=TODAY)
            d["manual"] = [fund()]
            data_store.write_json("data/portfolio.json", d, local_dir=tmp)
            lines = daily_job.portfolio_part(tmp, TODAY.isoformat(), FakeFetch())
            self.assertTrue(lines and lines[0].startswith("💼 התיק שלך"), lines)
            doc = data_store.read_json("data/holding_scores.json", local_dir=tmp)
            self.assertEqual(doc["date"], TODAY.isoformat())
            self.assertEqual(set(doc["scores"]), {"MSFT", "TA35.TA"})
            self.assertIsNotNone(doc["scores"]["MSFT"]["analysts"])
            self.assertIn("MSFT", doc["calendar"])


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
