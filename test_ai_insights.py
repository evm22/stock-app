"""
test_ai_insights.py -- offline tests for ai_insights.py, the daily job's AI
picks (no Gemini: the code fallback), and both screens in the real app on
the dev/ sample data (AppTest). No network.
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

import ai_insights as AI  # noqa: E402
import daily_job  # noqa: E402
import gemini_helper  # noqa: E402


def prow(sym, value_home, currency="USD", pnl=1.0):
    return {"symbol": sym, "value_home": value_home, "currency": currency, "pnl": pnl}


def scan_row(sym, s6, s5, upside=10.0, status="ok", themes=("core",)):
    return {"symbol": sym, "themes": list(themes), "regions": ["US"],
            "score_6m": "Buy", "score_1y": "Buy", "score_5y": "Buy",
            "score_6m_num": s6, "score_1y_num": 60.0, "score_5y_num": s5,
            "analyst_implied_upside_pct": upside,
            "risk": {"beta": 1.0, "debt_to_equity": 50.0}, "data_status": status}


class TestFacts(unittest.TestCase):
    def test_portfolio_facts(self):
        rows = [prow("NVDA", 6000), prow("TEVA.TA", 3000, "ILS", pnl=-5), prow("X", None)]
        f = AI.portfolio_facts(rows, {"NVDA": 50, "TEVA.TA": 20}, {"NVDA": "Nvidia"})
        self.assertAlmostEqual(f["total"], 9000)
        self.assertEqual(f["top"][0], "NVDA")
        self.assertAlmostEqual(f["top"][2], 66.666, places=2)
        self.assertAlmostEqual(f["il_pct"], 33.333, places=2)
        self.assertEqual(f["avg_score"], round((6000 * 50 + 3000 * 20) / 9000))
        self.assertEqual((f["winners"], f["losers"]), (1, 1))
        payload = AI.facts_for_ai(f)
        self.assertEqual(payload["weights_pct"][0], {"stock": "Nvidia", "symbol": "NVDA", "pct": 66.7})
        self.assertEqual(AI.signature(f), (("NVDA", 67), ("TEVA.TA", 33)))

    def test_empty(self):
        self.assertIsNone(AI.portfolio_facts([])["avg_score"])
        self.assertEqual(AI.portfolio_facts([prow("A", None)])["weights"], [])


class TestPicks(unittest.TestCase):
    ROWS = [scan_row(f"S{i}", 50 + i, 80 - i) for i in range(15)] + [
        scan_row("BAD", 99, 99, status="partial")]

    def test_candidates_by_horizon_and_ok_only(self):
        c = AI.pick_candidates(self.ROWS, n=3)
        self.assertEqual([x["symbol"] for x in c["short"]], ["S14", "S13", "S12"])
        self.assertEqual([x["symbol"] for x in c["long"]], ["S0", "S1", "S2"])
        self.assertNotIn("BAD", [x["symbol"] for x in c["short"] + c["long"]])

    def test_clean_picks_keeps_only_candidates(self):
        c = AI.pick_candidates(self.ROWS, n=3)
        raw = {"short": [{"symbol": "s14", "why": "טוב"}, {"symbol": "NOPE", "why": "x"},
                         {"symbol": "S13", "why": ""}, {"symbol": "S14", "why": "dup"}, "junk"],
               "long": [{"symbol": "S0", "why": "א"}, {"symbol": "S1", "why": "ב"},
                        {"symbol": "S2", "why": "ג"}, {"symbol": "S0", "why": "ד"}]}
        p = AI.clean_picks(raw, c)
        self.assertEqual(p["short"], [{"symbol": "S14", "why": "טוב"}])
        self.assertEqual([x["symbol"] for x in p["long"]], ["S0", "S1", "S2"])
        self.assertEqual(AI.clean_picks(None, c), {"short": [], "long": []})

    def test_fallback(self):
        p = AI.fallback_picks(AI.pick_candidates(self.ROWS, n=3))
        self.assertEqual([x["symbol"] for x in p["short"]], ["S14", "S13"])
        self.assertIn("בלי AI", p["short"][0]["why"])

    def test_daily_job_without_key_uses_code(self):
        p = daily_job.make_ai_picks(self.ROWS, "2026-11-10", api_key=None)
        self.assertEqual(p["source"], "code")
        lines = daily_job.picks_lines(p)
        self.assertIn("🔎 שווה לבדוק (לפי הקוד):", lines)
        self.assertTrue(any(l.startswith("• S14") for l in lines))

    def test_json_parsing(self):
        self.assertEqual(gemini_helper.parse_json_object('```json\n{"a": [1]}\n```'), {"a": [1]})
        self.assertIsNone(gemini_helper.parse_json_object("[1,2]"))
        self.assertIsNone(gemini_helper.parse_json_object(""))
        self.assertIsNone(gemini_helper.portfolio_overview({}, None))
        self.assertIsNone(gemini_helper.pick_recommendations({}, None))


class TestScreens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["STOCKAPP_DATA_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("STOCKAPP_DATA_DIR", None)
        self.tmp.cleanup()

    def _app(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(HERE, "dev", "demo_app.py"), default_timeout=60)
        at.run()
        self.assertFalse(at.exception, at.exception)
        return at

    def test_overview_card_on_home(self):
        at = self._app()
        at.text_input(key="search_box").set_value("NVDA").run()
        at.segmented_control(key="trade_side_NVDA").set_value("קנייה")
        at.number_input(key="trade_qty_NVDA").set_value(5)
        [b for b in at.button if b.label == "שמור עסקה"][0].click().run()
        at.button(key="back_home").click().run()
        self.assertFalse(at.exception, at.exception)
        md = "\n".join(m.value for m in at.markdown)
        self.assertIn("מבט על התיק", md)
        self.assertIn("במניה הגדולה", md)
        self.assertIn("100%", md)                       # one stock = 100% of it
        self.assertTrue(at.button(key="ai_overview_btn").disabled)   # no key in tests

    def test_ai_tab_without_and_with_picks(self):
        at = self._app()
        at.segmented_control(key="home_tab").set_value("ה-AI ממליץ").run()
        self.assertIn("בסריקה היומית", "\n".join(i.value for i in at.info))
        os.makedirs(os.path.join(self.tmp.name, "data"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "data", "ai_picks.json"), "w", encoding="utf-8") as fh:
            json.dump({"day": "2026-11-10", "source": "ai",
                       "short": [{"symbol": "NVDA", "why": "מומנטום חזק"}],
                       "long": [{"symbol": "AAPL", "why": "עסק יציב"}]}, fh, ensure_ascii=False)
        at.run()
        self.assertFalse(at.exception, at.exception)
        caps = "\n".join(c.value for c in at.caption)
        self.assertIn("מומנטום חזק", caps)
        self.assertIn("ה-AI בחר", caps)
        at.button(key="pick_short_NVDA").click().run()      # opens the stock
        self.assertIn("ציון קנייה", "\n".join(m.value for m in at.markdown))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
