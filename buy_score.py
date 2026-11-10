"""
buy_score.py -- the "buy score" from -100 to +100 (mockup items 12-13).

    buy score = 50% our engine + 30% the analysts + 20% AI reading the news

Every part is on the same -100..+100 scale:
  * engine  : our 1-year verdict score (0..100, 50 = neutral) -> (s - 50) x 2
  * analysts: 60% their rating + 40% their price target
                rating : Yahoo's mean 1 (Strong Buy) .. 5 (Sell), 3 = neutral
                         -> (3 - mean) x 50
                target : implied upside to the mean target, clamped to
                         -25%..+25% -> x 4
  * AI news : Gemini's read of the latest headlines, already -100..+100

If a part is missing (no analyst coverage, AI not run yet), the others are
re-weighted to add up to 100%, and the explanation says so.

Pure Python, no Streamlit / network (tested in test_buy_score.py).
"""

import math

WEIGHTS = {"engine": 0.5, "analysts": 0.3, "ai": 0.2}
LEAN = 20   # |score| >= 20 -> "leaning buy / sell", otherwise neutral


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _clamp(x, lo=-100.0, hi=100.0):
    return max(lo, min(hi, x))


def engine_part(verdict_score):
    """0..100 verdict score -> -100..+100 (50 -> 0)."""
    return _clamp((verdict_score - 50) * 2) if _num(verdict_score) else None


def analyst_part(mean_rating, upside_pct):
    """Yahoo's 1..5 rating + implied upside % -> -100..+100 (None if neither)."""
    rating = _clamp((3 - mean_rating) * 50) if _num(mean_rating) else None
    target = _clamp(upside_pct, -25, 25) * 4 if _num(upside_pct) else None
    if rating is not None and target is not None:
        return 0.6 * rating + 0.4 * target
    return rating if rating is not None else target


def combine(engine=None, analysts=None, ai=None):
    """
    -> {"score": int or None, "label": str, "cls": "up"/"down"/"hold",
        "parts": [{"key", "weight", "used_weight", "value"}...], "formula": str,
        "missing": [keys]}
    """
    values = {"engine": engine, "analysts": analysts, "ai": ai}
    # Round each part first, so the score always equals the formula shown.
    present = {k: round(_clamp(v)) for k, v in values.items() if _num(v)}
    total_w = sum(WEIGHTS[k] for k in present)
    parts = []
    for key in ("engine", "analysts", "ai"):
        used = WEIGHTS[key] / total_w if key in present else 0.0
        parts.append({"key": key, "weight": WEIGHTS[key], "used_weight": used,
                      "value": round(present[key]) if key in present else None})
    if not present:
        return {"score": None, "label": "אין מספיק נתונים", "cls": "hold",
                "parts": parts, "formula": "", "missing": list(values)}
    raw = sum(present[k] * WEIGHTS[k] / total_w for k in present)
    score = int(round(raw))
    if score >= LEAN:
        label, cls = "נוטה לקנייה", "up"
    elif score <= -LEAN:
        label, cls = "נוטה למכירה", "down"
    else:
        label, cls = "ניטרלי", "hold"
    formula = " + ".join(f"{p['used_weight']*100:.3g}% × {p['value']:+d}"
                         for p in parts if p["value"] is not None) + f" = {score:+d}"
    return {"score": score, "label": label, "cls": cls, "parts": parts,
            "formula": formula, "missing": [k for k in values if k not in present]}


def from_engine_objects(verdict, analyst, ai_score=None):
    """Convenience: build the score from the engine's Verdict + AnalystConsensus."""
    hv = verdict.horizons.get("1Y") if (verdict is not None and verdict.found) else None
    eng = engine_part(hv.score) if (hv is not None and hv.enough_data) else None
    ana = None
    if analyst is not None and analyst.found and analyst.has_coverage:
        ana = analyst_part(analyst.mean, analyst.upside_pct)
    return combine(eng, ana, ai_score)
