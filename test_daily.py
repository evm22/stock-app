"""
test_daily.py -- offline tests for daily.py and notify.py.  No network:
Telegram sending is mocked, no scan is run.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

import daily
import notify
from test_rank import _row


def _ranked(symbols):
    return [{"symbol": s, "rank": i + 1, "opportunity": 70 - i,
             "our_label": "Buy", "upside_pct": 12.0, "agree": True,
             "regions": ["US"]} for i, s in enumerate(symbols)]


class TestNotify(unittest.TestCase):

    def test_split_respects_limit_and_keeps_text(self):
        text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
        parts = notify.split_message(text, limit=500)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        self.assertEqual("\n".join(parts), text)

    def test_split_short_text_single_part(self):
        self.assertEqual(notify.split_message("hi"), ["hi"])

    def test_credentials_from_secrets_file(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "secrets.toml")
            with open(p, "w") as fh:
                fh.write('TELEGRAM_TOKEN = "t"\nTELEGRAM_CHAT_ID = 42\n')
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(notify.load_credentials(p), ("t", "42"))
        finally:
            shutil.rmtree(d)

    def test_credentials_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(notify.load_credentials("/nonexistent.toml"),
                             (None, None))

    def test_send_without_credentials_returns_false(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("notify.SECRETS_PATH", "/nonexistent.toml"), \
             patch("notify.urllib.request.urlopen") as uo:
            self.assertFalse(notify.send_telegram("x"))
            uo.assert_not_called()


class TestDaily(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_diff_top(self):
        prev = {"ranked": _ranked(["A", "B", "C"])}
        d = daily.diff_top(_ranked(["B", "D", "A"]), prev, n=3)
        self.assertEqual(d["entered"], ["D"])
        self.assertEqual(d["left"], ["C"])
        self.assertTrue(d["has_previous"])
        self.assertFalse(daily.diff_top(_ranked(["A"]), None)["has_previous"])

    def test_previous_snapshot_is_strictly_earlier(self):
        for day in ("2026-11-08", "2026-11-09", "2026-11-10"):
            daily.save_snapshot(_ranked([day]), {}, day, history_dir=self.dir)
        prev = daily.load_previous_snapshot("2026-11-10", history_dir=self.dir)
        self.assertEqual(prev["day"], "2026-11-09")
        self.assertIsNone(
            daily.load_previous_snapshot("2026-11-08", history_dir=self.dir))

    def test_message_contents(self):
        rows = [_row("AAA"), _row("TEVA.TA", regions=("IL",),
                                  themes=("israel",)),
                _row("BAD", status="failed")]
        data = {"rows": rows, "complete": True}
        ranked = daily.rank.rank_rows(rows)
        diff = daily.diff_top(ranked, {"ranked": _ranked(["OLD"])})
        msg = daily.build_message(data, ranked, diff, "2026-11-10")
        self.assertIn("10.11.2026", msg)
        self.assertIn("נסרקו 3 מניות", msg)
        self.assertIn("AAA", msg)
        self.assertIn("יצאו מהטופ: OLD", msg)
        self.assertIn("🇮🇱", msg)
        self.assertIn("TEVA.TA", msg)
        self.assertIn("לא ייעוץ השקעות", msg)

    def test_message_warns_on_incomplete_or_flaky_scan(self):
        rows = [_row("A", status="failed"), _row("B", status="failed"),
                _row("C")]
        msg = daily.build_message({"rows": rows, "complete": False},
                                  daily.rank.rank_rows(rows),
                                  {"entered": [], "left": [],
                                   "has_previous": False}, "2026-11-10")
        self.assertIn("הסריקה לא הושלמה", msg)
        self.assertIn("Yahoo", msg)

    def test_main_no_scan_sends_and_snapshots(self):
        results = os.path.join(self.dir, "screen_results.json")
        with open(results, "w") as fh:
            json.dump({"generated_at": "x", "universe_size": 2,
                       "complete": True, "rows": [_row("A"), _row("B")]}, fh)
        hist = os.path.join(self.dir, "history")
        with patch("daily.RESULTS_PATH", results), \
             patch("daily.HISTORY_DIR", hist), \
             patch("daily.run_scan_step") as scan_step, \
             patch("notify.send_telegram", return_value=True) as send:
            rc = daily.main(["--no-scan"])
        self.assertEqual(rc, 0)
        scan_step.assert_not_called()
        send.assert_called_once()
        self.assertEqual(len(os.listdir(hist)), 1)

    def test_seconds_until(self):
        from datetime import datetime
        now = datetime(2026, 11, 10, 8, 40, 0)
        self.assertEqual(daily.seconds_until("09:00", now), 20 * 60)
        late = datetime(2026, 11, 10, 9, 5, 0)
        self.assertEqual(daily.seconds_until("09:00", late), 0)

    def test_send_at_holds_until_time(self):
        results = os.path.join(self.dir, "screen_results.json")
        with open(results, "w") as fh:
            json.dump({"generated_at": "x", "universe_size": 1,
                       "complete": True, "rows": [_row("A")]}, fh)
        with patch("daily.RESULTS_PATH", results), \
             patch("daily.HISTORY_DIR", self.dir), \
             patch("daily.seconds_until", return_value=120.0), \
             patch("daily.time.sleep") as sleep, \
             patch("notify.send_telegram", return_value=True):
            daily.main(["--no-scan", "--send-at", "09:00"])
        sleep.assert_called_once_with(120.0)

    def test_main_missing_results_alerts(self):
        with patch("daily.RESULTS_PATH", os.path.join(self.dir, "none.json")), \
             patch("daily.HISTORY_DIR", self.dir), \
             patch("notify.send_telegram", return_value=True) as send:
            rc = daily.main(["--no-scan"])
        self.assertEqual(rc, 1)
        send.assert_called_once()


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
