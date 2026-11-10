"""
scan.py -- Stage 2a: standalone opportunity screener.

Scores every ticker in the universe using the SAME engine as the app
(compute_verdict + get_analyst_consensus), then writes
local_data/data/screen_results.json (the same file daily_job.py uses).

Usage:
    python scan.py             # full universe
    python scan.py --limit 20  # first N tickers (smoke-test)
    python scan.py --resume    # continue an interrupted run from its checkpoint

Then rank the results with rank.py (Stage 2b).
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import engine
from universe import get_universe

# ONE place for scan results, shared by scan.py, rank.py and daily_job.py
# (daily_job writes <data dir>/data/screen_results.json; locally that is this).
DEFAULT_RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "local_data", "data", "screen_results.json")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SLEEP_BETWEEN = 1.0    # seconds between tickers (Yahoo throttle)
CHECKPOINT_EVERY = 25  # write partial results every N tickers (crash-safe)
RETRY_DELAYS  = [5, 15]  # seconds to wait before retry 1, retry 2

# Fields that a fully-populated ("ok") row should have non-null.
# Dot notation resolves into nested dicts (e.g. "risk.beta" -> row["risk"]["beta"]).
EXPECTED_FIELDS = [
    "current_price",
    "analyst_mean_target",
    "analyst_implied_upside_pct",
    "score_6m", "score_1y", "score_5y",
    "risk.beta",
    "risk.pct_below_52w_high",
    "risk.debt_to_equity",
]


def _get_nested(row: dict, dotted: str):
    """Resolve 'risk.beta' against a row dict; return None if absent."""
    cur = row
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def compute_missing_fields(row: dict) -> list:
    """Return expected fields that are null/absent in this row."""
    return [f for f in EXPECTED_FIELDS if _get_nested(row, f) in (None, "", "N/A")]


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _num_score(hv):
    """A HorizonVerdict's 0..100 score rounded to 1 decimal, or None."""
    if hv is None or not hv.enough_data or not engine._is_number(hv.score):
        return None
    return round(float(hv.score), 1)


def _build_row(entry: dict, as_of: str) -> dict:
    """Score one universe entry; return a result row.  Never raises."""
    symbol  = entry["symbol"]
    regions = entry["regions"]
    themes  = entry["themes"]

    # --- verdict (3-horizon scoring) ---
    verdict = engine.compute_verdict(symbol)

    score_6m = score_1y = score_5y = None
    # Numeric 0..100 scores (same scale as the app's verdict tiles). Kept next
    # to the labels so the daily job can store a score history over time.
    score_6m_num = score_1y_num = score_5y_num = None
    if verdict.found and verdict.horizons:
        hv6  = verdict.horizons.get("6M")
        hv1  = verdict.horizons.get("1Y")
        hv5  = verdict.horizons.get("5Y")
        score_6m = hv6.label if (hv6 and hv6.enough_data) else None
        score_1y = hv1.label if (hv1 and hv1.enough_data) else None
        score_5y = hv5.label if (hv5 and hv5.enough_data) else None
        score_6m_num = _num_score(hv6)
        score_1y_num = _num_score(hv1)
        score_5y_num = _num_score(hv5)

    # --- analyst consensus ---
    analyst = engine.get_analyst_consensus(symbol)
    analyst_mean_target      = None
    analyst_implied_upside   = None
    if analyst.found and analyst.has_coverage:
        analyst_mean_target    = analyst.target_mean
        analyst_implied_upside = analyst.upside_pct

    # --- current price (prefer analyst's current_price; fallback to quote) ---
    current_price = None
    if analyst.found and engine._is_number(analyst.current_price):
        current_price = analyst.current_price
    if current_price is None:
        quote = engine.get_stock_quote(symbol)
        if quote.found and engine._is_number(quote.price):
            current_price = quote.price

    # --- risk fields from company + technicals ---
    beta = pct_below_52w_high = debt_to_equity = None
    try:
        technicals = engine.get_stock_technicals(symbol)
        if technicals.found:
            def _tv(key):
                m = technicals.metrics.get(key)
                return m.value if (m and m.available) else None
            beta_raw  = _tv("beta")
            high52    = _tv("week52_high")
            beta      = beta_raw if engine._is_number(beta_raw) else None
            if engine._is_number(high52) and high52 > 0 and engine._is_number(current_price):
                pct_below_52w_high = (high52 - current_price) / high52 * 100
    except Exception:
        pass
    try:
        company = engine.get_company_metrics(symbol)
        if company.found:
            def _cv(key):
                m = company.metrics.get(key)
                return m.value if (m and m.available) else None
            d2e_raw      = _cv("debt_to_equity")
            debt_to_equity = d2e_raw if engine._is_number(d2e_raw) else None
    except Exception:
        pass

    # --- data_status ---
    core_scored = any(x is not None for x in [score_6m, score_1y, score_5y])
    if not verdict.found:
        data_status = "failed"
    elif not core_scored:
        data_status = "failed"
    elif analyst_mean_target is None or current_price is None:
        data_status = "partial"
    else:
        data_status = "ok"

    row = {
        "symbol":   symbol,
        "regions":  regions,
        "themes":   themes,
        "score_6m": score_6m,
        "score_1y": score_1y,
        "score_5y": score_5y,
        "score_6m_num": score_6m_num,
        "score_1y_num": score_1y_num,
        "score_5y_num": score_5y_num,
        "current_price":              current_price,
        "analyst_mean_target":        analyst_mean_target,
        "analyst_implied_upside_pct": analyst_implied_upside,
        "risk": {
            "beta":               beta,
            "pct_below_52w_high": pct_below_52w_high,
            "debt_to_equity":     debt_to_equity,
        },
        "data_status": data_status,
        "as_of":       as_of,
    }

    # Fine-grained missing-field tracking: catch any gap the status check missed.
    row["missing_fields"] = compute_missing_fields(row)
    if row["data_status"] == "ok" and row["missing_fields"]:
        row["data_status"] = "partial"

    return row


