"""
scan.py -- Stage 2a: standalone opportunity screener.

Scores every ticker in the universe using the SAME engine as the app
(compute_verdict + get_analyst_consensus), then writes screen_results.json.

Usage:
    python scan.py             # full universe
    python scan.py --limit 20  # first N tickers (smoke-test)
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import engine
from universe import get_universe

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SLEEP_BETWEEN = 1.0    # seconds between tickers (Yahoo throttle)
RETRY_DELAYS  = [5, 15]  # seconds to wait before retry 1, retry 2


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _build_row(entry: dict, as_of: str) -> dict:
    """Score one universe entry; return a result row.  Never raises."""
    symbol  = entry["symbol"]
    regions = entry["regions"]
    themes  = entry["themes"]

    # --- verdict (3-horizon scoring) ---
    verdict = engine.compute_verdict(symbol)

    score_6m = score_1y = score_5y = None
    if verdict.found and verdict.horizons:
        hv6  = verdict.horizons.get("6M")
        hv1  = verdict.horizons.get("1Y")
        hv5  = verdict.horizons.get("5Y")
        score_6m = hv6.label if (hv6 and hv6.enough_data) else None
        score_1y = hv1.label if (hv1 and hv1.enough_data) else None
        score_5y = hv5.label if (hv5 and hv5.enough_data) else None

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

    return {
        "symbol":   symbol,
        "regions":  regions,
        "themes":   themes,
        "score_6m": score_6m,
        "score_1y": score_1y,
        "score_5y": score_5y,
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

def run_scan(universe: list, out_path: str = "screen_results.json") -> None:
    total      = len(universe)
    rows       = []
    failed_syms = []
    t_start    = time.monotonic()

    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Starting scan of {total} tickers.  Output -> {out_path}")
    print("-" * 60)

    for idx, entry in enumerate(universe, start=1):
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
        if idx < total:
            time.sleep(SLEEP_BETWEEN)

    # Write output.
    elapsed_total = time.monotonic() - t_start
    result = {
        "generated_at":  as_of,
        "universe_size": total,
        "rows":          rows,
    }
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
        print(f"\nWrote {out_path}")
    except Exception as exc:
        print(f"\nERROR writing {out_path}: {exc}")

    # Summary.
    ok_count      = sum(1 for r in rows if r["data_status"] == "ok")
    partial_count = sum(1 for r in rows if r["data_status"] == "partial")
    failed_count  = len(failed_syms)
    print("\n" + "=" * 60)
    print(f"SCAN COMPLETE  total={total}  ok={ok_count}  "
          f"partial={partial_count}  failed={failed_count}  "
          f"elapsed={elapsed_total:.1f}s")
    if failed_syms:
        print(f"Failed symbols: {', '.join(failed_syms)}")
    else:
        print("No failures.")
    print("=" * 60)


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
        "--out", default="screen_results.json",
        help="Output JSON file (default: screen_results.json)",
    )
    args = parser.parse_args()

    universe = get_universe()
    if args.limit:
        universe = universe[:args.limit]

    try:
        run_scan(universe, out_path=args.out)
    except KeyboardInterrupt:
        print("\nInterrupted by user.  Partial results NOT saved.")
        sys.exit(1)


if __name__ == "__main__":
    main()
