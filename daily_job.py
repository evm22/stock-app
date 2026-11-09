"""
daily_job.py -- the once-a-day automatic run (no Streamlit inside).

What it does, in order:
  1. Time guard: only runs once per Israel calendar day, and not before 07:00
     Israel time (GitHub's scheduler works in UTC and ignores summer/winter
     time, so the workflow fires twice and this guard keeps exactly one run).
  2. Scans the whole universe with the same engine as the app
     -> data/screen_results.json
  3. Appends today's numeric scores + price per stock to
     data/score_history.csv (the raw material for "was the score right?").
  4. Writes data/last_run.json and a one-line Hebrew summary
     data/last_summary.txt.
  5. Sends the summary to Telegram IF a bot token + chat id are configured
     (env TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). Otherwise it just skips that.

Usage:
    python daily_job.py                      # data goes to ./local_data
    python daily_job.py --limit 5 --force    # quick test, ignore the time guard
    python daily_job.py --data-dir PATH      # e.g. a checkout of the data repo
"""

import argparse
import csv
import io
import os
import sys
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

import data_store

ISRAEL = ZoneInfo("Asia/Jerusalem")
EARLIEST_HOUR = 7  # don't run before 07:00 Israel time

HISTORY_PATH = "data/score_history.csv"
RESULTS_PATH = "data/screen_results.json"
LAST_RUN_PATH = "data/last_run.json"
SUMMARY_PATH = "data/last_summary.txt"

HISTORY_FIELDS = ["date", "symbol", "score_6m_num", "score_1y_num", "score_5y_num",
                  "label_1y", "price", "data_status"]


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def israel_now(now_utc=None):
    """Current time in Israel (pass a UTC datetime to test)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc.astimezone(ISRAEL)


def should_run(now_utc, last_run_date, force=False):
    """
    Decide whether today's run should happen. Returns (bool, reason).

    - force=True always runs (manual test runs).
    - Before 07:00 Israel time: skip (the earlier of the two daily triggers in
      winter time).
    - Already ran today (Israel date): skip (the later trigger, or a re-run).
    """
    if force:
        return True, "forced"
    local = israel_now(now_utc)
    if local.hour < EARLIEST_HOUR:
        return False, f"too early in Israel ({local:%H:%M})"
    if last_run_date == local.date().isoformat():
        return False, f"already ran today ({last_run_date})"
    return True, f"running at {local:%H:%M} Israel time"


def _fmt(value):
    """CSV cell: '' for missing, numbers without float noise."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def merge_history(existing_csv, today, rows):
    """
    Return new CSV text: every past day's rows untouched, plus today's rows.
    If today already exists in the file (a re-run), only today's rows are
    replaced, so running twice never duplicates a day.
    """
    kept = []
    if existing_csv and existing_csv.strip():
        reader = csv.DictReader(io.StringIO(existing_csv))
        kept = [r for r in reader if r.get("date") != today]

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=HISTORY_FIELDS, lineterminator="\n")
    writer.writeheader()
    for r in kept:
        writer.writerow({k: r.get(k, "") for k in HISTORY_FIELDS})
    for row in rows:
        writer.writerow({
            "date": today,
            "symbol": row.get("symbol", ""),
            "score_6m_num": _fmt(row.get("score_6m_num")),
            "score_1y_num": _fmt(row.get("score_1y_num")),
            "score_5y_num": _fmt(row.get("score_5y_num")),
            "label_1y": _fmt(row.get("score_1y")),
            "price": _fmt(row.get("current_price")),
            "data_status": _fmt(row.get("data_status")),
        })
    return out.getvalue()


def summary_line(counts, today):
    """One-line Hebrew summary for the log and Telegram."""
    # Mostly failed usually means the data source blocked us: flag it clearly.
    icon = "⚠️" if counts["total"] and counts["failed"] * 2 > counts["total"] else "✅"
    return (f"{icon} הריצה היומית הסתיימה ({today}): {counts['total']} מניות נסרקו. "
            f"{counts['ok']} תקינות, {counts['partial']} חלקיות, {counts['failed']} נכשלו.")


# ---------------------------------------------------------------------------
# Telegram (optional)
# ---------------------------------------------------------------------------

def send_telegram(text):
    """Send `text` if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. Never raises."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("telegram: not configured, skipped")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat_id, "text": text}, timeout=20)
        ok = r.status_code == 200
        print(f"telegram: {'sent' if ok else f'failed (HTTP {r.status_code})'}")
        return ok
    except requests.RequestException as exc:
        print(f"telegram: failed ({type(exc).__name__})")  # never print the token/URL
        return False


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(data_dir, limit=None, force=False, now_utc=None, scan_fn=None, universe=None):
    """
    Do the daily run. Returns the last_run dict, or None if the guard skipped.
    `scan_fn` / `universe` are injectable so tests never touch the network.
    """
    last = data_store.read_json(LAST_RUN_PATH, default={}, local_dir=data_dir) or {}
    go, reason = should_run(now_utc or datetime.now(timezone.utc), last.get("date"), force)
    print(f"guard: {reason}")
    if not go:
        return None

    started = datetime.now(timezone.utc)
    today = israel_now(now_utc).date().isoformat()

    if scan_fn is None:
        import scan  # imported here so tests can run without yfinance calls
        scan_fn = scan.run_scan
    if universe is None:
        from universe import get_universe
        universe = get_universe()
    if limit:
        universe = universe[:limit]

    results_file = data_store._local_path(RESULTS_PATH, data_dir)
    os.makedirs(os.path.dirname(results_file), exist_ok=True)
    result = scan_fn(universe, out_path=results_file) or {}
    rows = result.get("rows", [])

    existing = data_store.read_text(HISTORY_PATH, local_dir=data_dir)
    data_store.write_text(HISTORY_PATH, merge_history(existing, today, rows), local_dir=data_dir)

    counts = {"total": len(rows), "ok": 0, "partial": 0, "failed": 0}
    for r in rows:
        status = r.get("data_status")
        if status in counts:
            counts[status] += 1

    last_run = {
        "date": today,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **counts,
    }
    data_store.write_json(LAST_RUN_PATH, last_run, local_dir=data_dir)
    line = summary_line(counts, today)
    data_store.write_text(SUMMARY_PATH, line + "\n", local_dir=data_dir)
    print(line)
    send_telegram(line)
    return last_run


def main():
    parser = argparse.ArgumentParser(description="Once-a-day automatic run")
    parser.add_argument("--data-dir", default=data_store.DEFAULT_LOCAL_DIR,
                        help="Folder to write data/ into (default: ./local_data)")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N tickers")
    parser.add_argument("--force", action="store_true", help="Ignore the time guard")
    args = parser.parse_args()
    try:
        run(args.data_dir, limit=args.limit, force=args.force)
    except Exception:
        traceback.print_exc()
        send_telegram("⚠️ הריצה היומית נכשלה. פרטים ביומן של GitHub Actions.")
        sys.exit(1)


if __name__ == "__main__":
    main()
