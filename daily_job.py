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
  4. Ranks the stocks (rank.py) and saves the full ranked list as
     data/ranked/YYYY-MM-DD.json + data/ranked/latest.json (kept for checking
     later whether the picks worked).
  5. Writes data/last_run.json and a one-line Hebrew summary
     data/last_summary.txt.
  6. My portfolio (data/portfolio.json, when there is one): scores every stock
     I hold / my funds follow / I watch -> data/holding_scores.json (the app's
     home screen reads it instead of computing them live), and puts a personal
     part at the top of the message (my total, the day, movers, this week's
     reports / dividends, big score changes).
  7. Builds the Hebrew Telegram message (top 10, who entered / left the top
     vs the previous run, top Israeli stocks), optionally holds it until
     --send-at, and sends it IF a bot is configured (see notify.py:
     env TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, or .streamlit/secrets.toml).

Usage:
    python daily_job.py                      # data goes to ./local_data
    python daily_job.py --limit 5 --force    # quick test, ignore the time guard
    python daily_job.py --data-dir PATH      # e.g. a checkout of the data repo
    python daily_job.py --send-at 09:00      # hold the message until 09:00
    python daily_job.py --dry-run            # print the message, don't send

On the Mac it is started at 08:00 by launchd (com.evyat.stockapp.daily.plist
-> run_daily.sh), which passes --send-at 09:00.
"""

import argparse
import csv
import io
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import ai_insights
import data_store
import notify
import rank

ISRAEL = ZoneInfo("Asia/Jerusalem")
EARLIEST_HOUR = 7  # don't run before 07:00 Israel time

HISTORY_PATH = "data/score_history.csv"
RESULTS_PATH = "data/screen_results.json"
LAST_RUN_PATH = "data/last_run.json"
SUMMARY_PATH = "data/last_summary.txt"
RANKED_LATEST_PATH = "data/ranked/latest.json"
RANKED_DAY_PATH = "data/ranked/{day}.json"
AI_PICKS_PATH = "data/ai_picks.json"   # "ה-AI ממליץ" (read by the app)
HOLDING_SCORES_PATH = "data/holding_scores.json"   # my stocks' scores + calendar (app)
PORTFOLIO_PATH = "data/portfolio.json"
WATCHLIST_PATH = "data/watchlist.json"

HORIZON = "1y"           # horizon used for the daily ranking
TOP_N = 10               # stocks listed in the message
ISRAEL_TOP_N = 3
MIN_OK_FRACTION = 0.5    # below this, warn that the data source was flaky

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


def diff_top(today, previous, n=TOP_N):
    """Who entered / left today's top-n compared with the previous ranking."""
    now = [e["symbol"] for e in today[:n]]
    if not previous:
        return {"entered": [], "left": [], "has_previous": False}
    before = [e["symbol"] for e in previous.get("ranked", [])[:n]]
    return {
        "entered": [s for s in now if s not in before],
        "left": [s for s in before if s not in now],
        "has_previous": True,
    }


def _top_line(e, new):
    up = f"{e['upside_pct']:+.0f}%" if e.get("upside_pct") is not None else "—"
    return (f"{e['rank']}. {e['symbol']}  {e['opportunity']:.0f}  "
            f"({e['our_label']}, אנליסטים {up})"
            f"{' ✅' if e.get('agree') else ''}{' 🆕' if new else ''}")


def build_message(rows, ranked, diff, today, complete=True):
    """The full Hebrew Telegram message."""
    counts = {"ok": 0, "partial": 0, "failed": 0}
    for r in rows:
        if r.get("data_status") in counts:
            counts[r["data_status"]] += 1
    d = datetime.strptime(today, "%Y-%m-%d").strftime("%d.%m.%Y")

    out = [f"📈 סיכום מניות יומי — {d}",
           f"נסרקו {len(rows)} מניות (תקין {counts['ok']}, "
           f"חלקי {counts['partial']}, נכשל {counts['failed']})"]
    if rows and counts["ok"] / len(rows) < MIN_OK_FRACTION:
        out.append("⚠️ הרבה מניות בלי נתונים מלאים היום — "
                   "ייתכן ש-Yahoo חסם/האט. כדאי להתייחס בזהירות.")
    if not complete:
        out.append("⚠️ הסריקה לא הושלמה — הדירוג חלקי.")

    out += ["", f"🏆 {TOP_N} ההזדמנויות המובילות (טווח שנה):"]
    entered = set(diff["entered"]) if diff["has_previous"] else set()
    out += [_top_line(e, e["symbol"] in entered) for e in ranked[:TOP_N]]

    if diff["has_previous"]:
        out.append("")
        if diff["entered"]:
            out.append("🆕 נכנסו לטופ: " + ", ".join(diff["entered"]))
        if diff["left"]:
            out.append("⬇️ יצאו מהטופ: " + ", ".join(diff["left"]))
        if not (diff["entered"] or diff["left"]):
            out.append("ללא שינוי בטופ מהריצה הקודמת.")

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


