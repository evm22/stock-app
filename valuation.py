"""
valuation.py -- what my portfolio is worth now, shared by the app and the
nightly run (one implementation, no Streamlit, no network of its own: the
caller passes in functions that fetch quotes / rates / closes).

  * value_rows(...)        -> one row per holding (portfolio.position rows),
                              including hand-valued funds whose value moves
                              with the index they follow since the last
                              manual update
  * symbols_to_score(...)  -> what the nightly run scores for the app
  * telegram_lines(...)    -> the personal part of the morning message
"""

import bisect
from datetime import date

import portfolio


def rate_on(closes, day_iso):
    """The rate on (or the last trading day before) day_iso; None before the
    first known day."""
    return _close_on(closes, day_iso)


def closes_major(history, currency):
    """engine.PriceHistory -> [(iso_date, close)] in the MAJOR unit (agorot ->
    ₪, pence -> £). [] when there's no data. Shared by the app and the job."""
    try:
        if history is None or not history.found:
            return []
        factor, _ = portfolio.to_major(1.0, currency or "")
        df = history.data[["Date", "Close"]].dropna()
        out = []
        for t, c in zip(df["Date"], df["Close"]):
            d = t.date().isoformat() if hasattr(t, "date") else str(t)[:10]
            out.append((d, float(c) * factor))
        return out
    except Exception:
        return []


_NAME_SUFFIXES = (" and Company", " & Co.", ", Inc.", " Inc.", " Inc", " Corporation",
                  " Corp.", " Corp", " Limited", " Ltd.", " Ltd", " plc", " PLC", " N.V.",
                  " S.A.", " AG", " SE", " Holdings", " Industries", " Co.",
                  " Company", " B.M.")


def short_name(name, symbol=""):
    """'Teva Pharmaceutical Industries Limited' -> 'Teva Pharmaceutical',
    'Eli Lilly and Company' -> 'Eli Lilly'."""
    name = (name or "").strip() or symbol
    for _ in range(3):
        for suf in _NAME_SUFFIXES:
            if name.endswith(suf) and len(name) > len(suf) + 2:
                name = name[: -len(suf)].rstrip(" ,&")
    return name


# --- funds that follow an index -----------------------------------------------

def _close_on(closes, day_iso):
    """Last close on or before day_iso from [(iso, close)], or None."""
    pairs = sorted((d, c) for d, c in (closes or []) if c)
    i = bisect.bisect_right([d for d, _ in pairs], day_iso) - 1
    return pairs[i][1] if i >= 0 else None


def index_move(m, quote_fn, closes_fn, today=None):
    """
    How the fund's index moved since the manual value was typed in.
    -> (factor, day_pct): factor = now / close on the update day (averaged
       over the symbols it follows), day_pct = today's average change.
    (1.0, None) when it follows nothing or there's no data. On the update
    day itself the typed value already includes the day's move -> factor 1.
    """
    syms = portfolio.tracks_of(m)
    today = (today or date.today()).isoformat()
    ratios, days = [], []
    for s in syms:
        q = quote_fn(s)
        if not q:
            continue
        price, change = q
        if change is not None:
            days.append(change)
        if m["updated"] >= today or not price:
            continue
        base = _close_on(closes_fn(s), m["updated"])
        if base:
            ratios.append(price / base)
    factor = sum(ratios) / len(ratios) if ratios else 1.0
    day = sum(days) / len(days) if days else None
    return factor, day


# --- valuing the whole portfolio ------------------------------------------------

def value_rows(data, quote_fn, fx_fn, closes_fn=None, today=None, fx_on=None):
    """
    fx_on(currency, iso_date) -> the rate on that day (optional): foreign
    stocks then cost their ₪ price on the buy days, not at today's rate.
    quote_fn(symbol)  -> (price_in_major_unit, change_pct, currency, name) or None
    fx_fn(currency)   -> rate to ₪ or None
    closes_fn(symbol) -> [(iso, close)] (for funds; may be None)
    -> (rows, names). Rows are portfolio.position() / manual_row() rows.
    """
    rows, names = [], {}
    holdings = portfolio.as_holdings(portfolio.compute(data))
    for h in holdings:
        q = quote_fn(h["symbol"])
        price = currency = change = None
        if q:
            price, change, currency, name = q
            if name:
                names[h["symbol"]] = short_name(name, h["symbol"])
        fx = fx_fn(currency) if currency else None
        rows.append(portfolio.position(h, price, currency or "", change, fx))
    if fx_on is not None:
        cost_home, _ = portfolio.home_costs(data, fx_on)
        for r in rows:
            if r["currency"] != portfolio.HOME_CURRENCY and r["symbol"] in cost_home \
                    and r["cost_home"] is not None:
                r["cost_home"] = cost_home[r["symbol"]]
                r["cost_at_buy_rate"] = True
    for m in data.get("manual", []):
        factor, day = (index_move(m, lambda s: (quote_fn(s) or (None, None))[:2],
                                  closes_fn, today)
                       if closes_fn else (1.0, None))
        r = portfolio.manual_row(m, factor, day)
        rows.append(r)
        names[r["symbol"]] = m["name"]
    return rows, names


