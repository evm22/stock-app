"""
test_scan.py -- offline tests for scan.py row-building / JSON-writing logic.

No network calls: engine.compute_verdict and engine.get_analyst_consensus are
mocked with fake data for 2-3 fake tickers.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is on the path.
sys.path.insert(0, os.path.dirname(__file__))

import engine
from scan import _build_row, run_scan

# ---------------------------------------------------------------------------
# Helpers to build fake engine return values
# ---------------------------------------------------------------------------

def _fake_verdict(found=True, label_6m="Buy", label_1y="Hold", label_5y="Strong Buy"):
    if not found:
        v = engine.Verdict(found=False, symbol="FAKE")
        return v
    hv6 = engine.HorizonVerdict("6M", label=label_6m, score=60.0, enough_data=True)
    hv1 = engine.HorizonVerdict("1Y", label=label_1y, score=50.0, enough_data=True)
    hv5 = engine.HorizonVerdict("5Y", label=label_5y, score=80.0, enough_data=True)
    return engine.Verdict(
        found=True, symbol="FAKE",
        horizons={"6M": hv6, "1Y": hv1, "5Y": hv5},
        signals=[],
    )


def _fake_analyst(found=True, has_coverage=True, target_mean=150.0, upside=10.0, price=136.0):
    if not found:
        return engine.AnalystConsensus(found=False, symbol="FAKE")
    return engine.AnalystConsensus(
        found=found, symbol="FAKE",
        has_coverage=has_coverage,
        current_price=price,
        target_mean=target_mean if has_coverage else None,
        upside_pct=upside if has_coverage else None,
    )


def _fake_technicals(found=True, beta=1.2, high52=160.0):
    mg = MagicMock()
    mg.found = found
    if found:
        def _metric(val):
            m = MagicMock()
            m.available = val is not None
            m.value = val
            return m
        mg.metrics = {
            "beta":       _metric(beta),
            "week52_high": _metric(high52),
        }
    else:
        mg.metrics = {}
    return mg


def _fake_company(found=True, debt_to_equity=80.0):
    mg = MagicMock()
    mg.found = found
    if found:
        def _metric(val):
            m = MagicMock()
            m.available = val is not None
            m.value = val
            return m
        mg.metrics = {"debt_to_equity": _metric(debt_to_equity)}
    else:
        mg.metrics = {}
    return mg


def _fake_quote(found=True, price=136.0):
    q = MagicMock(spec=engine.StockQuote)
    q.found = found
    q.price = price
    return q


AS_OF = "2026-06-28T12:00:00+00:00"

ENTRY_A = {"symbol": "AAAA", "regions": ["US"], "themes": ["ai"]}
ENTRY_B = {"symbol": "BBBB", "regions": ["IL"], "themes": ["israel"]}
ENTRY_C = {"symbol": "CCCC", "regions": ["US"], "themes": ["core"]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRowSchema(unittest.TestCase):
    """The row dict has the correct keys regardless of data quality."""

    EXPECTED_KEYS = {
        "symbol", "regions", "themes",
        "score_6m", "score_1y", "score_5y",
        "current_price",
        "analyst_mean_target", "analyst_implied_upside_pct",
        "risk", "data_status", "as_of",
    }
    EXPECTED_RISK_KEYS = {"beta", "pct_below_52w_high", "debt_to_equity"}

    def _patch_all(self, verdict, analyst, technicals, company, quote):
        return [
            patch("scan.engine.compute_verdict",      return_value=verdict),
            patch("scan.engine.get_analyst_consensus", return_value=analyst),
            patch("scan.engine.get_stock_technicals",  return_value=technicals),
            patch("scan.engine.get_company_metrics",   return_value=company),
            patch("scan.engine.get_stock_quote",       return_value=quote),
        ]

    def test_ok_row_has_all_keys(self):
        verdict    = _fake_verdict()
        analyst    = _fake_analyst()
        technicals = _fake_technicals()
        company    = _fake_company()
        quote      = _fake_quote()
        patches = self._patch_all(verdict, analyst, technicals, company, quote)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            row = _build_row(ENTRY_A, AS_OF)
        self.assertEqual(set(row.keys()), self.EXPECTED_KEYS)
        self.assertEqual(set(row["risk"].keys()), self.EXPECTED_RISK_KEYS)

    def test_ok_row_values(self):
        verdict    = _fake_verdict(label_6m="Buy", label_1y="Hold", label_5y="Strong Buy")
        analyst    = _fake_analyst(target_mean=150.0, upside=10.0, price=136.0)
        technicals = _fake_technicals(beta=1.2, high52=160.0)
        company    = _fake_company(debt_to_equity=80.0)
        quote      = _fake_quote(price=136.0)
        patches = self._patch_all(verdict, analyst, technicals, company, quote)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            row = _build_row(ENTRY_A, AS_OF)

        self.assertEqual(row["symbol"],   "AAAA")
        self.assertEqual(row["score_6m"], "Buy")
        self.assertEqual(row["score_1y"], "Hold")
        self.assertEqual(row["score_5y"], "Strong Buy")
        self.assertAlmostEqual(row["current_price"], 136.0)
        self.assertAlmostEqual(row["analyst_mean_target"], 150.0)
        self.assertAlmostEqual(row["analyst_implied_upside_pct"], 10.0)
        self.assertAlmostEqual(row["risk"]["beta"], 1.2)
        self.assertAlmostEqual(row["risk"]["debt_to_equity"], 80.0)
        pct = (160.0 - 136.0) / 160.0 * 100
        self.assertAlmostEqual(row["risk"]["pct_below_52w_high"], pct, places=4)
        self.assertEqual(row["data_status"], "ok")
        self.assertEqual(row["as_of"], AS_OF)

    def test_partial_when_no_analyst_target(self):
        verdict    = _fake_verdict()
        analyst    = _fake_analyst(has_coverage=False)
        technicals = _fake_technicals()
        company    = _fake_company()
        quote      = _fake_quote()
        patches = self._patch_all(verdict, analyst, technicals, company, quote)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            row = _build_row(ENTRY_B, AS_OF)
        self.assertEqual(row["data_status"], "partial")
        self.assertIsNone(row["analyst_mean_target"])
        self.assertIsNone(row["analyst_implied_upside_pct"])

    def test_failed_when_verdict_not_found(self):
        verdict    = _fake_verdict(found=False)
        analyst    = _fake_analyst(found=False)
        technicals = _fake_technicals(found=False)
        company    = _fake_company(found=False)
        quote      = _fake_quote(found=False)
        patches = self._patch_all(verdict, analyst, technicals, company, quote)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            row = _build_row(ENTRY_C, AS_OF)
        self.assertEqual(row["data_status"], "failed")
        self.assertIsNone(row["score_6m"])
        self.assertIsNone(row["score_1y"])
        self.assertIsNone(row["score_5y"])


class TestBatchBehavior(unittest.TestCase):
    """One failing ticker must not abort the batch; JSON written correctly."""

    def _make_ok_mocks(self, symbol):
        verdict    = _fake_verdict()
        verdict.symbol = symbol
        analyst    = _fake_analyst()
        technicals = _fake_technicals()
        company    = _fake_company()
        quote      = _fake_quote()
        return verdict, analyst, technicals, company, quote

    def test_failing_ticker_does_not_abort_batch(self):
        universe = [
            {"symbol": "GOOD1", "regions": ["US"], "themes": ["core"]},
            {"symbol": "BAD",   "regions": ["US"], "themes": ["ai"]},
            {"symbol": "GOOD2", "regions": ["US"], "themes": ["core"]},
        ]

        call_counts = {"n": 0}

        def mock_compute_verdict(symbol):
            call_counts["n"] += 1
            if symbol == "BAD":
                raise RuntimeError("Simulated fetch failure")
            v = _fake_verdict()
            v.symbol = symbol
            return v

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            out_path = tmp.name

        try:
            with patch("scan.engine.compute_verdict", side_effect=mock_compute_verdict), \
                 patch("scan.engine.get_analyst_consensus", return_value=_fake_analyst()), \
                 patch("scan.engine.get_stock_technicals",  return_value=_fake_technicals()), \
                 patch("scan.engine.get_company_metrics",   return_value=_fake_company()), \
                 patch("scan.engine.get_stock_quote",       return_value=_fake_quote()), \
                 patch("scan.SLEEP_BETWEEN", 0), \
                 patch("scan.RETRY_DELAYS", [0, 0]):
                run_scan(universe, out_path=out_path)

            with open(out_path, encoding="utf-8") as fh:
                data = json.load(fh)

            self.assertEqual(data["universe_size"], 3)
            self.assertEqual(len(data["rows"]), 3)

            statuses = {r["symbol"]: r["data_status"] for r in data["rows"]}
            self.assertEqual(statuses["GOOD1"], "ok")
            self.assertEqual(statuses["BAD"],   "failed")
            self.assertEqual(statuses["GOOD2"], "ok")
        finally:
            os.unlink(out_path)

    def test_json_output_structure(self):
        universe = [ENTRY_A, ENTRY_B]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            out_path = tmp.name

        try:
            with patch("scan.engine.compute_verdict",      return_value=_fake_verdict()), \
                 patch("scan.engine.get_analyst_consensus", return_value=_fake_analyst()), \
                 patch("scan.engine.get_stock_technicals",  return_value=_fake_technicals()), \
                 patch("scan.engine.get_company_metrics",   return_value=_fake_company()), \
                 patch("scan.engine.get_stock_quote",       return_value=_fake_quote()), \
                 patch("scan.SLEEP_BETWEEN", 0), \
                 patch("scan.RETRY_DELAYS", [0, 0]):
                run_scan(universe, out_path=out_path)

            with open(out_path, encoding="utf-8") as fh:
                data = json.load(fh)

            self.assertIn("generated_at",  data)
            self.assertIn("universe_size", data)
            self.assertIn("rows",          data)
            self.assertEqual(data["universe_size"], 2)
            self.assertEqual(len(data["rows"]), 2)
        finally:
            os.unlink(out_path)


if __name__ == "__main__":
    ok = True
    loader = unittest.TestLoader()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(loader.loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
