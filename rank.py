"""
rank.py -- Stage 2b: rank and filter the screener's scan results.

Reads screen_results.json (written by scan.py) and turns it into a ranked
"top opportunities" list.  Pure Python, no network, no Streamlit -- so the
app (Stage 3) can import rank_rows() directly.

Usage:
    python rank.py                         # top 25, 1-year horizon
    python rank.py --horizon 5y --top 10
    python rank.py --theme ai --region IL
    python rank.py --min-upside 15 --max-beta 1.5
    python rank.py --agree-only            # our verdict AND analysts bullish
    python rank.py --json top.json         # also save the ranked list

HOW THE OPPORTUNITY SCORE WORKS (transparent on purpose)
--------------------------------------------------------
    opportunity = 60% x our verdict score (0..100, chosen horizon)
                + 40% x analyst-upside points (0..100)
                - risk penalty (0..20)

  * Analyst-upside points: implied upside to the mean price target, clamped
    to -20%..+50% and mapped linearly onto 0..100 (so +15% upside = 50 pts).
    No analyst coverage -> treated as a NEUTRAL +10% upside (close to the
    universe median), so uncovered stocks are neither rewarded nor punished.
  * Risk penalty: high beta (above 1.5) and heavy debt (debt-to-equity above
    200) each cost points.  Capped so risk can nudge, not dominate.

It only re-weights CURRENT data.  It is not a prediction, and not advice.
"""

import argparse
import json
import sys

# --- Tunable knobs ---------------------------------------------------------

WEIGHT_OURS    = 0.6
WEIGHT_ANALYST = 0.4

UPSIDE_FLOOR = -20.0   # % upside that maps to 0 analyst points
UPSIDE_CAP   = 50.0    # % upside that maps to 100 (more is treated as noise)
NEUTRAL_UPSIDE = 10.0  # assumed upside when no analyst covers the stock

BETA_SAFE        = 1.5   # beta above this starts to cost points
BETA_PENALTY     = 10.0  # points per 1.0 of beta above BETA_SAFE
BETA_PENALTY_MAX = 10.0
D2E_SAFE         = 200.0  # debt-to-equity (%) above this costs points
D2E_PENALTY      = 5.0
RISK_PENALTY_MAX = 20.0

HORIZONS = ("6m", "1y", "5y")
BULLISH_LABELS = ("Buy", "Strong Buy")

# Fallback when a row only has a label (older scan files without score_num):
# the midpoint of each label's band in engine._label_for_score.
_LABEL_MIDPOINT = {"Sell": 17.5, "Hold": 45.0, "Buy": 65.0, "Strong Buy": 87.5}


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def our_score(row: dict, horizon: str):
    """Our 0..100 verdict score for this horizon, or None."""
    val = (row.get("score_num") or {}).get(horizon)
    if _num(val):
        return float(val)
    return _LABEL_MIDPOINT.get(row.get(f"score_{horizon}"))


def upside_points(upside_pct):
    """Map analyst implied upside (%) onto 0..100, clamped."""
    if not _num(upside_pct):
        return None
    clamped = max(UPSIDE_FLOOR, min(UPSIDE_CAP, upside_pct))
    return (clamped - UPSIDE_FLOOR) / (UPSIDE_CAP - UPSIDE_FLOOR) * 100


def risk_penalty(row: dict):
    """(points, reasons) -- the risk deduction and why."""
    risk = row.get("risk") or {}
    points, reasons = 0.0, []
    beta = risk.get("beta")
    if _num(beta) and beta > BETA_SAFE:
        p = min(BETA_PENALTY_MAX, (beta - BETA_SAFE) * BETA_PENALTY)
        points += p
        reasons.append(f"beta {beta:.2f} (-{p:.1f})")
    d2e = risk.get("debt_to_equity")
    if _num(d2e) and d2e > D2E_SAFE:
        points += D2E_PENALTY
        reasons.append(f"debt/equity {d2e:.0f} (-{D2E_PENALTY:.0f})")
    return min(points, RISK_PENALTY_MAX), reasons


