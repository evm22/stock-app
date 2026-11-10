"""
portfolio.py -- the user's real portfolio (redesign Stage 3, mockup item 4).

Pure Python, no Streamlit and no network: app.py passes in quotes and FX
rates, this module does the arithmetic, so everything here is unit-tested
offline (test_portfolio.py).

A holding is a small dict:
    {"symbol": "NVDA", "qty": 20.0, "cost": 118.0}
`cost` is the average buy price PER SHARE in the stock's MAJOR currency unit
(dollars, shekels -- never agorot), exactly what the user typed.

Money units: Yahoo quotes Tel-Aviv stocks in agorot ("ILA", 1/100 shekel)
and London stocks in pence ("GBp"). to_major() converts those to ₪ / £ so
the app shows (and the user types) normal prices.
"""

import json
import math

# Yahoo "minor unit" currencies -> (major currency, divisor)
_MINOR_UNITS = {"ILA": ("ILS", 100.0), "ILX": ("ILS", 100.0),
                "GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0)}

CURRENCY_SYMBOLS = {"ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£"}

HOME_CURRENCY = "ILS"   # portfolio totals are shown in shekels


def _num(x):
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x))


def to_major(price, currency):
    """(price, currency) in the major unit: 13580 ILA -> (135.80, 'ILS')."""
    major, div = _MINOR_UNITS.get(currency or "", (currency or "", 1.0))
    if not _num(price):
        return None, major
    return price / div, major


def money(value, currency, decimals=2):
    """'₪1,234.50' / '$182.40' / '1,234.50 CHF'. None -> '—'."""
    if not _num(value):
        return "—"
    sym = CURRENCY_SYMBOLS.get(currency)
    body = f"{abs(value):,.{decimals}f}"
    sign = "-" if value < 0 else ""
    return f"{sign}{sym}{body}" if sym else f"{sign}{body} {currency}".strip()


def fx_symbol(currency):
    """Yahoo symbol for currency -> ILS, e.g. 'USDILS=X'. None for ILS."""
    if not currency or currency == HOME_CURRENCY:
        return None
    return f"{currency}{HOME_CURRENCY}=X"


# --- editing ---------------------------------------------------------------

def _norm(symbol):
    return (symbol or "").strip().upper()


def parse(raw):
    """Holdings from stored JSON (or a list). Bad / partial data -> skipped."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    out = []
    for h in data or []:
        try:
            sym, qty, cost = _norm(h["symbol"]), float(h["qty"]), float(h["cost"])
        except (KeyError, TypeError, ValueError):
            continue
        if sym and _num(qty) and _num(cost) and qty > 0 and cost >= 0:
            out.append({"symbol": sym, "qty": qty, "cost": cost})
    return out


def dumps(holdings):
    return json.dumps(holdings, ensure_ascii=False)


def find(holdings, symbol):
    sym = _norm(symbol)
    return next((h for h in holdings if h["symbol"] == sym), None)


def upsert(holdings, symbol, qty, cost):
    """Set a holding's quantity and average cost (new list). qty <= 0 removes."""
    sym = _norm(symbol)
    rest = [h for h in holdings if h["symbol"] != sym]
    if not sym or not _num(qty) or qty <= 0:
        return rest
    if not _num(cost) or cost < 0:
        raise ValueError("cost must be a number >= 0")
    old = find(holdings, sym)
    new = {"symbol": sym, "qty": float(qty), "cost": float(cost)}
    if old is None:
        return rest + [new]
    return [new if h["symbol"] == sym else h for h in holdings]  # keep order


def remove(holdings, symbol):
    sym = _norm(symbol)
    return [h for h in holdings if h["symbol"] != sym]


# --- valuation --------------------------------------------------------------

def position(holding, price_major, currency, change_pct=None, fx_to_home=None):
    """
    One row of the portfolio. price_major is the current price in the major
    unit (see to_major). fx_to_home converts `currency` to ₪ (None = unknown;
    1.0 for ILS). Values that can't be computed are None.
    """
    qty, cost = holding["qty"], holding["cost"]
    row = {"symbol": holding["symbol"], "qty": qty, "cost": cost,
           "currency": currency, "price": price_major if _num(price_major) else None,
           "value": None, "pnl": None, "pnl_pct": None, "day": None,
           "value_home": None, "cost_home": None, "day_home": None}
    if not _num(price_major):
        return row
    value = qty * price_major
    basis = qty * cost
    row["value"] = value
    row["pnl"] = value - basis
    row["pnl_pct"] = (value / basis - 1) * 100 if basis > 0 else None
    if _num(change_pct) and change_pct > -100:
        prev = value / (1 + change_pct / 100)
        row["day"] = value - prev
    rate = 1.0 if currency == HOME_CURRENCY else fx_to_home
    if _num(rate):
        row["value_home"] = value * rate
        row["cost_home"] = basis * rate      # cost at TODAY's rate (no FX history yet)
        row["day_home"] = row["day"] * rate if row["day"] is not None else None
    return row


def summarize(rows):
    """Totals in ₪ over the rows that have a ₪ value. Returns a dict."""
    priced = [r for r in rows if r["value_home"] is not None]
    value = sum(r["value_home"] for r in priced)
    cost = sum(r["cost_home"] for r in priced)
    day_rows = [r for r in priced if r["day_home"] is not None]
    day = sum(r["day_home"] for r in day_rows)
    prev = value - day
    return {
        "value": value if priced else None,
        "cost": cost if priced else None,
        "pnl": (value - cost) if priced else None,
        "pnl_pct": ((value / cost - 1) * 100) if (priced and cost > 0) else None,
        "day": day if day_rows else None,
        "day_pct": (day / prev * 100) if (day_rows and prev > 0) else None,
        "missing": [r["symbol"] for r in rows if r["value_home"] is None],
        "count": len(rows),
    }
