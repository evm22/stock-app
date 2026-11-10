"""
backup.py -- backup & restore of the user's own data (mockup item 9).

Why: the portfolio and watchlist live in the browser (localStorage), and
Safari on iPhone wipes a site's storage after ~7 days without a visit. A
backup is a small JSON text the user can copy, or save as a file, and later
paste / upload to restore everything.

Pure Python, no Streamlit (tested in test_backup.py).

Format (version 1):
    {"app": "stock-app", "version": 1, "saved_at": "2026-11-10T09:30:00+02:00",
     "portfolio": [{"symbol": "NVDA", "qty": 20.0, "cost": 118.0}, ...],
     "watchlist": ["AAPL", "TEVA.TA"]}
"""

import json
from datetime import datetime, timedelta

import portfolio

APP_ID = "stock-app"
VERSION = 1
REMIND_AFTER_DAYS = 6   # remind before Safari's ~7-day wipe


class BackupError(ValueError):
    """A backup text that can't be restored. The message is Hebrew, for the UI."""


def make_backup(holdings, watchlist, now):
    """The backup text (pretty JSON). `now` is a timezone-aware datetime."""
    return json.dumps({
        "app": APP_ID,
        "version": VERSION,
        "saved_at": now.isoformat(timespec="seconds"),
        "portfolio": portfolio.parse(holdings),
        "watchlist": _clean_watchlist(watchlist),
    }, ensure_ascii=False, indent=1)


def file_name(now):
    return f"stock-app-backup-{now:%Y-%m-%d}.json"


def _clean_watchlist(items):
    out = []
    for s in items or []:
        if isinstance(s, str) and s.strip():
            sym = s.strip().upper()
            if sym not in out:
                out.append(sym)
    return out


def parse_backup(text):
    """
    Read a backup text -> {"portfolio": [...], "watchlist": [...],
    "saved_at": str|None}. Raises BackupError with a Hebrew message for
    anything that isn't a backup of this app.
    """
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise BackupError("הקובץ לא נקרא. צריך את קובץ הגיבוי (‎.json‎) של האפליקציה.")
    text = (text or "").strip()
    if not text:
        raise BackupError("לא הודבק כלום.")
    try:
        data = json.loads(text)
    except ValueError:
        raise BackupError("זה לא נראה כמו גיבוי של האפליקציה. כדאי להדביק את כל "
                          "הטקסט, מהסוגריים { ועד }.")
    if not isinstance(data, dict) or data.get("app") != APP_ID:
        raise BackupError("זה לא גיבוי של האפליקציה הזו.")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise BackupError("הגיבוי נוצר בגרסה חדשה יותר של האפליקציה.")
    return {
        "portfolio": portfolio.parse(data.get("portfolio") or []),
        "watchlist": _clean_watchlist(data.get("watchlist")),
        "saved_at": data.get("saved_at") if isinstance(data.get("saved_at"), str) else None,
    }


def summary(restored):
    """Short Hebrew description of what a backup holds."""
    p, w = len(restored["portfolio"]), len(restored["watchlist"])
    when = ""
    if restored.get("saved_at"):
        try:
            when = f" (מ-{datetime.fromisoformat(restored['saved_at']):%d.%m.%Y})"
        except ValueError:
            pass
    return f"{p} מניות בתיק, {w} במעקב{when}"


def needs_reminder(last_backup_iso, now, has_data):
    """True when there is data and no backup in the last REMIND_AFTER_DAYS."""
    if not has_data:
        return False
    if not last_backup_iso:
        return True
    try:
        last = datetime.fromisoformat(last_backup_iso)
    except (TypeError, ValueError):
        return True
    if last.tzinfo is None and now.tzinfo is not None:
        last = last.replace(tzinfo=now.tzinfo)
    return now - last >= timedelta(days=REMIND_AFTER_DAYS)