def _score_one(entry: dict, as_of: str) -> dict:
    """Wrapper that retries on failure before marking 'failed'."""
    symbol = entry["symbol"]
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        try:
            row = _build_row(entry, as_of)
            if row["data_status"] != "failed":
                return row
            # Core score failed — wait and retry.
        except Exception:
            pass
        time.sleep(delay)
    # Final attempt (no retry after this).
    try:
        return _build_row(entry, as_of)
    except Exception as exc:
        return {
            "symbol":   symbol,
            "regions":  entry.get("regions", []),
            "themes":   entry.get("themes", []),
            "score_6m": None, "score_1y": None, "score_5y": None,
            "score_6m_num": None, "score_1y_num": None, "score_5y_num": None,
            "current_price": None,
            "analyst_mean_target": None,
            "analyst_implied_upside_pct": None,
            "risk": {"beta": None, "pct_below_52w_high": None, "debt_to_equity": None},
            "data_status": "failed",
            "as_of": as_of,
            "_error": str(exc),
        }


# ---------------------------------------------------------------------------
# Main scan loop
# ---------------------------------------------------------------------------

def _write_results(path: str, as_of: str, total: int, rows: list,
                   complete: bool) -> dict:
    """Write the results file atomically (tmp file + rename); return it."""
    result = {
        "generated_at":  as_of,
        "universe_size": total,
        "complete":      complete,
        "rows":          rows,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return result


def _load_checkpoint(path: str) -> list:
    """Rows from an unfinished earlier run (complete=False), else []."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if data.get("complete", True):
        return []
    return data.get("rows", [])


def run_scan(universe: list, out_path: str = DEFAULT_RESULTS,
             resume: bool = False) -> dict:
    total      = len(universe)
    rows       = []
    failed_syms = []
    t_start    = time.monotonic()

    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --resume: keep rows already scored by an interrupted run, skip them.
    done = set()
    if resume:
        rows = _load_checkpoint(out_path)
        done = {r["symbol"] for r in rows}
        failed_syms = [r["symbol"] for r in rows if r["data_status"] == "failed"]
        if rows:
            as_of = rows[0].get("as_of", as_of)
            print(f"Resuming: {len(rows)} tickers already scored.")

    print(f"Starting scan of {total} tickers.  Output -> {out_path}")
    print("-" * 60)

    for idx, entry in enumerate(universe, start=1):
        if entry["symbol"] in done:
            continue
        symbol = entry["symbol"]
        row = _score_one(entry, as_of)
        rows.append(row)
        status = row["data_status"]
        if status == "failed":
            failed_syms.append(symbol)
        elapsed = time.monotonic() - t_start
        print(f"[{idx:4d}/{total}] scored {symbol:12s} ... {status}  "
              f"({elapsed:.0f}s)")
        sys.stdout.flush()
        if len(rows) % CHECKPOINT_EVERY == 0:
            try:
                _write_results(out_path, as_of, total, rows, complete=False)
            except Exception as exc:
                print(f"  (checkpoint write failed: {exc})")
        if idx < total:
            time.sleep(SLEEP_BETWEEN)

    # Write output.
    elapsed_total = time.monotonic() - t_start
    result = {"generated_at": as_of, "universe_size": total,
              "complete": True, "rows": rows}
    try:
        _write_results(out_path, as_of, total, rows, complete=True)
        print(f"\nWrote {out_path}")
    except Exception as exc:
        print(f"\nERROR writing {out_path}: {exc}")

    # Summary + degradation rollup.
    status_counts = Counter(r["data_status"] for r in rows)
    field_failures = Counter()
    for r in rows:
        field_failures.update(r.get("missing_fields", []))

    print("\n" + "=" * 60)
    print(f"SCAN COMPLETE  total={total}  ok={status_counts['ok']}  "
          f"partial={status_counts['partial']}  failed={status_counts['failed']}  "
          f"elapsed={elapsed_total:.1f}s")
    if failed_syms:
        print(f"Failed symbols: {', '.join(failed_syms)}")
    else:
        print("No failures.")
    print("\n=== degradation rollup ===")
    print("missing-field counts (most common first):")
    for field_name, n in field_failures.most_common():
        print(f"  {field_name:32s} {n}")
    if not field_failures:
        print("  (none -- every row fully populated)")
    print("=" * 60)
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Opportunity screener batch scan")
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Only scan the first N tickers (default: full universe)",
    )
    parser.add_argument(
        "--out", default=DEFAULT_RESULTS,
        help="Output JSON file (default: local_data/data/screen_results.json, "
             "the same file the daily job uses)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Continue an interrupted scan from its checkpoint in --out",
    )
    args = parser.parse_args()

    universe = get_universe()
    if args.limit:
        universe = universe[:args.limit]

    try:
        run_scan(universe, out_path=args.out, resume=args.resume)
    except KeyboardInterrupt:
        print("\nInterrupted.  Re-run with --resume to continue from the "
              "last checkpoint.")
        sys.exit(1)


if __name__ == "__main__":
    main()