def seconds_until(hhmm, now=None):
    """Seconds from `now` (Israel time) until today's HH:MM; 0 if past."""
    now = now or israel_now()
    h, m = map(int, hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return max(0.0, (target - now).total_seconds())


# ---------------------------------------------------------------------------
# "AI recommends" (mockup item 15)
# ---------------------------------------------------------------------------

def make_ai_picks(rows, today, api_key=None):
    """Code filters the scan to 10 short- + 10 long-term candidates; Gemini
    picks 2-3 of each and says why. Without a key (or if the AI fails) the
    code's own top-2 are used and marked so. Never raises."""
    try:
        candidates = ai_insights.pick_candidates(rows)
    except Exception:
        candidates = {"short": [], "long": []}
    picks, source = None, "code"
    if api_key and (candidates["short"] or candidates["long"]):
        import gemini_helper  # imported here: only the daily run needs it
        raw = gemini_helper.pick_recommendations(candidates, api_key)
        picks = ai_insights.clean_picks(raw, candidates) if raw else None
        if picks and (picks["short"] or picks["long"]):
            source = "ai"
        else:
            picks = None
    if picks is None:
        picks = ai_insights.fallback_picks(candidates)
    return {"day": today, "source": source, "short": picks["short"],
            "long": picks["long"], "candidates": candidates}


def picks_lines(picks):
    """The Telegram section for the picks."""
    if not picks or not (picks.get("short") or picks.get("long")):
        return []
    title = "🤖 ה-AI ממליץ לבדוק" if picks.get("source") == "ai" else "🔎 שווה לבדוק (לפי הקוד)"
    out = ["", title + ":"]
    for key, name in (("short", "טווח קצר"), ("long", "טווח ארוך")):
        if picks.get(key):
            out.append(f"{name}:")
            out += [f"• {p['symbol']} – {p['why']}" for p in picks[key]]
    return out


# ---------------------------------------------------------------------------
# Telegram (optional)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# My portfolio: scores for the app (fast home screen) + the personal message
# ---------------------------------------------------------------------------

class Fetchers:
    """Network calls for the portfolio part (tests pass fakes)."""

    def __init__(self):
        import engine
        import events
        self.engine, self.events = engine, events

    def quote(self, sym):
        return self.engine.get_stock_quote(sym)

    def verdict(self, sym):
        return self.engine.compute_verdict(sym)

    def analyst(self, sym):
        return self.engine.get_analyst_consensus(sym)

    def history(self, sym):
        return self.engine.get_price_history(sym, "1Y")

    def calendar(self, sym):
        return self.events.fetch_calendar(sym)


def portfolio_part(data_dir, today, fetch=None):
    """
    Score every stock I hold / my funds follow / I watch, save them for the app
    (data/holding_scores.json: the home screen reads these instead of computing
    20+ scores live), and build the personal lines of the morning message.
    -> lines (list of str; [] when there's no portfolio). Never raises.
    """
    import buy_score
    import events
    import portfolio
    import valuation
    try:
        data = data_store.read_json(PORTFOLIO_PATH, default=None, local_dir=data_dir)
        if not data:
            return []
        data = portfolio.clean_data(data)
        watch = data_store.read_json(WATCHLIST_PATH, default=[], local_dir=data_dir) or []
        fetch = fetch or Fetchers()
        syms = valuation.symbols_to_score(data, watch)

        quotes = {}
        for sym in syms:
            try:
                quotes[sym] = fetch.quote(sym)
            except Exception:
                quotes[sym] = None

        def quote_fn(sym):
            q = quotes.get(sym)
            if q is None:
                try:
                    q = quotes[sym] = fetch.quote(sym)
                except Exception:
                    return None
            if not (q and q.found):
                return None
            price, cur = portfolio.to_major(q.price, q.currency)
            return price, q.change_pct, cur, q.name

        fx_cache = {}

        def fx_fn(cur):
            sym = portfolio.fx_symbol(cur)
            if sym is None:
                return 1.0
            if cur not in fx_cache:
                q = quote_fn(sym)
                fx_cache[cur] = q[0] if q else None
            return fx_cache[cur]

        def closes_fn(sym):
            q = quotes.get(sym)
            try:
                return valuation.closes_major(fetch.history(sym), q.currency if q else "")
            except Exception:
                return []

        previous = data_store.read_json(HOLDING_SCORES_PATH, default={}, local_dir=data_dir) or {}
        scores, calendars = {}, {}
        for sym in syms:
            try:
                sc = buy_score.from_engine_objects(fetch.verdict(sym), fetch.analyst(sym))
                parts = {p["key"]: p["value"] for p in sc["parts"]}
                scores[sym] = {"engine": parts.get("engine"), "analysts": parts.get("analysts"),
                               "score": sc["score"]}
            except Exception:
                pass
            try:
                calendars[sym] = fetch.calendar(sym)
            except Exception:
                pass
        data_store.write_json(HOLDING_SCORES_PATH, {
            "date": today, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scores": scores, "calendar": calendars}, local_dir=data_dir)

        rows, names = valuation.value_rows(data, quote_fn, fx_fn, closes_fn)
        held = {h["symbol"]: h["qty"] for h in portfolio.as_holdings(portfolio.compute(data))}
        week = events.upcoming({s: calendars.get(s) for s in held}, held)
        old = {s: (v or {}).get("score") for s, v in (previous.get("scores") or {}).items()} \
            if previous.get("date") != today else {}
        changes = valuation.score_changes(old, {s: scores[s]["score"] for s in held if s in scores},
                                          names)
        return valuation.telegram_lines(rows, names, week, changes)
    except Exception as exc:
        print(f"portfolio part: failed ({type(exc).__name__}: {exc})")
        return []


def send_telegram(text):
    """Send `text` via notify.py if a bot is configured. Never raises."""
    try:
        return notify.send_telegram(text)
    except Exception as exc:  # never let messaging break the run
        print(f"telegram: failed ({type(exc).__name__})")
        return False


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def _resume_today(results_file, today):
    """True if results_file is an unfinished scan started today (Israel)."""
    try:
        with open(results_file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    started = data.get("generated_at") or ""
    try:
        started_day = israel_now(datetime.fromisoformat(started)).date().isoformat()
    except ValueError:
        return False
    return not data.get("complete", True) and started_day == today


def run(data_dir, limit=None, force=False, now_utc=None, scan_fn=None, universe=None,
        send_at=None, dry_run=False, fetch=None):
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

    results_file = data_store._local_path(RESULTS_PATH, data_dir)
    os.makedirs(os.path.dirname(results_file), exist_ok=True)

    if scan_fn is None:
        import scan  # imported here so tests can run without yfinance calls
        resume = _resume_today(results_file, today)

        def scan_fn(u, out_path):
            return scan.run_scan(u, out_path=out_path, resume=resume)
    if universe is None:
        from universe import get_universe
        universe = get_universe()
    if limit:
        universe = universe[:limit]

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

    # Rank + keep the ranked list (latest + one file per day).
    ranked = rank.rank_rows(rows, horizon=HORIZON)
    previous = data_store.read_json(RANKED_LATEST_PATH, default=None, local_dir=data_dir)
    if previous and previous.get("day") == today:
        previous = None  # a same-day re-run: don't compare a day with itself
    snapshot = {"day": today, "horizon": HORIZON,
                "scan_generated_at": result.get("generated_at"), "ranked": ranked}
    data_store.write_json(RANKED_DAY_PATH.format(day=today), snapshot, local_dir=data_dir)
    data_store.write_json(RANKED_LATEST_PATH, snapshot, local_dir=data_dir)
    last_run["ranked"] = len(ranked)

    ai_key = None
    if not os.environ.get("STOCKAPP_DISABLE_GEMINI"):
        import gemini_helper
        ai_key = gemini_helper.load_api_key()
    picks = make_ai_picks(rows, today, ai_key)
    data_store.write_json(AI_PICKS_PATH, picks, local_dir=data_dir)
    last_run["ai_picks"] = picks["source"]

    mine = portfolio_part(data_dir, today, fetch=fetch)
    last_run["portfolio_lines"] = len(mine)
    message = build_message(rows, ranked, diff_top(ranked, previous), today,
                            complete=result.get("complete", True))
    if mine:   # my portfolio first, then the market scan
        message = "\n".join(mine) + "\n\n" + message
    extra = picks_lines(picks)
    if extra:  # before the two footer lines (formula + disclaimer)
        lines = message.split("\n")
        message = "\n".join(lines[:-3] + extra + lines[-3:])
    if dry_run:
        print("\n" + message)
        return last_run
    if send_at:
        wait = seconds_until(send_at)
        if wait:
            print(f"holding the message until {send_at} ({wait / 60:.0f} min)")
            sys.stdout.flush()
            time.sleep(wait)
    send_telegram(message)
    return last_run


def main():
    parser = argparse.ArgumentParser(description="Once-a-day automatic run")
    parser.add_argument("--data-dir", default=data_store.DEFAULT_LOCAL_DIR,
                        help="Folder to write data/ into (default: ./local_data)")
    parser.add_argument("--limit", type=int, default=None, help="Only the first N tickers")
    parser.add_argument("--force", action="store_true", help="Ignore the time guard")
    parser.add_argument("--send-at", metavar="HH:MM", default=None,
                        help="Hold the Telegram message until this Israel time")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the message instead of sending it")
    args = parser.parse_args()
    try:
        run(args.data_dir, limit=args.limit, force=args.force,
            send_at=args.send_at, dry_run=args.dry_run)
    except Exception:
        traceback.print_exc()
        if not args.dry_run:
            send_telegram("⚠️ הריצה היומית נכשלה. פרטים ביומן (logs/daily.log "
                          "במק, או GitHub Actions).")
        sys.exit(1)


if __name__ == "__main__":
    main()
