"""
ai_insights.py -- the numbers behind the AI features (mockup items 14-15).

Rule of the mockup: the CODE computes every number; the AI only writes words
(strengths, weaknesses, why a pick is interesting). This module is the code
half and is pure Python (tested in test_ai_insights.py):

  * portfolio_facts()  -> weights, biggest position, share in Israel,
                          value-weighted average buy score (item 14)
  * pick_candidates()  -> the nightly scan's strongest stocks for the short
                          term (6-month view) and the long term (5-year view),
                          10 each, out of ~611 (item 15). Gemini then chooses
                          2-3 per list from THESE ONLY.
  * clean_picks()      -> keep only valid picks from the candidates
  * fallback_picks()   -> code-only picks when there is no AI key
"""

import rank

SHORT_HORIZON, LONG_HORIZON = "6m", "5y"
CANDIDATES_PER_LIST = 10
MAX_PICKS = 3


def portfolio_facts(rows, scores=None, names=None):
    """
    rows   : portfolio.position() rows (need symbol, currency, value_home)
    scores : {symbol: buy score int or None}
    names  : {symbol: display name}
    """
    scores, names = scores or {}, names or {}
    priced = [r for r in rows if r.get("value_home")]
    total = sum(r["value_home"] for r in priced)
    if not priced or total <= 0:
        return {"count": len(rows), "total": None, "weights": [], "top": None,
                "il_pct": None, "avg_score": None, "winners": 0, "losers": 0}
    weights = sorted(((r["symbol"], names.get(r["symbol"], r["symbol"]),
                       r["value_home"] / total * 100) for r in priced),
                     key=lambda w: -w[2])
    il = sum(r["value_home"] for r in priced if r.get("currency") == "ILS") / total * 100
    scored = [(r["value_home"], scores.get(r["symbol"])) for r in priced
              if isinstance(scores.get(r["symbol"]), (int, float))]
    sw = sum(v for v, _ in scored)
    avg = round(sum(v * s for v, s in scored) / sw) if sw > 0 else None
    return {
        "count": len(rows), "total": total, "weights": weights,
        "top": weights[0], "il_pct": il, "avg_score": avg,
        "winners": sum(1 for r in priced if (r.get("pnl") or 0) > 0),
        "losers": sum(1 for r in priced if (r.get("pnl") or 0) < 0),
    }


def facts_for_ai(facts):
    """A small, rounded dict for the AI prompt (no exact money amounts)."""
    return {
        "number_of_stocks": facts["count"],
        "weights_pct": [{"stock": name, "symbol": sym, "pct": round(p, 1)}
                        for sym, name, p in facts["weights"]],
        "pct_in_israel_ils": round(facts["il_pct"], 1) if facts["il_pct"] is not None else None,
        "average_buy_score_-100_to_100": facts["avg_score"],
        "stocks_in_profit": facts["winners"], "stocks_in_loss": facts["losers"],
    }


def signature(facts):
    """Stable key for caching the AI overview: same holdings & rough weights."""
    return tuple((sym, round(p)) for sym, _, p in facts["weights"])


def _compact(e):
    return {"symbol": e["symbol"], "our_score": e["our_score"],
            "our_label": e["our_label"], "analyst_upside_pct": e["upside_pct"],
            "themes": [t for t in e.get("themes", []) if t != "core"],
            "region": (e.get("regions") or ["?"])[0], "risk_penalty": e["risk_penalty"]}


def pick_candidates(scan_rows, n=CANDIDATES_PER_LIST):
    """{"short": [...], "long": [...]} -- the code filter (fully-scored rows only)."""
    return {
        "short": [_compact(e) for e in rank.rank_rows(scan_rows, horizon=SHORT_HORIZON,
                                                      include_partial=False, top=n)],
        "long": [_compact(e) for e in rank.rank_rows(scan_rows, horizon=LONG_HORIZON,
                                                     include_partial=False, top=n)],
    }


def clean_picks(obj, candidates):
    """Keep only picks whose symbol is in that list's candidates, max 3,
    each with a non-empty reason. -> {"short": [...], "long": [...]}"""
    out = {}
    for key in ("short", "long"):
        allowed = {c["symbol"] for c in candidates.get(key, [])}
        picks, seen = [], set()
        for p in (obj or {}).get(key) or []:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("symbol") or "").strip().upper()
            why = str(p.get("why") or "").strip()
            if sym in allowed and sym not in seen and why:
                picks.append({"symbol": sym, "why": why})
                seen.add(sym)
            if len(picks) >= MAX_PICKS:
                break
        out[key] = picks
    return out


def fallback_picks(candidates, k=2):
    """No AI available: the top-k of each list, explained from the numbers."""
    def why(c):
        up = (f", אנליסטים {c['analyst_upside_pct']:+.0f}%"
              if c.get("analyst_upside_pct") is not None else "")
        return f"ציון המנוע {c['our_score']:.0f}/100{up} (נבחר לפי הקוד, בלי AI)."
    return {key: [{"symbol": c["symbol"], "why": why(c)} for c in candidates.get(key, [])[:k]]
            for key in ("short", "long")}
