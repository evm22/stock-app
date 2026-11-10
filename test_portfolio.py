"""
test_portfolio.py -- offline tests for portfolio.py (no network, no Streamlit):
units and money, holdings and valuation, trades, average cost, realized
profit, tax estimate, the move from the old browser portfolio, backup.

The screens are tested in test_portfolio_ui.py.

Run:  python test_portfolio.py
"""

import json
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(__file__))

import portfolio as P

pf = P  # the trade tests below use this name
TODAY = date(2026, 11, 10)


def build(*trades):
    data = pf.empty()
    for sym, side, qty, price, when, *cur in trades:
        data = pf.add_transaction(data, sym, side, qty, price, (cur or ["USD"])[0], when, today=TODAY)
    return data


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


class TestAdd(unittest.TestCase):
    def test_add_returns_new_dict(self):
        data = pf.empty()
        new = pf.add_transaction(data, " nvda ", "BUY", "10", "100", "usd", "2026-01-05", today=TODAY)
        self.assertEqual(data["transactions"], [])
        tx = new["transactions"][0]
        self.assertEqual((tx["symbol"], tx["side"], tx["qty"], tx["price"], tx["currency"]),
                         ("NVDA", "buy", 10.0, 100.0, "USD"))
        self.assertEqual(tx["date"], "2026-01-05")
        self.assertTrue(tx["id"])

    def test_rejects_bad_input(self):
        bad = [("", "buy", 1, 1, "2026-01-01"), ("X", "hold", 1, 1, "2026-01-01"),
               ("X", "buy", 0, 1, "2026-01-01"), ("X", "buy", 1, -5, "2026-01-01"),
               ("X", "buy", "a", 1, "2026-01-01"), ("X", "buy", 1, 1, "not-a-date"),
               ("X", "buy", 1, 1, "2026-12-01")]  # future
        for args in bad:
            with self.assertRaises(pf.PortfolioError, msg=args):
                pf.add_transaction(pf.empty(), *args[:4], "USD", args[4], today=TODAY)

    def test_cannot_sell_more_than_held(self):
        data = build(("AAPL", "buy", 5, 100, "2026-02-01"))
        with self.assertRaises(pf.PortfolioError):
            pf.add_transaction(data, "AAPL", "sell", 6, 120, "USD", "2026-03-01", today=TODAY)

    def test_cannot_sell_before_buying(self):
        data = build(("AAPL", "buy", 5, 100, "2026-02-01"))
        with self.assertRaises(pf.PortfolioError):
            pf.add_transaction(data, "AAPL", "sell", 1, 120, "USD", "2026-01-15", today=TODAY)

    def test_same_day_buy_then_sell(self):
        data = build(("AAPL", "buy", 5, 100, "2026-02-01"), ("AAPL", "sell", 5, 110, "2026-02-01"))
        res = pf.compute(data)
        self.assertNotIn("AAPL", res.holdings)
        self.assertAlmostEqual(res.realized[0].profit, 50.0)


class TestCompute(unittest.TestCase):
    def test_average_cost_and_realized(self):
        data = build(("NVDA", "buy", 10, 100, "2026-01-05"),
                     ("NVDA", "buy", 10, 200, "2026-02-05"),
                     ("NVDA", "sell", 5, 180, "2026-03-05"))
        res = pf.compute(data)
        h = res.holdings["NVDA"]
        self.assertAlmostEqual(h.qty, 15)
        self.assertAlmostEqual(h.avg_cost, 150)   # a sale doesn't change the average
        self.assertAlmostEqual(h.cost_basis, 2250)
        self.assertAlmostEqual(res.realized[0].profit, 150)  # (180-150)*5
        self.assertAlmostEqual(h.realized, 150)

    def test_order_independent_of_entry(self):
        a = build(("X", "buy", 1, 10, "2026-01-01"), ("X", "buy", 1, 20, "2026-02-01"))
        b = pf.empty()
        b = pf.add_transaction(b, "X", "buy", 1, 20, "USD", "2026-02-01", today=TODAY)
        b = pf.add_transaction(b, "X", "buy", 1, 10, "USD", "2026-01-01", today=TODAY)
        self.assertAlmostEqual(pf.compute(a).holdings["X"].avg_cost,
                               pf.compute(b).holdings["X"].avg_cost)

    def test_sold_out_is_not_a_holding(self):
        data = build(("X", "buy", 3, 10, "2026-01-01"), ("X", "sell", 3, 8, "2026-02-01"))
        res = pf.compute(data)
        self.assertEqual(res.holdings, {})
        self.assertAlmostEqual(res.realized[0].profit, -6)

    def test_remove_transaction(self):
        data = build(("X", "buy", 3, 10, "2026-01-01"), ("X", "buy", 2, 10, "2026-01-02"))
        first = data["transactions"][0]["id"]
        new = pf.remove_transaction(data, first)
        self.assertEqual(len(new["transactions"]), 1)
        self.assertEqual(len(data["transactions"]), 2)

    def test_remove_buy_that_a_sale_needs_is_refused(self):
        data = build(("X", "buy", 3, 10, "2026-01-01"), ("X", "sell", 3, 12, "2026-02-01"))
        with self.assertRaises(pf.PortfolioError):
            pf.remove_transaction(data, data["transactions"][0]["id"])


