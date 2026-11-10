"""
daily.py -- the once-a-day screener job.

    1. scan the whole universe        (scan.py -> screen_results.json)
    2. rank it                        (rank.py)
    3. save a dated snapshot          (history/YYYY-MM-DD.json)
       -> these snapshots are what we will later use to check whether the
          recommendations actually worked (model evaluation).
    4. compare with the previous snapshot (who entered / left the top list)
    5. send a short summary to Telegram (notify.py)

Usage:
    python daily.py               # full job (scan takes ~30-40 min)
    python daily.py --no-scan     # reuse the existing screen_results.json
    python daily.py --dry-run     # print the message instead of sending it

Scheduled on the Mac with launchd -- see com.evyat.stockapp.daily.plist:
it starts at 08:00 and runs `daily.py --send-at 09:00`, so the Telegram
message arrives at 09:00.
"""

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime

import rank

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = os.path.join(HERE, "screen_results.json")
HISTORY_DIR = os.path.join(HERE, "history")

TOP_N = 10
HORIZON = "1y"
ISRAEL_TOP_N = 3
MIN_OK_FRACTION = 0.5   # below this, warn that the data source was flaky

# launchd starts the job at 08:00 (the scan takes ~40 min); the message is
# held until SEND_AT so it always arrives at the same time.  If the scan
# runs late, the message goes out as soon as it is ready.
SEND_AT = "09:00"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def run_scan_step(results_path: str = None) -> None:
    """Full scan.  If today's run was interrupted, resume it instead."""
    results_path = results_path or RESULTS_PATH
    import scan                      # imported lazily: pulls in yfinance
    from universe import get_universe
    resume = False
    try:
        with open(results_path, encoding="utf-8") as fh:
            data = json.load(fh)
        started = (data.get("generated_at") or "")[:10]
        resume = (not data.get("complete", True)
                  and started == datetime.now().strftime("%Y-%m-%d"))
    except (OSError, ValueError):
        pass
    scan.run_scan(get_universe(), out_path=results_path, resume=resume)


def save_snapshot(ranked: list, data: dict, day: str,
                  history_dir: str = None) -> str:
    history_dir = history_dir or HISTORY_DIR
    os.makedirs(history_dir, exist_ok=True)
    path = os.path.join(history_dir, f"{day}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "day": day,
            "scan_generated_at": data.get("generated_at"),
            "horizon": HORIZON,
            "ranked": ranked,
        }, fh, indent=1, ensure_ascii=False)
    return path


def load_previous_snapshot(day: str, history_dir: str = None):
    """Most recent snapshot strictly before `day`, or None."""
    history_dir = history_dir or HISTORY_DIR
    files = sorted(glob.glob(os.path.join(history_dir, "????-??-??.json")))
    earlier = [f for f in files if os.path.basename(f)[:10] < day]
    if not earlier:
        return None
    try:
        with open(earlier[-1], encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def diff_top(today: list, previous, n: int = TOP_N) -> dict:
    """Who entered / left today's top-n compared with the previous snapshot."""
    now = [e["symbol"] for e in today[:n]]
    if not previous:
        return {"entered": [], "left": [], "has_previous": False}
    before = [e["symbol"] for e in previous.get("ranked", [])[:n]]
    return {
        "entered": [s for s in now if s not in before],
        "left": [s for s in before if s not in now],
        "has_previous": True,
    }


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

def _line(e: dict, new: bool) -> str:
    up = f"{e['upside_pct']:+.0f}%" if e.get("upside_pct") is not None else "—"
    flag = " 🆕" if new else ""
    agree = " ✅" if e.get("agree") else ""
    return (f"{e['rank']}. {e['symbol']}  {e['opportunity']:.0f}  "
            f"({e['our_label']}, אנליסטים {up}){agree}{flag}")


def build_message(data: dict, ranked: list, diff: dict, day: str) -> str:
    rows = data.get("rows", [])
    counts = Counter(r.get("data_status") for r in rows)
    d = datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.%Y")

    out = [f"📈 סיכום מניות יומי — {d}",
           f"נסרקו {len(rows)} מניות "
           f"(תקין {counts['ok']}, חלקי {counts['partial']}, "
           f"נכשל {counts['failed']})"]
    if rows and counts["ok"] / len(rows) < MIN_OK_FRACTION:
        out.append("⚠️ הרבה מניות בלי נתונים מלאים היום — "
                   "ייתכן ש-Yahoo חסם/האט. כדאי להתייחס בזהירות.")
    if not data.get("complete", True):
        out.append("⚠️ הסריקה לא הושלמה — הדירוג חלקי.")

    out += ["", f"🏆 {TOP_N} ההזדמנויות המובילות (טווח שנה):"]
    entered = set(diff["entered"])
    out += [_line(e, e["symbol"] in entered and diff["has_previous"])
            for e in ranked[:TOP_N]]

    if diff["has_previous"]:
        out.append("")
        if diff["entered"] or diff["left"]:
            if diff["entered"]:
                out.append("🆕 נכנסו לטופ: " + ", ".join(diff["entered"]))
            if diff["left"]:
                out.append("⬇️ יצאו מהטופ: " + ", ".join(diff["left"]))
        else:
            out.append("ללא שינוי בטופ מאתמול.")

    il = [e for e in ranked if "IL" in e.get("regions", [])][:ISRAEL_TOP_N]
    if il:
        out += ["", "🇮🇱 המובילות בישראל:"]
        out += [f"• {e['symbol']}  {e['opportunity']:.0f}  ({e['our_label']})"
                for e in il]

    out += ["",
            "ציון = 60% פסק הדין שלנו + 40% יעד האנליסטים, פחות קנס סיכון. "
            "✅ = שנינו חיוביים.",
            "⚠️ לא ייעוץ השקעות."]
    return "\n".join(out)


def seconds_until(hhmm: str, now: datetime = None) -> float:
    """Seconds from `now` until today's HH:MM (0 if already past)."""
    now = now or datetime.now()
    h, m = map(int, hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return max(0.0, (target - now).total_seconds())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Daily screener job")
    p.add_argument("--no-scan", action="store_true",
                   help="Reuse the existing screen_results.json")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the message instead of sending it")
    p.add_argument("--send-at", metavar="HH:MM", default=None,
                   help=f"Hold the message until this time (launchd passes "
                        f"{SEND_AT}); default: send immediately")
    a = p.parse_args(argv)

    day = datetime.now().strftime("%Y-%m-%d")
    print(f"=== daily.py {datetime.now().isoformat(timespec='seconds')} ===")

    if not a.no_scan:
        run_scan_step()

    try:
        data = rank.load_results(RESULTS_PATH)
    except (OSError, ValueError) as exc:
        msg = f"⚠️ stock-app: אין תוצאות סריקה היום ({exc})"
        print(msg)
        if not a.dry_run:
            import notify
            notify.send_telegram(msg)
        return 1

    ranked = rank.rank_rows(data.get("rows", []), horizon=HORIZON)
    previous = load_previous_snapshot(day)
    snap = save_snapshot(ranked, data, day)
    print(f"Saved snapshot {snap} ({len(ranked)} ranked)")

    message = build_message(data, ranked, diff_top(ranked, previous), day)
    if a.dry_run:
        print("\n" + message)
        return 0

    if a.send_at:
        wait = seconds_until(a.send_at)
        if wait:
            print(f"Holding message until {a.send_at} ({wait / 60:.0f} min)")
            sys.stdout.flush()
            time.sleep(wait)

    import notify
    sent = notify.send_telegram(message)
    print("Telegram:", "sent" if sent else "FAILED")
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
