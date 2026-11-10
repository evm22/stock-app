"""
benchmark.py -- "my portfolio vs the index" (mockup item 17).

The honest question is: "if I had put the SAME money into the index on the
SAME days, would I have more or less today?" So we run a shadow portfolio:

  * the window is the last year (or since the first trade, if later)
  * stocks already held when the window starts count as bought at that
    day's close, for both sides
  * every buy in the window: the same ₪ amount buys index units at the
    index close of that day
  * every sale of X% of a stock's shares: the shadow sells X% of the index
    units that were bought for that stock

Gain = value now + cash taken out - cash put in, for each side; shown as a
percent of the cash put in. Foreign money is converted at today's rate (like
the rest of the portfolio screen), so only the stock / index moves count.

Pure Python, no Streamlit / network (tested in test_benchmark.py).
"""

import bisect
from datetime import date, timedelta

INDEXES = {
    "ta125": {"symbol": "^TA125.TA", "name": "ת״א 125"},
    "sp500": {"symbol": "^GSPC", "name": "S&P 500"},
}
WINDOW_DAYS = 365
MIN_POINTS = 5   # fewer trading days than this -> too early to compare


class _Series:
    """Sorted (iso_date, close) pairs with 'last close on or before d'."""

    def __init__(self, pairs):
        pairs = sorted((d, float(c)) for d, c in pairs if c is not None and c > 0)
        self.dates = [d for d, _ in pairs]
        self.closes = [c for _, c in pairs]

    def at(self, d):
        i = bisect.bisect_right(self.dates, d) - 1
        return self.closes[i] if i >= 0 else None

    def __bool__(self):
        return bool(self.dates)


def _sort(txs):
    return sorted(txs, key=lambda t: (t.get("date", ""), 0 if t.get("side") == "buy" else 1))


def compare(transactions, closes, fx, index_closes, today=None, window_days=WINDOW_DAYS):
    """
    transactions : portfolio data["transactions"] (major-unit prices)
    closes       : {symbol: [(iso_date, close_in_major_unit), ...]}
    fx           : {symbol: rate to ₪} (1.0 for ₪ stocks)
    index_closes : [(iso_date, close), ...] for the index
    -> None if there is nothing to compare, else
       {"start", "my_pct", "idx_pct", "diff", "invested", "my_gain", "idx_gain",
        "points": [(iso_date, my_pct, idx_pct)], "missing": [symbols]}
    """
    today = today or date.today()
    idx = _Series(index_closes)
    if not idx or not transactions:
        return None
    window_start = (today - timedelta(days=window_days)).isoformat()
    days = [d for d in idx.dates if d >= window_start]
    if not days:
        return None

    series = {s: _Series(p) for s, p in (closes or {}).items()}
    usable = lambda s: bool(series.get(s)) and isinstance(fx.get(s), (int, float))
    missing = sorted({t["symbol"] for t in transactions if not usable(t["symbol"])})
    txs = _sort(t for t in transactions if usable(t["symbol"]))
    if not txs:
        return None

    qty, units, last_px = {}, {}, {}
    cash_in = cash_out = shadow_out = 0.0
    i = 0
    # Trades before the window: only the share count matters.
    while i < len(txs) and txs[i]["date"] < days[0]:
        t = txs[i]
        q = float(t["qty"]) * (1 if t["side"] == "buy" else -1)
        qty[t["symbol"]] = max(0.0, qty.get(t["symbol"], 0.0) + q)
        last_px[t["symbol"]] = float(t["price"])
        i += 1
    # Holdings on day one count as bought at that day's close, on both sides.
    opened = False
    for sym, q in qty.items():
        px = series[sym].at(days[0]) or last_px.get(sym)
        if q > 0 and px:
            v = q * px * fx[sym]
            cash_in += v
            units[sym] = v / idx.at(days[0])
            opened = True

    def price(sym, d):
        return series[sym].at(d) or last_px.get(sym) or 0.0

    points = []
    first_day = days[0] if opened else None
    for n, d in enumerate(days):
        last = n == len(days) - 1
        while i < len(txs) and (txs[i]["date"] <= d or last):
            t = txs[i]
            sym, q, px = t["symbol"], float(t["qty"]), float(t["price"])
            ix = idx.at(d)
            cash = q * px * fx[sym]
            last_px[sym] = px
            if t["side"] == "buy":
                cash_in += cash
                units[sym] = units.get(sym, 0.0) + cash / ix
                qty[sym] = qty.get(sym, 0.0) + q
            else:
                held = qty.get(sym, 0.0)
                frac = min(1.0, q / held) if held > 0 else 1.0
                sold = units.get(sym, 0.0) * frac
                units[sym] = units.get(sym, 0.0) - sold
                shadow_out += sold * ix
                cash_out += cash
                qty[sym] = max(0.0, held - q)
            if first_day is None:
                first_day = d
            i += 1
        if cash_in <= 0:
            continue
        my_val = sum(q * price(s, d) * fx[s] for s, q in qty.items() if q > 0)
        idx_val = sum(u for u in units.values()) * idx.at(d)
        my_gain = my_val + cash_out - cash_in
        idx_gain = idx_val + shadow_out - cash_in
        points.append((d, my_gain / cash_in * 100, idx_gain / cash_in * 100))

    if not points:
        return None
    _, my_pct, idx_pct = points[-1]
    return {"start": first_day, "my_pct": my_pct, "idx_pct": idx_pct,
            "diff": my_pct - idx_pct, "invested": cash_in,
            "my_gain": my_gain, "idx_gain": idx_gain,
            "points": points, "missing": missing}


def verdict_he(result, index_name):
    """One line: who won, by how many percentage points."""
    d = result["diff"]
    if abs(d) < 0.05:
        return f"תיקו עם {index_name}"
    return (f"ניצחת את {index_name} ב-⁦{abs(d):.1f}⁩ נקודות אחוז" if d > 0
            else f"{index_name} ניצח אותך ב-⁦{abs(d):.1f}⁩ נקודות אחוז")