class TestYearAndTax(unittest.TestCase):
    def test_last_year_newest_first(self):
        data = build(("X", "buy", 1, 10, "2025-06-01"), ("X", "buy", 1, 10, "2026-01-01"),
                     ("X", "sell", 1, 12, "2026-05-01"))
        recent = pf.last_year(data["transactions"], today=TODAY)
        self.assertEqual([t["date"] for t in recent], ["2026-05-01", "2026-01-01"])

    def test_realized_in_year_nets_gains_and_losses_per_currency(self):
        data = build(("A", "buy", 1, 100, "2026-01-01"), ("A", "sell", 1, 150, "2026-02-01"),
                     ("B", "buy", 1, 100, "2026-01-01"), ("B", "sell", 1, 80, "2026-03-01"),
                     ("T", "buy", 10, 50, "2026-01-01", "ILS"), ("T", "sell", 10, 60, "2026-04-01", "ILS"),
                     ("C", "buy", 1, 10, "2025-01-01"), ("C", "sell", 1, 99, "2025-02-01"))
        out = pf.realized_in_year(pf.compute(data), 2026)
        self.assertAlmostEqual(out["USD"], 30)
        self.assertAlmostEqual(out["ILS"], 100)

    def test_tax_estimate(self):
        self.assertAlmostEqual(pf.tax_estimate(1000), 250)
        self.assertEqual(pf.tax_estimate(-500), 0)


class TestHoldingsFromTrades(unittest.TestCase):
    def test_as_holdings_feeds_position(self):
        data = build(("NVDA", "buy", 10, 100, "2026-01-05"), ("NVDA", "buy", 10, 200, "2026-02-05"),
                     ("AAPL", "buy", 1, 50, "2026-03-01"), ("AAPL", "sell", 1, 60, "2026-04-01"))
        hs = pf.as_holdings(pf.compute(data))
        self.assertEqual(hs, [{"symbol": "NVDA", "qty": 20.0, "cost": 150.0}])
        row = pf.position(hs[0], 180.0, "USD", None, 3.7)
        self.assertAlmostEqual(row["pnl"], 600)

    def test_currency_of(self):
        data = build(("TEVA.TA", "buy", 1, 60, "2026-01-05", "ILS"))
        self.assertEqual(pf.currency_of(data, "teva.ta"), "ILS")
        self.assertEqual(pf.currency_of(data, "X"), "")

    def test_from_old_browser_holdings(self):
        old = [{"symbol": "NVDA", "qty": 20.0, "cost": 118.0},
               {"symbol": "TEVA.TA", "qty": 300.0, "cost": 52.4},
               {"symbol": "FREE", "qty": 5.0, "cost": 0.0}]   # cost 0: can't be a buy
        data = build(("NVDA", "buy", 1, 100, "2026-01-05"))  # already has trades: left alone
        new = pf.from_holdings(data, old, {"TEVA.TA": "ILS", "NVDA": "USD"}, when=TODAY)
        added = new["transactions"][1:]
        self.assertEqual(len(added), 1)
        self.assertEqual((added[0]["symbol"], added[0]["qty"], added[0]["price"],
                          added[0]["currency"], added[0]["date"], added[0]["note"]),
                         ("TEVA.TA", 300.0, 52.4, "ILS", "2026-11-10", "opening"))
        hs = {h["symbol"]: h for h in pf.as_holdings(pf.compute(new))}
        self.assertAlmostEqual(hs["TEVA.TA"]["cost"], 52.4)


class TestBackup(unittest.TestCase):
    def test_round_trip(self):
        data = build(("NVDA", "buy", 10, 100, "2026-01-05"), ("NVDA", "sell", 2, 120, "2026-02-05"))
        text = pf.make_backup(data, ["NVDA", "aapl"], now=datetime(2026, 11, 10, 9, 0))
        back, wl = pf.parse_backup(text)
        self.assertEqual(back["transactions"], data["transactions"])
        self.assertEqual(wl, ["NVDA", "AAPL"])
        self.assertEqual(json.loads(text)["created"], "2026-11-10T09:00:00")

    def test_rejects_other_files(self):
        for text in ["not json", "[]", json.dumps({"kind": "other"}),
                     json.dumps({"kind": pf.BACKUP_KIND, "portfolio": {"transactions": [{"symbol": "X"}]}})]:
            with self.assertRaises(pf.PortfolioError, msg=text):
                pf.parse_backup(text)

    def test_rejects_impossible_trades(self):
        doc = {"kind": pf.BACKUP_KIND, "portfolio": {"transactions": [
            {"symbol": "X", "side": "sell", "qty": 1, "price": 1, "date": "2026-01-01"}]}}
        with self.assertRaises(pf.PortfolioError):
            pf.parse_backup(json.dumps(doc))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
