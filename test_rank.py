"""
test_rank.py -- offline tests for rank.py (Stage 2b) and the scan.py
checkpoint/resume + numeric-score additions.  No network.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

import rank
import scan
from test_scan import (_fake_verdict, _fake_analyst, _fake_technicals,
                       _fake_company, _fake_quote)


def _row(symbol, s1y=60.0, label="Buy", upside=20.0, beta=1.0, d2e=50.0,
         status="ok", themes=("core",), regions=("US",)):
    return {
        "symbol": symbol, "themes": list(themes), "regions": list(regions),
        "score_6m": label, "score_1y": label, "score_5y": label,
        "score_num": {"6m": s1y, "1y": s1y, "5y": s1y},
        "current_price": 100.0,
        "analyst_mean_target": 100 + (upside or 0),
        "analyst_implied_upside_pct": upside,
        "risk": {"beta": beta, "pct_below_52w_high": 5.0,
                 "debt_to_equity": d2e},
        "data_status": status,
    }


class TestScoring(unittest.TestCase):

    def test_upside_points_mapping_and_clamp(self):
        self.assertAlmostEqual(rank.upside_points(-20), 0)
        self.assertAlmostEqual(rank.upside_points(15), 50)
        self.assertAlmostEqual(rank.upside_points(50), 100)
        self.assertAlmostEqual(rank.upside_points(300), 100)   # clamped
        self.assertAlmostEqual(rank.upside_points(-90), 0)     # clamped
        self.assertIsNone(rank.upside_points(None))

    def test_blend(self):
        e = rank.score_row(_row("X", s1y=70, upside=15))
        self.assertAlmostEqual(e["opportunity"], 0.6 * 70 + 0.4 * 50)
        self.assertEqual(e["risk_penalty"], 0)

    def test_no_analyst_uses_neutral_upside(self):
        e = rank.score_row(_row("X", s1y=70, upside=None))
        neutral = rank.upside_points(rank.NEUTRAL_UPSIDE)
        self.assertAlmostEqual(e["opportunity"], round(0.6 * 70 + 0.4 * neutral, 1))
        self.assertIsNone(e["upside_pct"])
        self.assertTrue(any("no analyst" in n for n in e["notes"]))

    def test_risk_penalty(self):
        pts, why = rank.risk_penalty(_row("X", beta=2.0, d2e=300)
                                     )
        self.assertAlmostEqual(pts, 5 + 5)
        self.assertEqual(len(why), 2)
        pts, _ = rank.risk_penalty(_row("X", beta=9.0, d2e=50))
        self.assertAlmostEqual(pts, rank.BETA_PENALTY_MAX)

    def test_label_fallback_for_old_files(self):
        r = _row("X", label="Strong Buy")
        del r["score_num"]
        self.assertAlmostEqual(rank.our_score(r, "1y"), 87.5)

    def test_failed_or_unscored_rows_skipped(self):
        self.assertIsNone(rank.score_row(_row("X", status="failed")))
        r = _row("Y")
        r["score_num"]["1y"] = None
        r["score_1y"] = None
        self.assertIsNone(rank.score_row(r))

    def test_agree_flag(self):
        self.assertTrue(rank.score_row(_row("X", label="Buy", upside=12))["agree"])
        self.assertFalse(rank.score_row(_row("X", label="Hold", upside=40))["agree"])
        self.assertFalse(rank.score_row(_row("X", label="Buy", upside=5))["agree"])


class TestRankRows(unittest.TestCase):

    ROWS = [
        _row("LOW",  s1y=40, upside=0),
        _row("HIGH", s1y=80, upside=30, themes=("ai",)),
        _row("MID",  s1y=60, upside=15, regions=("IL",), themes=("israel",)),
        _row("RISKY", s1y=80, upside=30, beta=2.5),
        _row("PART", s1y=60, upside=None, status="partial"),
        _row("DEAD", status="failed"),
    ]

    def test_sorted_best_first_with_ranks(self):
        out = rank.rank_rows(self.ROWS)
        syms = [e["symbol"] for e in out]
        self.assertEqual(syms[0], "HIGH")
        self.assertNotIn("DEAD", syms)
        self.assertEqual([e["rank"] for e in out], list(range(1, len(out) + 1)))
        opps = [e["opportunity"] for e in out]
        self.assertEqual(opps, sorted(opps, reverse=True))

    def test_risky_ranks_below_identical_safe_stock(self):
        syms = [e["symbol"] for e in rank.rank_rows(self.ROWS)]
        self.assertLess(syms.index("HIGH"), syms.index("RISKY"))

    def test_filters(self):
        f = lambda **kw: [e["symbol"] for e in rank.rank_rows(self.ROWS, **kw)]
        self.assertEqual(f(theme="ai"), ["HIGH"])
        self.assertEqual(f(region="IL"), ["MID"])
        self.assertNotIn("RISKY", f(max_beta=2.0))
        self.assertNotIn("PART", f(include_partial=False))
        self.assertNotIn("PART", f(min_upside=10))
        self.assertNotIn("LOW", f(min_upside=10))
        self.assertTrue(all(s in ("HIGH", "MID", "RISKY") for s in f(agree_only=True)))
        self.assertEqual(len(f(top=2)), 2)

    def test_bad_horizon(self):
        with self.assertRaises(ValueError):
            rank.rank_rows(self.ROWS, horizon="10y")


class TestScanAdditions(unittest.TestCase):
    """score_num in rows, checkpoint writes, and --resume."""

    def _patches(self, verdict_fn=None):
        return [
            patch("scan.engine.compute_verdict",
                  side_effect=verdict_fn or (lambda s: _fake_verdict())),
            patch("scan.engine.get_analyst_consensus", return_value=_fake_analyst()),
            patch("scan.engine.get_stock_technicals",  return_value=_fake_technicals()),
            patch("scan.engine.get_company_metrics",   return_value=_fake_company()),
            patch("scan.engine.get_stock_quote",       return_value=_fake_quote()),
            patch("scan.SLEEP_BETWEEN", 0),
            patch("scan.RETRY_DELAYS", [0, 0]),
            patch("scan.CHECKPOINT_EVERY", 2),
        ]

    def _run(self, universe, path, resume=False, verdict_fn=None):
        ps = self._patches(verdict_fn)
        for p in ps:
            p.start()
        try:
            scan.run_scan(universe, out_path=path, resume=resume)
        finally:
            for p in ps:
                p.stop()

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.universe = [{"symbol": f"T{i}", "regions": ["US"], "themes": ["core"]}
                         for i in range(5)]

    def tearDown(self):
        for p in (self.path, self.path + ".tmp"):
            if os.path.exists(p):
                os.unlink(p)

    def test_rows_carry_numeric_scores_and_complete_flag(self):
        self._run(self.universe, self.path)
        with open(self.path) as fh:
            data = json.load(fh)
        self.assertTrue(data["complete"])
        self.assertEqual(data["rows"][0]["score_num"],
                         {"6m": 60.0, "1y": 50.0, "5y": 80.0})

    def test_resume_skips_already_scored(self):
        # Simulate an interrupted run: checkpoint with the first 2 rows.
        self._run(self.universe[:2], self.path)
        with open(self.path) as fh:
            data = json.load(fh)
        data["complete"] = False
        data["universe_size"] = 5
        with open(self.path, "w") as fh:
            json.dump(data, fh)

        called = []
        def vfn(sym):
            called.append(sym)
            return _fake_verdict()
        self._run(self.universe, self.path, resume=True, verdict_fn=vfn)

        self.assertEqual(called, ["T2", "T3", "T4"])
        with open(self.path) as fh:
            final = json.load(fh)
        self.assertTrue(final["complete"])
        self.assertEqual([r["symbol"] for r in final["rows"]],
                         [f"T{i}" for i in range(5)])

    def test_resume_ignores_completed_file(self):
        self._run(self.universe[:2], self.path)   # complete=True
        called = []
        def vfn(sym):
            called.append(sym)
            return _fake_verdict()
        self._run(self.universe, self.path, resume=True, verdict_fn=vfn)
        self.assertEqual(len(called), 5)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
