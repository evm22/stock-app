"""
events.py -- "this week in my portfolio" (mockup item 19): upcoming earnings
reports and dividends for the stocks I hold.

  * fetch_calendar(symbol) -> the next dates from Yahoo's free calendar
                              (network; never raises)
  * upcoming(...)          -> pure Python: the events in the next N days,
                              sorted, with a Hebrew line for each
                              (tested in test_events.py)

Dividend amounts: Yahoo's "last dividend" per share is reliable for US stocks
but not for Tel Aviv (.TA) ones (the units don't match), so the estimate
"≈ $X for you" is shown only when the stock isn't on the TASE.
"""

from datetime import date, datetime, timedelta

DAYS_AHEAD = 7
HE_WEEKDAYS = ["ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳", "א׳"]   # Monday = 0


def _as_date(x):
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, (list, tuple)) and x:
        return _as_date(x[0])
    if isinstance(x, str):
        try:
            return date.fromisoformat(x[:10])
        except ValueError:
            return None
    return None


def fetch_calendar(symbol):
    """{"earnings", "ex_dividend", "dividend_pay": iso date or None,
        "dividend_per_share": float or None}. Empty values on any error."""
    out = {"earnings": None, "ex_dividend": None, "dividend_pay": None,
           "dividend_per_share": None}
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        cal = t.calendar or {}
        if isinstance(cal, dict):
            for key, name in (("earnings", "Earnings Date"), ("ex_dividend", "Ex-Dividend Date"),
                              ("dividend_pay", "Dividend Date")):
                d = _as_date(cal.get(name))
                out[key] = d.isoformat() if d else None
        if out["ex_dividend"] or out["dividend_pay"]:
            v = (t.info or {}).get("lastDividendValue")
            if isinstance(v, (int, float)) and v > 0:
                out["dividend_per_share"] = float(v)
    except Exception:
        pass
    return out


def day_he(d, today):
    """'היום' / 'מחר' / 'יום ד׳ 12.11'."""
    if d == today:
        return "היום"
    if d == today + timedelta(days=1):
        return "מחר"
    return f"יום {HE_WEEKDAYS[d.weekday()]} {d:%d.%m}"


def upcoming(calendars, qty, today=None, days=DAYS_AHEAD):
    """
    calendars : {symbol: fetch_calendar() dict}
    qty       : {symbol: shares held}
    -> [{"date", "when", "symbol", "kind", "what", "amount"}] sorted by date,
       only events from today to today + days. "amount" is (value, currency)
       for a dividend payment when it can be estimated, else None.
    """
    today = today or date.today()
    end = today + timedelta(days=days)
    out = []
    for sym, cal in (calendars or {}).items():
        cal = cal or {}
        per_share = cal.get("dividend_per_share")
        trusted = per_share and not sym.upper().endswith(".TA")
        for kind in ("earnings", "ex_dividend", "dividend_pay"):
            d = _as_date(cal.get(kind))
            if d is None or not (today <= d <= end):
                continue
            amount = None
            if kind == "earnings":
                what = "דוח רבעוני"
            elif kind == "ex_dividend":
                what = "יום אקס דיבידנד (מי שמחזיק לפני כן מקבל)"
                if trusted:
                    what = f"יום אקס דיבידנד, ⁦${per_share:.2f}⁩ למניה"
            else:
                what = "תשלום דיבידנד"
                if trusted and qty.get(sym):
                    amount = (per_share * qty[sym], "USD")
            out.append({"date": d.isoformat(), "when": day_he(d, today), "symbol": sym,
                        "kind": kind, "what": what, "amount": amount})
    order = {"earnings": 0, "ex_dividend": 1, "dividend_pay": 2}
    return sorted(out, key=lambda e: (e["date"], order[e["kind"]], e["symbol"]))


NEXT_HE = {"earnings": "דוח רבעוני", "ex_dividend": "יום אקס דיבידנד",
           "dividend_pay": "תשלום דיבידנד"}


def next_event(calendars, today=None):
    """The nearest future event of any kind -> (date, symbol, Hebrew kind) or None."""
    today = today or date.today()
    best = None
    for sym, cal in (calendars or {}).items():
        for kind in ("earnings", "ex_dividend", "dividend_pay"):
            d = _as_date((cal or {}).get(kind))
            if d and d >= today and (best is None or d < best[0]):
                best = (d, sym, NEXT_HE[kind])
    return best
