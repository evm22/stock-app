"""
test_buy_score.py -- offline tests for buy_score.py, news.py (parsing) and
gemini_helper.parse_news_rating. No network, no Gemini.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import buy_score as BS
import engine
import gemini_helper
import news


class TestParts(unittest.TestCase):
    def test_engine_part(self):
        self.assertEqual(BS.engine_part(50), 0)
        self.assertEqual(BS.engine_part(75), 50)
        self.assertEqual(BS.engine_part(100), 100)
        self.assertIsNone(BS.engine_part(None))

    def test_analyst_part(self):
        self.assertAlmostEqual(BS.analyst_part(3.0, 0.0), 0.0)
        self.assertAlmostEqual(BS.analyst_part(1.0, 25.0), 100.0)        # both max
        self.assertAlmostEqual(BS.analyst_part(2.0, 10.0), 0.6 * 50 + 0.4 * 40)
        self.assertAlmostEqual(BS.analyst_part(2.0, 300.0), 0.6 * 50 + 0.4 * 100)  # clamped
        self.assertAlmostEqual(BS.analyst_part(None, -10.0), -40.0)       # only target
        self.assertIsNone(BS.analyst_part(None, None))


class TestCombine(unittest.TestCase):
    def test_all_three_parts(self):
        r = BS.combine(engine=56, analysts=70, ai=40)   # mockup NVDA numbers
        self.assertEqual(r["score"], round(0.5 * 56 + 0.3 * 70 + 0.2 * 40))
        self.assertEqual(r["label"], "נוטה לקנייה")
        self.assertEqual(r["missing"], [])
        self.assertIn("50% × +56", r["formula"])

    def test_missing_parts_are_reweighted(self):
        r = BS.combine(engine=40, analysts=None, ai=None)
        self.assertEqual(r["score"], 40)                 # engine alone = 100%
        self.assertEqual(r["missing"], ["analysts", "ai"])
        r2 = BS.combine(engine=40, analysts=-20)
        self.assertEqual(r2["score"], round((0.5 * 40 + 0.3 * -20) / 0.8))
        used = {p["key"]: p["used_weight"] for p in r2["parts"]}
        self.assertAlmostEqual(used["engine"], 0.625)
        self.assertAlmostEqual(used["ai"], 0.0)

    def test_labels_and_empty(self):
        self.assertEqual(BS.combine(engine=-40)["label"], "נוטה למכירה")
        self.assertEqual(BS.combine(engine=10)["label"], "ניטרלי")
        self.assertIsNone(BS.combine()["score"])

    def test_from_engine_objects(self):
        hv = engine.HorizonVerdict("1Y", label="Buy", score=78.0, enough_data=True)
        v = engine.Verdict(found=True, symbol="X", horizons={"1Y": hv})
        a = engine.AnalystConsensus(found=True, symbol="X", has_coverage=True,
                                    mean=1.5, upside_pct=20.0)
        r = BS.from_engine_objects(v, a, ai_score=None)
        self.assertEqual(r["parts"][0]["value"], 56)
        self.assertEqual(r["parts"][1]["value"], round(0.6 * 75 + 0.4 * 80))
        no_cov = engine.AnalystConsensus(found=True, symbol="X", has_coverage=False)
        self.assertEqual(BS.from_engine_objects(v, no_cov)["missing"], ["analysts", "ai"])


RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Nvidia beats estimates - Reuters</title><link>https://x/1</link>
<pubDate>Tue, 10 Nov 2026 06:00:00 GMT</pubDate><source url="r">Reuters</source></item>
<item><title>Second headline</title><link>https://x/2</link>
<pubDate>bad date</pubDate></item>
<item><title></title></item>
</channel></rss>"""


class TestNews(unittest.TestCase):
    def test_parse_rss(self):
        items = news.parse_rss(RSS)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Nvidia beats estimates")   # " - Reuters" dropped
        self.assertEqual(items[0]["source"], "Reuters")
        self.assertEqual(items[0]["published"], "2026-11-10T06:00:00+00:00")
        self.assertIsNone(items[1]["published"])
        self.assertEqual(news.parse_rss(b"not xml"), [])

    def test_google_query(self):
        self.assertEqual(news.google_query("NVDA", "NVIDIA Corporation")[0], "NVIDIA stock when:7d")
        q = news.google_query("TEVA.TA", "Teva Pharmaceutical Industries Limited")
        self.assertEqual((q[0], q[1]), ("Teva Pharmaceutical Industries מניה when:7d", "iw"))

    def test_age_he(self):
        now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(news.age_he("2026-11-10T09:00:00+00:00", now), "לפני 3 שעות")
        self.assertEqual(news.age_he("2026-11-09T10:00:00+00:00", now), "אתמול")
        self.assertEqual(news.age_he("2026-11-06T10:00:00+00:00", now), "לפני 4 ימים")
        self.assertEqual(news.age_he(None, now), "")


class TestGeminiParsing(unittest.TestCase):
    def test_parse_news_rating(self):
        self.assertEqual(gemini_helper.parse_news_rating('{"score": 40, "reason": "חיובי"}'),
                         (40, "חיובי"))
        fenced = '```json\n{"score": -250, "reason": "x"}\n```'
        self.assertEqual(gemini_helper.parse_news_rating(fenced), (-100, "x"))
        self.assertIsNone(gemini_helper.parse_news_rating("no json here"))
        self.assertIsNone(gemini_helper.parse_news_rating('{"score": "abc"}'))
        self.assertIsNone(gemini_helper.parse_news_rating(""))

    def test_rate_news_without_key_is_none(self):
        self.assertIsNone(gemini_helper.rate_news("X", "X", [{"title": "t"}], None))
        self.assertIsNone(gemini_helper.rate_news("X", "X", [], "key"))


class TestScoreInApp(unittest.TestCase):
    """The score card in the REAL app on the offline sample data (dev/)."""

    @classmethod
    def setUpClass(cls):
        os.environ["STOCKAPP_DISABLE_BROWSER_STORAGE"] = "1"
        os.environ["STOCKAPP_DISABLE_PASSWORD"] = "1"
        os.environ["STOCKAPP_DISABLE_GEMINI"] = "1"

    def test_stock_page_shows_score_and_calculation(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "dev", "demo_app.py"),
                               default_timeout=60)
        at.run()
        at.text_input(key="search_box").set_value("NVDA").run()
        self.assertFalse(at.exception, at.exception)
        md = "\n".join(m.value for m in at.markdown)
        self.assertIn("ציון קנייה", md)
        self.assertIn("חדשות", md)
        self.assertIn("sample headline 1", md)
        formulas = [c.value for c in at.code]
        self.assertTrue(any(f.startswith("6") and "=" in f for f in formulas), formulas)
        # No Gemini key -> the AI button is there but disabled; AI part missing.
        btn = at.button(key="ai_news_NVDA")
        self.assertTrue(btn.disabled)
        # Follow it -> the home row carries the score tag.
        at.button(key="wl_toggle").click().run()
        at.button(key="back_home").click().run()
        labels = [b.label for b in at.button]
        self.assertTrue(any("ציון" in l for l in labels), labels)
        # Tapping the row opens that exact stock (no "which one?" picker).
        at.button(key="wl_view_NVDA").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(len(at.selectbox), 0)
        self.assertIn("ציון קנייה", "\n".join(m.value for m in at.markdown))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
