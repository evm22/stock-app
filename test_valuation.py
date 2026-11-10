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


class TestGroups(unittest.TestCase):
    def test_grouped_and_allocation(self):
        rows = [P.position({"symbol": "MSFT", "qty": 1, "cost": 100}, 100, "USD", 0, 3.0),
                P.position({"symbol": "QQQ", "qty": 1, "cost": 100}, 200, "USD", 0, 3.0),
                P.manual_row(fund())]
        names = {"MSFT": "Microsoft", "QQQ": "Invesco QQQ Trust", "manual:f1": "קרן"}
        g = V.grouped(rows, names)
        self.assertEqual([(k, len(r), round(t)) for k, _, r, t in g],
                         [("stock", 1, 300), ("etf", 1, 600), ("fund", 1, 1000)])
        a = V.allocation(rows, names)
        self.assertEqual([n for n, _ in a["kind"]], ["מניות", "קרנות סל", "קרנות (שווי ידני)"])
        self.assertAlmostEqual(sum(p for _, p in a["kind"]), 100)
        self.assertEqual(a["currency"][0][0], "שקל")          # 1000 of 1900
        self.assertEqual(V.allocation([], {}), {"kind": [], "currency": []})
        self.assertEqual(V.kind_of({}, "Vanguard S&P 500 ETF"), "etf")
        self.assertEqual(V.kind_of({}, "NVIDIA"), "stock")


class TestHomeCost(unittest.TestCase):
    RATES = {"2026-01-05": 3.70, "2026-06-01": 3.50, "2026-09-01": 3.20}

    def fx_on(self, cur, d):
        return V.rate_on(sorted(self.RATES.items()), d)

    def test_cost_and_sale_in_shekels(self):
        d = P.add_transaction(P.empty(), "A", "buy", 10, 100, "USD", "2026-01-05", today=TODAY)
        d = P.add_transaction(d, "A", "buy", 10, 100, "USD", "2026-06-01", today=TODAY)
        d = P.add_transaction(d, "A", "sell", 10, 110, "USD", "2026-09-01", today=TODAY)
        cost, sales = P.home_costs(d, self.fx_on)
        self.assertAlmostEqual(cost["A"], (3700 + 3500) / 2)        # half the ₪ basis left
        self.assertEqual(len(sales), 1)
        self.assertAlmostEqual(sales[0][1], 10 * 110 * 3.20 - 3600)  # a $ gain, a ₪ loss
        self.assertEqual(P.home_costs(d, lambda c, x: None)[0], {})  # unknown rate
        ils = P.add_transaction(P.empty(), "B.TA", "buy", 2, 50, "ILS", "2026-01-05", today=TODAY)
        self.assertEqual(P.home_costs(ils, lambda c, x: None)[0], {"B.TA": 100.0})

    def test_rows_use_buy_day_rate(self):
        d = P.add_transaction(P.empty(), "A", "buy", 10, 100, "USD", "2026-01-05", today=TODAY)
        rows, _ = V.value_rows(d, lambda s: (100.0, 0.0, "USD", "A"), lambda c: 3.0,
                               fx_on=self.fx_on)
        self.assertAlmostEqual(rows[0]["cost_home"], 3700)
        self.assertAlmostEqual(P.summarize(rows)["pnl"], 3000 - 3700)   # the dollar fell


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

    def fx_history(self, s):
        return self.history(s)

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
