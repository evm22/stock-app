"""
test_portfolio.py -- offline tests for portfolio.py (trades, holdings, P/L,
tax estimate, backup/restore).

Run:  python test_portfolio.py
"""

import json
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(__file__))

import portfolio as pf

TODAY = date(2026, 11, 10)


def build(*trades):
    data = pf.empty()
    for sym, side, qty, price, when, *cur in trades:
        data = pf.add_transaction(data, sym, side, qty, price, (cur or ["USD"])[0], when, today=TODAY)
    return data


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


class TestPricesAndValue(unittest.TestCase):
    def test_agorot_become_shekels(self):
        self.assertEqual(pf.normalize_price(6890, "ILA"), (68.9, "ILS"))
        self.assertEqual(pf.normalize_price(100, "usd"), (100, "USD"))
        self.assertEqual(pf.normalize_price(None, "ILA"), (None, "ILA"))

    def test_value_positions(self):
        data = build(("NVDA", "buy", 10, 100, "2026-01-05"),
                     ("TEVA.TA", "buy", 100, 60, "2026-01-05", "ILS"))
        res = pf.compute(data)
        prices = {"NVDA": (110.0, "USD", 10.0), "TEVA.TA": (6900.0, "ILA", 0.0)}
        rows, tot = pf.value_positions(res, prices, {"USD": 3.7})
        by = {r["symbol"]: r for r in rows}
        self.assertAlmostEqual(by["NVDA"]["value"], 1100)
        self.assertAlmostEqual(by["NVDA"]["pl_pct"], 10)
        self.assertAlmostEqual(by["TEVA.TA"]["price"], 69.0)
        self.assertEqual(by["TEVA.TA"]["currency"], "ILS")
        self.assertAlmostEqual(tot["value_ils"], 1100 * 3.7 + 6900)
        self.assertAlmostEqual(tot["cost_ils"], 1000 * 3.7 + 6000)
        self.assertAlmostEqual(tot["day_ils"], (1100 - 1000) * 3.7)
        self.assertEqual(tot["missing"], [])

    def test_missing_rate_or_price_left_out_of_total(self):
        data = build(("NVDA", "buy", 10, 100, "2026-01-05"), ("Q", "buy", 1, 5, "2026-01-05"))
        rows, tot = pf.value_positions(pf.compute(data), {"NVDA": (110.0, "USD", None)}, {})
        self.assertEqual(sorted(tot["missing"]), ["NVDA", "Q"])
        self.assertEqual(tot["value_ils"], 0)
        self.assertEqual(len(rows), 2)


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
    unittest.main()
