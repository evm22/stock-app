"""
portfolio.py -- the user's own trades and what they add up to.

Pure Python (no Streamlit), so it can be tested offline and used by the daily
job later. The app keeps the data with data_store (a local folder now, the
private GitHub repo once it's connected).

The stored data is a small JSON document:

    {"version": 1, "transactions": [
        {"id": "t1", "symbol": "NVDA", "side": "buy", "qty": 20,
         "price": 118.0, "currency": "USD", "date": "2026-01-14"},
        ...]}

Money rules (kept simple and stated in the UI):
  * Cost uses the AVERAGE price paid. A sale doesn't change the average cost
    of the shares that are left.
  * Realized profit on a sale = (sale price - average cost) x shares sold.
  * Tax is only a rough estimate: 25% of the year's net realized profit in
    each currency (gains minus losses), with no fees, inflation adjustment or
    shekel conversion at the trade dates.
  * Tel Aviv prices on Yahoo are in agorot ("ILA"); they're converted to
    shekels so they match what people type in.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

STORE_PATH = "data/portfolio.json"
TAX_RATE = 0.25
SIDES = ("buy", "sell")


class PortfolioError(ValueError):
    """A trade that can't be right (e.g. selling more than you hold)."""


def empty():
    return {"version": 1, "transactions": []}


def normalize_price(price, currency):
    """(price, currency) in a unit people use: agorot (ILA) -> shekels (ILS)."""
    if price is None:
        return None, currency
    cur = (currency or "").upper()
    if cur == "ILA":
        return price / 100.0, "ILS"
    return price, cur


def currency_symbol(currency):
    return {"ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£"}.get((currency or "").upper(), "")


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------

def _parse_date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def add_transaction(data, symbol, side, qty, price, currency, when, today=None):
    """Return a NEW data dict with the trade added (the input isn't changed).
    Raises PortfolioError for anything that doesn't make sense."""
    symbol = (symbol or "").strip().upper()
    side = (side or "").lower()
    today = today or date.today()
    if not symbol:
        raise PortfolioError("חסר סימול מניה.")
    if side not in SIDES:
        raise PortfolioError("סוג העסקה חייב להיות קנייה או מכירה.")
    try:
        qty = float(qty)
        price = float(price)
    except (TypeError, ValueError):
        raise PortfolioError("כמות ומחיר חייבים להיות מספרים.") from None
    if qty <= 0:
        raise PortfolioError("הכמות חייבת להיות גדולה מאפס.")
    if price <= 0:
        raise PortfolioError("המחיר חייב להיות גדול מאפס.")
    try:
        when = _parse_date(when)
    except ValueError:
        raise PortfolioError("התאריך לא תקין.") from None
    if when > today:
        raise PortfolioError("אי אפשר להזין עסקה בתאריך עתידי.")

    tx = {"id": uuid.uuid4().hex[:10], "symbol": symbol, "side": side,
          "qty": qty, "price": price, "currency": (currency or "").upper(),
          "date": when.isoformat()}
    new = {"version": 1, "transactions": list(data.get("transactions", [])) + [tx]}
    # Replaying everything catches a sale bigger than the holding at that date.
    compute(new)
    return new


def remove_transaction(data, tx_id):
    """Return a NEW data dict without that trade. Raises PortfolioError if
    removing it would leave a later sale with nothing to sell."""
    txs = [t for t in data.get("transactions", []) if t.get("id") != tx_id]
    new = {"version": 1, "transactions": txs}
    compute(new)
    return new


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class Holding:
    symbol: str
    currency: str
    qty: float = 0.0
    avg_cost: float = 0.0          # per share, in `currency`
    realized: float = 0.0          # all-time realized profit, in `currency`

    @property
    def cost_basis(self):
        return self.qty * self.avg_cost


@dataclass
class Realized:
    """One sale's realized profit."""
    tx_id: str
    symbol: str
    date: str
    qty: float
    price: float
    avg_cost: float
    currency: str

    @property
    def profit(self):
        return (self.price - self.avg_cost) * self.qty


@dataclass
class Result:
    holdings: dict = field(default_factory=dict)   # symbol -> Holding (qty > 0 only)
    realized: list = field(default_factory=list)   # list[Realized], oldest first


def _sorted(txs):
    # Same-day: buys before sells, so "bought and sold today" works.
    return sorted(txs, key=lambda t: (t.get("date", ""), 0 if t.get("side") == "buy" else 1))


def compute(data):
    """Replay every trade in date order -> current holdings + realized sales."""
    book = {}
    realized = []
    for t in _sorted(data.get("transactions", [])):
        sym = t["symbol"]
        h = book.get(sym)
        if h is None:
            h = book[sym] = Holding(sym, t.get("currency", ""))
        qty, price = float(t["qty"]), float(t["price"])
        if t["side"] == "buy":
            total = h.qty * h.avg_cost + qty * price
            h.qty += qty
            h.avg_cost = total / h.qty
        else:
            if qty > h.qty + 1e-9:
                raise PortfolioError(
                    f"אי אפשר למכור {qty:g} מניות {sym} ב-{t['date']}: "
                    f"באותו יום היו בתיק רק {h.qty:g}.")
            r = Realized(t.get("id", ""), sym, t["date"], qty, price, h.avg_cost, h.currency)
            realized.append(r)
            h.realized += r.profit
            h.qty -= qty
            if h.qty < 1e-9:
                h.qty, h.avg_cost = 0.0, 0.0
    return Result({s: h for s, h in book.items() if h.qty > 0}, realized)


