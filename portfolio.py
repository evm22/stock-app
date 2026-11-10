"""
portfolio.py -- the user's real portfolio: trades, holdings, profit, tax.

Pure Python, no Streamlit and no network: app.py passes in quotes and FX
rates, this module does the arithmetic, so everything here is unit-tested
offline (test_portfolio.py).

What's stored is the list of TRADES (buys and sells), in data/portfolio.json
through data_store (a local folder now, the private GitHub repo once it's
connected):

    {"version": 1, "transactions": [
        {"id": "a1b2c3", "symbol": "NVDA", "side": "buy", "qty": 20,
         "price": 118.0, "currency": "USD", "date": "2026-01-14"}, ...]}

The holdings are worked out from the trades (compute -> as_holdings). A
holding is a small dict:
    {"symbol": "NVDA", "qty": 20.0, "cost": 118.0}
`cost` is the average buy price PER SHARE in the stock's MAJOR currency unit
(dollars, shekels -- never agorot).

Money rules (kept simple and stated in the UI):
  * Average cost: a sale doesn't change the average cost of what's left.
  * Realized profit on a sale = (sale price - average cost) x shares sold.
  * Tax is a rough estimate only: 25% of the year's net realized profit in
    each currency, no fees, no inflation adjustment, no FX at trade dates.

Money units: Yahoo quotes Tel-Aviv stocks in agorot ("ILA", 1/100 shekel)
and London stocks in pence ("GBp"). to_major() converts those to ₪ / £ so
the app shows (and the user types) normal prices.
"""

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

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


def money(value, currency, decimals=2, signed=False):
    """'₪1,234.50' / '$182.40' / '1,234.50 CHF'. None -> '—'.
    signed=True puts a '+' in front of a gain."""
    if not _num(value):
        return "—"
    sym = CURRENCY_SYMBOLS.get(currency)
    body = f"{abs(value):,.{decimals}f}"
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}{sym}{body}" if sym else f"{sign}{body} {currency}".strip()


def fx_symbol(currency):
    """Yahoo symbol for currency -> ILS, e.g. 'USDILS=X'. None for ILS."""
    if not currency or currency == HOME_CURRENCY:
        return None
    return f"{currency}{HOME_CURRENCY}=X"


# --- holdings lists (the computed view; also the old browser format) -------

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


# --- trades (what's stored) -------------------------------------------------

STORE_PATH = "data/portfolio.json"
TAX_RATE = 0.25
SIDES = ("buy", "sell")


class PortfolioError(ValueError):
    """A trade that can't be right (e.g. selling more than you hold)."""


def empty():
    return {"version": 1, "transactions": []}


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


def as_holdings(result):
    """The computed holdings as the simple list position()/summarize() take,
    in the order the stocks were first bought."""
    return [{"symbol": h.symbol, "qty": h.qty, "cost": h.avg_cost}
            for h in result.holdings.values()]


def currency_of(data, symbol):
    """The currency the stock's trades were entered in ('' if none)."""
    sym = _norm(symbol)
    return next((t.get("currency", "") for t in data.get("transactions", [])
                 if t.get("symbol") == sym), "")


def from_holdings(data, holdings, currencies, when=None):
    """One-time move of the old browser portfolio (qty + average cost, no
    dates) into trades: one opening 'buy' per stock, dated `when` (today).
    Stocks that already have trades are left alone. Returns new data."""
    when = (when or date.today()).isoformat()
    have = {t["symbol"] for t in data.get("transactions", [])}
    txs = list(data.get("transactions", []))
    for h in holdings:
        if h["symbol"] in have or h["qty"] <= 0 or h["cost"] <= 0:
            continue
        txs.append({"id": uuid.uuid4().hex[:10], "symbol": h["symbol"], "side": "buy",
                    "qty": float(h["qty"]), "price": float(h["cost"]),
                    "currency": (currencies.get(h["symbol"]) or "").upper(),
                    "date": when, "note": "opening"})
    new = {"version": 1, "transactions": txs}
    compute(new)
    return new


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


# --- backup / restore (one JSON file the user keeps) ------------------------

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
        if t.get("note"):
            tx["note"] = str(t["note"])
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