def score_row(row: dict, horizon: str = "1y"):
    """
    Return a ranked-entry dict for one scan row, or None if it can't be
    ranked (failed scan, or no verdict for this horizon).
    """
    if row.get("data_status") == "failed":
        return None
    ours = our_score(row, horizon)
    if ours is None:
        return None

    up_pct = row.get("analyst_implied_upside_pct")
    up_pts = upside_points(up_pct)
    notes = []
    if up_pts is None:
        up_pts = upside_points(NEUTRAL_UPSIDE)
        notes.append(f"no analyst target -- assumed neutral "
                     f"{NEUTRAL_UPSIDE:+.0f}%")
    blended = WEIGHT_OURS * ours + WEIGHT_ANALYST * up_pts

    penalty, risk_notes = risk_penalty(row)
    label = row.get(f"score_{horizon}")
    agree = label in BULLISH_LABELS and _num(up_pct) and up_pct >= 10

    return {
        "symbol":      row["symbol"],
        "themes":      row.get("themes", []),
        "regions":     row.get("regions", []),
        "horizon":     horizon,
        "opportunity": round(blended - penalty, 1),
        "our_label":   label,
        "our_score":   round(ours, 1),
        "upside_pct":  round(up_pct, 1) if _num(up_pct) else None,
        "risk_penalty": round(penalty, 1),
        "agree":       bool(agree),
        "price":       row.get("current_price"),
        "data_status": row.get("data_status"),
        "notes":       notes + risk_notes,
    }


def rank_rows(rows: list, horizon: str = "1y", theme=None, region=None,
              min_upside=None, max_beta=None, agree_only=False,
              include_partial=True, top=None) -> list:
    """Filter + score + sort scan rows; best opportunity first."""
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}")
    out = []
    for row in rows:
        if theme and theme not in row.get("themes", []):
            continue
        if region and region not in row.get("regions", []):
            continue
        if not include_partial and row.get("data_status") != "ok":
            continue
        beta = (row.get("risk") or {}).get("beta")
        if max_beta is not None and _num(beta) and beta > max_beta:
            continue
        entry = score_row(row, horizon)
        if entry is None:
            continue
        if min_upside is not None and (entry["upside_pct"] is None
                                       or entry["upside_pct"] < min_upside):
            continue
        if agree_only and not entry["agree"]:
            continue
        out.append(entry)
    # Ties broken by symbol so the order is stable run-to-run.
    out.sort(key=lambda e: (-e["opportunity"], e["symbol"]))
    for i, e in enumerate(out, start=1):
        e["rank"] = i
    return out[:top] if top else out


def load_results(path: str = "screen_results.json") -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fmt(x, spec, dash="  -"):
    return format(x, spec) if _num(x) else dash


def print_table(ranked: list) -> None:
    print(f"{'#':>3}  {'Symbol':10s} {'Opp':>5}  {'Ours':>5} {'Label':10s} "
          f"{'Upside':>7}  {'Risk':>4}  {'Agree':5}  Themes")
    print("-" * 78)
    for e in ranked:
        themes = ",".join(t for t in e["themes"] if t != "core") or "core"
        print(f"{e['rank']:3d}  {e['symbol']:10s} {e['opportunity']:5.1f}  "
              f"{e['our_score']:5.1f} {str(e['our_label']):10s} "
              f"{_fmt(e['upside_pct'], '6.1f'):>6}%  "
              f"{_fmt(-e['risk_penalty'] if e['risk_penalty'] else None, '4.1f', '   '):>4}  "
              f"{'yes' if e['agree'] else '':5s}  {themes}")


def main():
    p = argparse.ArgumentParser(description="Rank screener scan results")
    p.add_argument("--in", dest="inp", default="screen_results.json")
    p.add_argument("--horizon", choices=HORIZONS, default="1y")
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--theme")
    p.add_argument("--region", help="e.g. US or IL")
    p.add_argument("--min-upside", type=float, metavar="PCT")
    p.add_argument("--max-beta", type=float)
    p.add_argument("--agree-only", action="store_true",
                   help="Only stocks where our verdict is Buy+ and analysts "
                        "see >=10%% upside")
    p.add_argument("--ok-only", action="store_true",
                   help="Skip rows with partial data")
    p.add_argument("--json", metavar="PATH", help="Also write ranked list")
    a = p.parse_args()

    try:
        data = load_results(a.inp)
    except (OSError, ValueError) as exc:
        print(f"Could not read {a.inp}: {exc}\nRun scan.py first.")
        sys.exit(1)

    rows = data.get("rows", [])
    ranked = rank_rows(rows, horizon=a.horizon, theme=a.theme,
                       region=a.region, min_upside=a.min_upside,
                       max_beta=a.max_beta, agree_only=a.agree_only,
                       include_partial=not a.ok_only, top=a.top)

    status = "" if data.get("complete", True) else "  (INCOMPLETE scan)"
    print(f"Scan {data.get('generated_at')}: {len(rows)}/"
          f"{data.get('universe_size')} tickers{status}.  "
          f"Horizon {a.horizon}, showing {len(ranked)}.\n")
    print_table(ranked)
    print("\nNot financial advice -- a re-weighting of current public data.")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"generated_at": data.get("generated_at"),
                       "horizon": a.horizon, "ranked": ranked},
                      fh, indent=2, ensure_ascii=False)
        print(f"Wrote {a.json}")


if __name__ == "__main__":
    main()