def last_year(transactions, today=None):
    """Trades from the last 365 days, newest first."""
    today = today or date.today()
    start = (today - timedelta(days=365)).isoformat()
    picked = [t for t in transactions if t.get("date", "") >= start]
    return list(reversed(_sorted(picked)))


def realized_in_year(result, year):
    """{currency: realized profit} for sales in calendar `year`."""
    out = {}
    for r in result.realized:
        if r.date.startswith(str(year)):
            out[r.currency] = out.get(r.currency, 0.0) + r.profit
    return out


def tax_estimate(profit):
    """Rough 25% on a positive profit; 0 on a loss."""
    return max(0.0, profit) * TAX_RATE


def value_positions(result, prices, fx_to_ils=None):
    """
    Price each holding. `prices` maps symbol -> (price, currency, change_pct)
    straight from the quote (ILA is converted here). `fx_to_ils` maps a
    currency to its shekel rate (e.g. {"USD": 3.72}); when a rate is missing
    the position is still listed but left out of the shekel total.

    Returns (rows, totals). Each row: symbol, qty, price, currency, value,
    cost, pl, pl_pct, day_pct, value_ils. Totals: value_ils, cost_ils,
    pl_ils, day_ils, missing (symbols left out of the totals).
    """
    fx = {"ILS": 1.0}
    fx.update(fx_to_ils or {})
    rows, missing = [], []
    totals = {"value_ils": 0.0, "cost_ils": 0.0, "pl_ils": 0.0, "day_ils": 0.0}
    for sym, h in sorted(result.holdings.items()):
        raw = prices.get(sym)
        price, cur, day = (None, h.currency, None)
        if raw:
            price, cur = normalize_price(raw[0], raw[1])
            day = raw[2] if len(raw) > 2 else None
        value = h.qty * price if price is not None else None
        cost = h.cost_basis
        pl = value - cost if value is not None else None
        rate = fx.get((cur or h.currency or "").upper())
        value_ils = value * rate if (value is not None and rate) else None
        rows.append({"symbol": sym, "qty": h.qty, "price": price, "currency": cur or h.currency,
                     "avg_cost": h.avg_cost, "value": value, "cost": cost, "pl": pl,
                     "pl_pct": (pl / cost * 100) if (pl is not None and cost) else None,
                     "day_pct": day, "value_ils": value_ils})
        if value_ils is None:
            missing.append(sym)
            continue
        totals["value_ils"] += value_ils
        totals["cost_ils"] += cost * rate
        totals["pl_ils"] += pl * rate
        if day is not None:
            totals["day_ils"] += value_ils - value_ils / (1 + day / 100)
    totals["missing"] = missing
    return rows, totals


# ---------------------------------------------------------------------------
# Backup / restore (one JSON file the user keeps)
# ---------------------------------------------------------------------------

BACKUP_KIND = "stock-app-backup"


def make_backup(data, watchlist, now=None):
    """The backup file's text: the trades plus the followed stocks."""
    now = now or datetime.now()
    doc = {"kind": BACKUP_KIND, "version": 1,
           "created": now.isoformat(timespec="seconds"),
           "portfolio": {"version": 1, "transactions": list(data.get("transactions", []))},
           "watchlist": list(watchlist or [])}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _clean_tx(t):
    try:
        tx = {"id": str(t.get("id") or uuid.uuid4().hex[:10]),
              "symbol": str(t["symbol"]).strip().upper(),
              "side": str(t["side"]).lower(),
              "qty": float(t["qty"]), "price": float(t["price"]),
              "currency": str(t.get("currency") or "").upper(),
              "date": _parse_date(t["date"]).isoformat()}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PortfolioError("בקובץ יש עסקה חסרה או לא תקינה.") from None
    if not tx["symbol"] or tx["side"] not in SIDES or tx["qty"] <= 0 or tx["price"] <= 0:
        raise PortfolioError(f"בקובץ יש עסקה לא תקינה ({tx['symbol'] or '?'}).")
    return tx


def parse_backup(text):
    """Backup text -> (portfolio data, watchlist). Raises PortfolioError if the
    file isn't a backup of this app or its trades don't add up."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        raise PortfolioError("הקובץ הזה לא נראה כמו גיבוי (זה לא JSON).") from None
    if not isinstance(doc, dict) or doc.get("kind") != BACKUP_KIND:
        raise PortfolioError("הקובץ הזה לא נראה כמו גיבוי של האפליקציה.")
    txs = (doc.get("portfolio") or {}).get("transactions") or []
    if not isinstance(txs, list):
        raise PortfolioError("רשימת העסקאות בקובץ לא תקינה.")
    data = {"version": 1, "transactions": [_clean_tx(t) for t in txs]}
    compute(data)  # raises if a sale is bigger than the holding
    wl = doc.get("watchlist") or []
    watchlist = [str(s).strip().upper() for s in wl if str(s).strip()] if isinstance(wl, list) else []
    return data, watchlist
