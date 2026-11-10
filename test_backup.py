"""
test_backup.py -- offline tests for backup.py, plus the backup/restore flow
in the real app on the dev/ sample data (AppTest). No network.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))

import backup as B

NOW = datetime(2026, 11, 10, 9, 30, tzinfo=ZoneInfo("Asia/Jerusalem"))
HOLD = [{"symbol": "NVDA", "qty": 20.0, "cost": 118.0},
        {"symbol": "TEVA.TA", "qty": 300.0, "cost": 52.4}]


class TestBackup(unittest.TestCase):
    def test_round_trip(self):
        text = B.make_backup(HOLD, ["aapl", "AAPL", "TEVA.TA", ""], NOW)
        got = B.parse_backup(text)
        self.assertEqual(got["portfolio"], HOLD)
        self.assertEqual(got["watchlist"], ["AAPL", "TEVA.TA"])
        self.assertEqual(got["saved_at"], "2026-11-10T09:30:00+02:00")
        self.assertEqual(B.summary(got), "2 מניות בתיק, 2 במעקב (מ-10.11.2026)")

    def test_bytes_from_uploaded_file(self):
        text = B.make_backup(HOLD, [], NOW).encode("utf-8-sig")
        self.assertEqual(B.parse_backup(text)["portfolio"], HOLD)

    def test_bad_inputs_raise_hebrew_errors(self):
        for bad in ("", "   ", "hello", "[1,2]", '{"app": "other"}',
                    json.dumps({"app": "stock-app", "version": 99})):
            with self.assertRaises(B.BackupError) as cm:
                B.parse_backup(bad)
            self.assertTrue(any("֐" <= ch <= "׿" for ch in str(cm.exception)))

    def test_bad_rows_inside_are_skipped(self):
        text = json.dumps({"app": "stock-app", "version": 1,
                           "portfolio": [{"symbol": "A", "qty": 1, "cost": 2}, {"x": 1}],
                           "watchlist": ["B", 5]})
        got = B.parse_backup(text)
        self.assertEqual(got["portfolio"], [{"symbol": "A", "qty": 1.0, "cost": 2.0}])
        self.assertEqual(got["watchlist"], ["B"])

    def test_file_name(self):
        self.assertEqual(B.file_name(NOW), "stock-app-backup-2026-11-10.json")

    def test_reminder(self):
        self.assertFalse(B.needs_reminder(None, NOW, has_data=False))
        self.assertTrue(B.needs_reminder(None, NOW, has_data=True))
        recent = (NOW - timedelta(days=2)).isoformat()
        old = (NOW - timedelta(days=6)).isoformat()
        self.assertFalse(B.needs_reminder(recent, NOW, True))
        self.assertTrue(B.needs_reminder(old, NOW, True))
        self.assertTrue(B.needs_reminder("garbage", NOW, True))


class TestAppFlow(unittest.TestCase):
    """Backup + restore in the REAL app on the offline sample data."""

    @classmethod
    def setUpClass(cls):
        os.environ["STOCKAPP_DISABLE_BROWSER_STORAGE"] = "1"
        os.environ["STOCKAPP_DISABLE_PASSWORD"] = "1"
        os.environ["STOCKAPP_DISABLE_GEMINI"] = "1"

    def _app(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "dev", "demo_app.py"),
                               default_timeout=60)
        at.run()
        self.assertFalse(at.exception, at.exception)
        return at

    def test_backup_text_and_restore(self):
        at = self._app()
        at.session_state["portfolio"] = HOLD[:1]
        at.session_state["watchlist"] = ["AAPL"]
        at.run()
        # The backup text shown on the home screen holds the data.
        codes = [c.value for c in at.code]
        self.assertTrue(codes, "expected the backup text block")
        self.assertEqual(B.parse_backup(codes[0])["portfolio"], HOLD[:1])
        # Restore a different backup by pasting it.
        other = B.make_backup(HOLD, ["MSFT"], NOW)
        at.text_area(key="restore_text").set_value(other)
        at.button(key="restore_btn").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.session_state["portfolio"], HOLD)
        self.assertEqual(at.session_state["watchlist"], ["MSFT"])
        self.assertIn("שוחזר", "\n".join(s.value for s in at.success))

    def test_restore_garbage_shows_error_and_keeps_data(self):
        at = self._app()
        at.session_state["portfolio"] = HOLD[:1]
        at.run()
        at.text_area(key="restore_text").set_value("not a backup")
        at.button(key="restore_btn").click().run()
        self.assertEqual(at.session_state["portfolio"], HOLD[:1])
        self.assertTrue(at.error, "expected an error message")

    def test_reminder_shown_when_never_backed_up(self):
        at = self._app()
        at.session_state["portfolio"] = HOLD[:1]
        at.run()
        self.assertIn("גיבוי", "\n".join(w.value for w in at.warning))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
