"""
test_daily_job.py -- offline tests for daily_job.py (no network, no Yahoo).

A fake 3-stock scan stands in for the real one, and everything is written to a
temp folder.

Run:  python test_daily_job.py
"""

import csv
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

import daily_job


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def fake_scan(prices):
    """Return a scan function that writes a tiny results file like scan.run_scan."""
    def _scan(universe, out_path):
        rows = []
        for e in universe:
            sym = e["symbol"]
            rows.append({"symbol": sym, "score_6m_num": 61.25, "score_1y_num": 55.0,
                         "score_5y_num": None, "score_1y": "Buy",
                         "current_price": prices.get(sym),
                         "data_status": "failed" if prices.get(sym) is None else "ok"})
        result = {"generated_at": "x", "universe_size": len(rows), "rows": rows}
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        return result
    return _scan


UNIVERSE = [{"symbol": "AAA"}, {"symbol": "BBB.TA"}, {"symbol": "CCC"}]


class TestTimeGuard(unittest.TestCase):
    # Winter (Israel = UTC+2): the 04:00 UTC trigger is 06:00 Israel -> skip,
    # the 05:00 UTC trigger is 07:00 Israel -> run.
    def test_winter_early_trigger_skips(self):
        go, why = daily_job.should_run(utc(2026, 1, 15, 4), None)
        self.assertFalse(go)
        self.assertIn("early", why)

    def test_winter_late_trigger_runs(self):
        self.assertTrue(daily_job.should_run(utc(2026, 1, 15, 5), None)[0])

    # Summer (Israel = UTC+3): 04:00 UTC is 07:00 Israel -> run; the 05:00 UTC
    # trigger then finds today's run already done -> skip.
    def test_summer_first_trigger_runs(self):
        self.assertTrue(daily_job.should_run(utc(2026, 7, 15, 4), "2026-07-14")[0])

    def test_summer_second_trigger_skips_same_day(self):
        go, why = daily_job.should_run(utc(2026, 7, 15, 5), "2026-07-15")
        self.assertFalse(go)
        self.assertIn("already", why)

    def test_late_start_still_runs(self):
        # GitHub started the job an hour and a half late: still today's run.
        self.assertTrue(daily_job.should_run(utc(2026, 1, 15, 6, 30), "2026-01-14")[0])

    def test_force_always_runs(self):
        self.assertTrue(daily_job.should_run(utc(2026, 1, 15, 1), "2026-01-15", force=True)[0])

    def test_israel_date_not_utc_date(self):
        # 22:30 UTC on Jan 14 is already Jan 15 (00:30) in Israel.
        self.assertEqual(daily_job.israel_now(utc(2026, 1, 14, 22, 30)).date().isoformat(), "2026-01-15")


class TestHistory(unittest.TestCase):
    def _rows(self, text):
        return list(csv.DictReader(io.StringIO(text)))

    def test_new_file_gets_header_and_rows(self):
        text = daily_job.merge_history(None, "2026-11-10", [{"symbol": "AAA", "score_1y_num": 55.0,
                                                               "current_price": 10.5, "data_status": "ok"}])
        self.assertTrue(text.startswith(",".join(daily_job.HISTORY_FIELDS)))
        rows = self._rows(text)
        self.assertEqual(rows[0]["symbol"], "AAA")
        self.assertEqual(rows[0]["score_1y_num"], "55")
        self.assertEqual(rows[0]["price"], "10.5")
        self.assertEqual(rows[0]["score_5y_num"], "")

    def test_past_days_kept_and_today_replaced(self):
        day1 = daily_job.merge_history(None, "2026-11-09", [{"symbol": "AAA", "current_price": 1.0}])
        day2 = daily_job.merge_history(day1, "2026-11-10", [{"symbol": "AAA", "current_price": 2.0}])
        rerun = daily_job.merge_history(day2, "2026-11-10", [{"symbol": "AAA", "current_price": 3.0}])
        rows = self._rows(rerun)
        self.assertEqual([(r["date"], r["price"]) for r in rows],
                         [("2026-11-09", "1"), ("2026-11-10", "3")])


class TestSummary(unittest.TestCase):
    def test_mostly_ok_is_green(self):
        line = daily_job.summary_line({"total": 10, "ok": 9, "partial": 0, "failed": 1}, "2026-11-10")
        self.assertTrue(line.startswith("✅"))

    def test_mostly_failed_is_a_warning(self):
        line = daily_job.summary_line({"total": 10, "ok": 2, "partial": 0, "failed": 8}, "2026-11-10")
        self.assertTrue(line.startswith("⚠️"))


class TestRun(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)

    def _run(self, now, prices, force=False):
        return daily_job.run(self.dir, now_utc=now, force=force,
                             scan_fn=fake_scan(prices), universe=UNIVERSE)

    def test_full_run_writes_all_files(self):
        last = self._run(utc(2026, 11, 10, 6), {"AAA": 10.0, "BBB.TA": 20.0})
        self.assertEqual(last["date"], "2026-11-10")
        self.assertEqual((last["total"], last["ok"], last["failed"]), (3, 2, 1))
        for name in ("screen_results.json", "score_history.csv", "last_run.json", "last_summary.txt"):
            self.assertTrue(os.path.exists(os.path.join(self.dir, "data", name)), name)
        with open(os.path.join(self.dir, "data", "last_summary.txt"), encoding="utf-8") as fh:
            self.assertIn("3 מניות", fh.read())

    def test_second_run_same_day_is_skipped(self):
        self._run(utc(2026, 11, 10, 6), {"AAA": 10.0})
        self.assertIsNone(self._run(utc(2026, 11, 10, 7), {"AAA": 99.0}))

    def test_forced_rerun_does_not_duplicate_today(self):
        self._run(utc(2026, 11, 10, 6), {"AAA": 10.0})
        self._run(utc(2026, 11, 10, 7), {"AAA": 11.0}, force=True)
        with open(os.path.join(self.dir, "data", "score_history.csv"), encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 3)  # 3 stocks, once
        self.assertEqual(rows[0]["price"], "11")

    def test_next_day_appends(self):
        self._run(utc(2026, 11, 10, 6), {"AAA": 10.0})
        self._run(utc(2026, 11, 11, 6), {"AAA": 12.0})
        with open(os.path.join(self.dir, "data", "score_history.csv"), encoding="utf-8") as fh:
            dates = [r["date"] for r in csv.DictReader(fh)]
        self.assertEqual(dates.count("2026-11-10"), 3)
        self.assertEqual(dates.count("2026-11-11"), 3)

    def test_limit(self):
        last = daily_job.run(self.dir, limit=1, now_utc=utc(2026, 11, 10, 6),
                             scan_fn=fake_scan({"AAA": 1.0}), universe=UNIVERSE)
        self.assertEqual(last["total"], 1)


class TestTelegram(unittest.TestCase):
    def test_skipped_when_not_configured(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            with patch("daily_job.requests.post") as post:
                self.assertFalse(daily_job.send_telegram("hi"))
                post.assert_not_called()

    def test_sends_when_configured(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "42"}):
            with patch("daily_job.requests.post") as post:
                post.return_value.status_code = 200
                self.assertTrue(daily_job.send_telegram("שלום"))
                self.assertEqual(post.call_args.kwargs["data"], {"chat_id": "42", "text": "שלום"})


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