# --- groups and allocation (home screen) ------------------------------------------

GROUPS = (("stock", "מניות"), ("etf", "קרנות סל"), ("fund", "קרנות (שווי ידני)"))
_ETF_WORDS = ("ETF", "TRUST", "SPDR", "ISHARES", "VANGUARD", "INVESCO", " FUND", "INDEX")


def kind_of(row, name=""):
    """'fund' (hand-valued), 'etf' (by its name) or 'stock'."""
    if row.get("manual"):
        return "fund"
    up = f" {name or ''} ".upper()
    return "etf" if any(w in up for w in _ETF_WORDS) else "stock"


def grouped(rows, names):
    """[(key, title, rows sorted by ₪ value, subtotal ₪ or None)] in GROUPS order,
    empty groups left out."""
    out = []
    for key, title in GROUPS:
        g = [r for r in rows if kind_of(r, names.get(r["symbol"], "")) == key]
        if not g:
            continue
        g.sort(key=lambda r: -(r.get("value_home") or 0))
        priced = [r["value_home"] for r in g if r.get("value_home") is not None]
        out.append((key, title, g, sum(priced) if priced else None))
    return out


def allocation(rows, names):
    """Shares of the ₪ total: {"kind": [(title, pct)], "currency": [(label, pct)]}.
    Hand-valued funds count as ₪ (that's the money they're held in)."""
    priced = [r for r in rows if r.get("value_home")]
    total = sum(r["value_home"] for r in priced)
    if total <= 0:
        return {"kind": [], "currency": []}
    titles = dict(GROUPS)
    by_kind, by_cur = {}, {}
    for r in priced:
        k = kind_of(r, names.get(r["symbol"], ""))
        by_kind[k] = by_kind.get(k, 0) + r["value_home"]
        c = {"USD": "דולר", "ILS": "שקל", "EUR": "אירו", "GBP": "ליש״ט"}.get(r["currency"], r["currency"] or "?")
        by_cur[c] = by_cur.get(c, 0) + r["value_home"]
    kind = [(titles[k], by_kind[k] / total * 100) for k, _ in GROUPS if k in by_kind]
    cur = sorted(((c, v / total * 100) for c, v in by_cur.items()), key=lambda x: -x[1])
    return {"kind": kind, "currency": cur}


def symbols_to_score(data, watchlist=()):
    """Stocks I hold + what my funds follow + my watchlist (no duplicates)."""
    out = []
    try:
        held = [h["symbol"] for h in portfolio.as_holdings(portfolio.compute(data))]
    except portfolio.PortfolioError:
        held = []
    for s in held + [t for m in data.get("manual", []) for t in portfolio.tracks_of(m)] \
            + list(watchlist or []):
        s = str(s).strip().upper()
        if s and s not in out:
            out.append(s)
    return out


# --- the personal part of the morning message -------------------------------------

def _ils(x, signed=False):
    s = f"₪{abs(x):,.0f}"
    if signed:
        s = ("+" if x >= 0 else "-") + s
    return s


def score_changes(old, new, names=None, min_points=15):
    """[(symbol, name, before, after)] for scores that moved >= min_points."""
    names = names or {}
    out = []
    for sym, n in (new or {}).items():
        o = (old or {}).get(sym)
        if isinstance(n, (int, float)) and isinstance(o, (int, float)) and abs(n - o) >= min_points:
            out.append((sym, names.get(sym, sym), int(o), int(n)))
    return sorted(out, key=lambda x: -abs(x[3] - x[2]))


def telegram_lines(rows, names, week_events=(), changes=(), max_moves=3):
    """
    Hebrew lines for the top of the morning message: my total, the last day,
    the biggest movers in my portfolio, this week's events and score changes.
    [] when there's nothing priced.
    """
    t = portfolio.summarize(rows)
    if t["value"] is None:
        return []
    lines = ["💼 התיק שלך", f"שווי: {_ils(t['value'])}"
             + (f" · רווח כולל {_ils(t['pnl'], True)} ({t['pnl_pct']:+.1f}%)"
                if t["pnl"] is not None and t["pnl_pct"] is not None else "")]
    if t["day"] is not None and t["day_pct"] is not None:
        lines.append(f"ביום המסחר האחרון: {_ils(t['day'], True)} ({t['day_pct']:+.2f}%)")
    moved = [r for r in rows if r.get("day_home") is not None and r.get("value_home")]
    moved.sort(key=lambda r: -abs(r["day_home"]))
    if moved[:max_moves]:
        parts = []
        for r in moved[:max_moves]:
            prev = r["value_home"] - r["day_home"]
            pct = r["day_home"] / prev * 100 if prev > 0 else 0
            parts.append(f"{names.get(r['symbol'], r['symbol'])} {pct:+.1f}%")
        lines.append("הכי זזו: " + " · ".join(parts))
    for e in list(week_events)[:4]:
        lines.append(f"📅 {e['when']}: {names.get(e['symbol'], e['symbol'])} – {e['what']}")
    for sym, name, before, after in list(changes)[:3]:
        lines.append(f"🔁 הציון של {name} השתנה: {before:+d} ← {after:+d}")
    return lines
