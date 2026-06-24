"""
engine.py — the data "engine" for the Stock Analysis App.

This module contains ALL the logic for looking up a stock and turning Yahoo
Finance's messy data into a clean, structured result. It is PURE PYTHON:
it does NOT import streamlit, so it can be tested on its own (see
test_engine.py) and reused anywhere.

The display layer (app.py) imports `get_stock_quote` from here and only worries
about showing the result on screen.
"""

import math
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd  # tables of price data (yfinance returns these too)
import yfinance as yf  # free Yahoo Finance data — our source for stock prices


@dataclass
class StockQuote:
    """
    A clean, structured result for one stock lookup.

    `found` is the clear "did we find it?" signal:
      - found == True  -> the other fields are filled in.
      - found == False -> the ticker couldn't be resolved (show the friendly
                          "couldn't find" message); other fields stay empty.

    `sources` records which data source each value came from (our debug info),
    e.g. {"price_candidates": {...}, "prev_candidates": {...}}.
    """
    found: bool
    symbol: str
    name: str = ""
    price: Optional[float] = None
    currency: str = ""
    previous_close: Optional[float] = None
    change_abs: Optional[float] = None        # day change, absolute (e.g. -1.23)
    change_pct: Optional[float] = None        # day change, percent  (e.g. -0.41)
    exchange: str = ""
    price_source: str = ""                    # where `price` came from
    prev_source: str = ""                     # where `previous_close` came from
    sources: dict = field(default_factory=dict)  # raw values tried (debug)


# --- Small helpers --------------------------------------------------------

def _is_number(value) -> bool:
    """
    True only if `value` is a real, usable number.

    yfinance fields are sometimes None, or a "NaN" (Not-a-Number) placeholder
    that *looks* like a number but isn't. We must reject both, otherwise we'd
    try to display empty data.
    """
    try:
        return value is not None and not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _safe(fast_info, key):
    """
    Read one value from yfinance's `fast_info` without crashing.

    `fast_info` behaves like a dictionary, but a missing key raises an error.
    This helper returns None instead, so the rest of the code stays simple.
    """
    try:
        return fast_info[key]
    except Exception:
        return None


def _first_valid(candidates, sources, sources_key):
    """
    Given a list of (source_name, value) pairs, return the first pair whose
    value is a real number — as (value, source_name). If none are valid,
    return (None, None).

    We also record every raw value we looked at into `sources` so the on-screen
    "Debug details" expander can show exactly what each source returned. This is
    how we diagnose tickers that misbehave (like AAPL did on Streamlit Cloud).
    """
    # Save the raw values (as text) for the debug panel.
    sources[sources_key] = {name: ("None" if val is None else str(val))
                            for name, val in candidates}
    for name, value in candidates:
        if _is_number(value):
            return float(value), name
    return None, None


# --- The main engine function --------------------------------------------

def get_stock_quote(ticker: str) -> StockQuote:
    """
    Look up one stock by its ticker symbol, robustly.

    Different tickers expose their price through different fields depending on
    the yfinance / pandas versions in use, so instead of trusting one field we
    try several sources in order and use the first that gives a real number.

    Returns a StockQuote. If no price could be found from ANY source, returns
    one with found=False (the "not found" signal).
    """
    symbol = (ticker or "").strip()
    display_symbol = symbol.upper()

    # A blank input is simply "not found".
    if not symbol:
        return StockQuote(found=False, symbol=display_symbol)

    stock = yf.Ticker(symbol)
    sources: dict = {}  # raw values, surfaced later in the Debug expander

    # --- Gather the three raw data sources once -------------------------
    # 1) fast_info: a lightweight bundle of quote fields.
    fast = stock.fast_info

    # 2) info: a heavier dictionary with lots of fields (can be slow/missing).
    try:
        info = stock.info
    except Exception:
        info = {}

    # 3) history: the last few days of prices, used as a final fallback.
    #    We keep only the "Close" column and drop any empty (NaN) rows.
    try:
        hist = stock.history(period="5d")
        closes = hist["Close"].dropna() if not hist.empty else None
    except Exception:
        closes = None

    hist_last = closes.iloc[-1] if closes is not None and len(closes) >= 1 else None
    hist_prev = closes.iloc[-2] if closes is not None and len(closes) >= 2 else None

    # --- Current price: try each source in order ------------------------
    price, price_source = _first_valid([
        ("fast_info.last_price",    _safe(fast, "last_price")),
        ("fast_info.lastPrice",     _safe(fast, "lastPrice")),
        ("info.regularMarketPrice", info.get("regularMarketPrice")),
        ("info.currentPrice",       info.get("currentPrice")),
        ("history.last_close",      hist_last),
    ], sources, "price_candidates")

    # If we couldn't get a price from ANY source, the ticker is unusable.
    if price is None:
        return StockQuote(found=False, symbol=display_symbol, sources=sources)

    # --- Previous close (for the day's change): same fallback idea ------
    previous_close, prev_source = _first_valid([
        ("fast_info.previous_close",        _safe(fast, "previous_close")),
        ("info.regularMarketPreviousClose", info.get("regularMarketPreviousClose")),
        ("history.prev_close",              hist_prev),
    ], sources, "prev_candidates")

    # --- Day's change (absolute + percent), only if we have a prior close
    change_abs = None
    change_pct = None
    if previous_close:  # guards against None and 0 (can't divide by 0)
        change_abs = price - previous_close
        change_pct = (change_abs / previous_close) * 100

    # --- Company name: longName → shortName → the symbol itself ---------
    name = info.get("longName") or info.get("shortName") or display_symbol

    # Currency / exchange: prefer fast_info, fall back to info.
    currency = _safe(fast, "currency") or info.get("currency") or ""
    exchange = _safe(fast, "exchange") or info.get("exchange") or ""

    return StockQuote(
        found=True,
        symbol=display_symbol,
        name=name,
        price=price,
        currency=currency,
        previous_close=previous_close,
        change_abs=change_abs,
        change_pct=change_pct,
        exchange=exchange,
        price_source=price_source,
        prev_source=prev_source,
        sources=sources,
    )


# --- Search / resolve a query to candidate tickers -----------------------

@dataclass
class TickerMatch:
    """One candidate the search turned up: a symbol plus a human label."""
    symbol: str
    name: str
    exchange: str = ""
    currency: str = ""


def _quick_resolve(symbol):
    """
    Confirm a symbol is real (has a live price) and grab a display name.
    Returns a TickerMatch, or None if it doesn't resolve. Uses fast_info for the
    price (cheap) and falls back to .info only for the name.
    """
    try:
        stock = yf.Ticker(symbol)
        if not _is_number(_safe(stock.fast_info, "last_price")):
            return None
        try:
            info = stock.info or {}
        except Exception:
            info = {}
        name = info.get("longName") or info.get("shortName") or symbol.upper()
        exchange = _safe(stock.fast_info, "exchange") or info.get("exchange") or ""
        currency = _safe(stock.fast_info, "currency") or info.get("currency") or ""
        return TickerMatch(symbol.upper(), name, exchange, currency)
    except Exception:
        return None


# --- Hebrew-input search support -----------------------------------------
#
# Yahoo's search does NOT understand Hebrew, so "אפל" or "טבע" return nothing.
# This curated map lets common Hebrew names (and a few spelling variants) resolve
# to the right ticker. Several Hebrew variants may point to the same target.
# English/ticker search is unchanged — this only ADDS a lookup (see find_tickers).
#
# Each value is (preferred_ticker, english_name). english_name is the fallback we
# hand to Yahoo's search if the preferred ticker ever stops resolving.
HEBREW_ALIASES = {
    # --- US giants (Yahoo has these; we just bridge the Hebrew spelling) ---
    "אפל": ("AAPL", "Apple"),
    "מיקרוסופט": ("MSFT", "Microsoft"),
    "נבידיה": ("NVDA", "Nvidia"),
    "אנבידיה": ("NVDA", "Nvidia"),
    "גוגל": ("GOOGL", "Alphabet"),
    "אלפבית": ("GOOGL", "Alphabet"),
    "אמזון": ("AMZN", "Amazon"),
    "טסלה": ("TSLA", "Tesla"),
    "מטא": ("META", "Meta"),
    "פייסבוק": ("META", "Meta"),
    # --- Israeli (TASE) — best-guess tickers, verified by the test suite ---
    "טבע": ("TEVA.TA", "Teva"),
    "פועלים": ("POLI.TA", "Bank Hapoalim"),
    "בנק הפועלים": ("POLI.TA", "Bank Hapoalim"),
    "לאומי": ("LUMI.TA", "Bank Leumi"),
    "בנק לאומי": ("LUMI.TA", "Bank Leumi"),
    "מזרחי": ("MZTF.TA", "Mizrahi Tefahot"),
    "מזרחי טפחות": ("MZTF.TA", "Mizrahi Tefahot"),
    "מגדל": ("MGDL.TA", "Migdal Insurance"),
    "הראל": ("HARL.TA", "Harel Insurance"),
    "כלל": ("CLIS.TA", "Clal Insurance"),
    "כלל ביטוח": ("CLIS.TA", "Clal Insurance"),
    "מנורה": ("MMHD.TA", "Menora Mivtachim"),
    "מנורה מבטחים": ("MMHD.TA", "Menora Mivtachim"),
    "אלביט": ("ESLT.TA", "Elbit Systems"),
    "אלביט מערכות": ("ESLT.TA", "Elbit Systems"),
    "טאואר": ("TSEM.TA", "Tower Semiconductor"),
    "שטראוס": ("STRS.TA", "Strauss Group"),
    "אל על": ("ELAL.TA", "El Al"),
    "דלק": ("DLEKG.TA", "Delek Group"),
    "קבוצת דלק": ("DLEKG.TA", "Delek Group"),
    "נופר": ("NOFR.TA", "Nofar Energy"),
    "נופר אנרג'י": ("NOFR.TA", "Nofar Energy"),
    # --- More TASE names (round 2) — best-guess tickers, VERIFIED by the test
    #     suite (expect_hebrew_aliases_resolve). Do not trust blindly. ---
    "כיל": ("ICL.TA", "ICL Group"),
    "כימיקלים לישראל": ("ICL.TA", "ICL Group"),
    "נייס": ("NICE.TA", "Nice"),
    "נובה": ("NVMI.TA", "Nova"),
    "קמטק": ("CAMT.TA", "Camtek"),
    "בזק": ("BEZQ.TA", "Bezeq"),
    "פרטנר": ("PTNR.TA", "Partner Communications"),
    "סלקום": ("CEL.TA", "Cellcom"),
    "שופרסל": ("SAE.TA", "Shufersal"),
    "רמי לוי": ("RMLI.TA", "Rami Levy"),
    "פז": ("PAZ.TA", "Paz Retail and Energy"),
    "פז נפט": ("PAZ.TA", "Paz Retail and Energy"),
    "דיסקונט": ("DSCT.TA", "Israel Discount Bank"),
    "בנק דיסקונט": ("DSCT.TA", "Israel Discount Bank"),
    "הבינלאומי": ("FIBI.TA", "First International Bank"),
    "בנק הבינלאומי": ("FIBI.TA", "First International Bank"),
    "מליסרון": ("MLSR.TA", "Melisron"),
    "עזריאלי": ("AZRG.TA", "Azrieli Group"),
    "קבוצת עזריאלי": ("AZRG.TA", "Azrieli Group"),
    "אמות": ("AMOT.TA", "Amot Investments"),
    "אמות השקעות": ("AMOT.TA", "Amot Investments"),
    "גזית": ("GCT.TA", "G City"),
    "גזית גלוב": ("GCT.TA", "G City"),
    "ג'י סיטי": ("GCT.TA", "G City"),
    "שיכון ובינוי": ("SKBN.TA", "Shikun & Binui"),
    "אשטרום": ("ASHG.TA", "Ashtrom Group"),
    "קבוצת אשטרום": ("ASHG.TA", "Ashtrom Group"),
    "אנרג'יקס": ("ENRG.TA", "Energix"),
    "נאוויטס": ("NVPT.TA", "Navitas Petroleum"),
    "נאוויטס פטרוליום": ("NVPT.TA", "Navitas Petroleum"),
    "ישראמקו": ("ISRA.TA", "Isramco"),
    "רציו": ("RATI.TA", "Ratio"),
    "אורמת": ("ORA.TA", "Ormat Technologies"),
    "אורמת טכנולוגיות": ("ORA.TA", "Ormat Technologies"),
    "מבטח שמיר": ("MISH.TA", "Mivtach Shamir"),
    "פיניקס": ("PHOE.TA", "Phoenix Holdings"),
    "הפניקס": ("PHOE.TA", "Phoenix Holdings"),
    "ביג": ("BIG.TA", "Big Shopping Centers"),
    "ביג מרכזי קניות": ("BIG.TA", "Big Shopping Centers"),
    "אלוני חץ": ("ALHE.TA", "Alony Hetz"),
    "טפרון": ("TDRN.TA", "Tadiran"),
    "פתאל": ("FTAL.TA", "Fattal Holdings"),
    # NOTE: a few candidates were dropped because their ticker is dead on Yahoo
    # and no live replacement applies (re-add with a verified .TA if that changes):
    #   - "ארית" (Arit Industries): no Yahoo listing under any ticker/name.
    #   - "מזור רובוטיקה" (Mazor Robotics): delisted after the 2018 Medtronic
    #     acquisition — no standalone ticker exists.
    # "פז" was corrected from the dead PZOL.TA to the live PAZ.TA (Paz Retail
    # and Energy); "גזית" now points at GCT.TA (Gazit Globe renamed to G City).
}

# Geresh / apostrophe variants we drop so "נופר אנרג'י" == "נופר אנרגי".
_GERESH_CHARS = ("'", "׳", "’", "`")  # ' (ASCII), ׳ (geresh), ’, `


def normalize_hebrew(q: str) -> str:
    """Normalize a query for Hebrew-alias matching:

    - trims and lower-cases (a no-op for Hebrew letters, but tidies Latin text);
    - drops geresh/apostrophe variants (', ׳, ’);
    - collapses internal whitespace;
    - strips a leading "בנק "/"קבוצת " so "בנק הפועלים" also matches "הפועלים".
    """
    s = (q or "").strip().lower()
    for ch in _GERESH_CHARS:
        s = s.replace(ch, "")
    s = " ".join(s.split())
    for prefix in ("בנק ", "קבוצת "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
            break
    return s


# Pre-normalized view of the alias keys, built once, for O(1) lookup.
_HEBREW_ALIASES_NORM = {normalize_hebrew(k): v for k, v in HEBREW_ALIASES.items()}


def hebrew_alias(query: str):
    """Return (preferred_ticker, english_name) if `query` matches a curated Hebrew
    alias (after normalization), else None. Pure dict logic — no network."""
    return _HEBREW_ALIASES_NORM.get(normalize_hebrew(query))


def find_tickers(query: str, max_results: int = 6):
    """
    Turn a free-text query into a list of candidate tickers, so the UI can let
    the user pick the RIGHT one (e.g. the Tel-Aviv `AVIV.TA`, not a US ETF).

    We combine two sources:
      1. Yahoo's own search (great for company names and symbols).
      2. An explicit probe of the `.TA` (Tel-Aviv) variant, because Yahoo's
         search often misses TASE stocks. This also lets a user paste a numeric
         TASE security number as `<number>.TA` if Yahoo happens to list it.

    NOTE: Yahoo does NOT map a TASE *security number* (e.g. 444018) to its
    ticker, so a bare number usually returns nothing — the UI then advises using
    the `.TA` ticker. Returns a list of TickerMatch (possibly empty).
    """
    q = (query or "").strip()
    if not q:
        return []
    q_upper = q.upper()
    is_numeric = q.isdigit()

    matches = []
    seen = set()

    def add(symbol, name, exchange):
        key = (symbol or "").upper()
        if key and key not in seen:
            seen.add(key)
            matches.append(TickerMatch(key, name or key, exchange or "", ""))

    # 0) Hebrew alias lookup FIRST — Yahoo's search can't read Hebrew, so without
    #    this, "אפל"/"טבע" return nothing. A matched alias is added before any
    #    other source so it becomes the default candidate.
    alias = hebrew_alias(q)
    if alias:
        preferred_ticker, english_name = alias
        resolved = _quick_resolve(preferred_ticker)
        if resolved:
            add(resolved.symbol, resolved.name, resolved.exchange)
        else:
            # Preferred ticker didn't resolve (e.g. a TASE symbol changed) — fall
            # back to a Yahoo search on the English name so we still find it.
            try:
                for r in yf.Search(english_name, max_results=max_results).quotes:
                    add(r.get("symbol"),
                        r.get("shortname") or r.get("longname"),
                        r.get("exchange"))
            except Exception:
                pass

    # 1) Yahoo text search — skipped for a bare number (its number search is
    #    unreliable and returns unrelated funds).
    if not is_numeric:
        try:
            for r in yf.Search(q, max_results=max_results).quotes:
                add(r.get("symbol"),
                    r.get("shortname") or r.get("longname"),
                    r.get("exchange"))
        except Exception:
            pass

    # 2) Always probe the Tel-Aviv `.TA` variant (search misses many TASE names).
    ta_symbol = q_upper if q_upper.endswith(".TA") else q_upper + ".TA"
    if ta_symbol not in seen:
        resolved = _quick_resolve(ta_symbol)
        if resolved:
            add(resolved.symbol, resolved.name, resolved.exchange)

    # Put an exact match for what the user typed first, so it's the default.
    matches.sort(key=lambda m: (m.symbol != q_upper, m.symbol != ta_symbol))
    return matches[:max_results]


# --- Watchlist (pure list helpers; storage lives in the UI layer) --------

def normalize_symbol(symbol: str) -> str:
    """Tidy a ticker for storage/compare: trimmed + upper-case."""
    return (symbol or "").strip().upper()


def add_to_watchlist(watchlist, symbol):
    """Return a NEW list with `symbol` added (upper-cased, de-duplicated,
    order preserved). A blank symbol is ignored."""
    items = [normalize_symbol(s) for s in (watchlist or []) if normalize_symbol(s)]
    sym = normalize_symbol(symbol)
    if sym and sym not in items:
        items.append(sym)
    return items


def remove_from_watchlist(watchlist, symbol):
    """Return a NEW list with `symbol` removed."""
    sym = normalize_symbol(symbol)
    return [normalize_symbol(s) for s in (watchlist or [])
            if normalize_symbol(s) and normalize_symbol(s) != sym]


def in_watchlist(watchlist, symbol) -> bool:
    """True if `symbol` is already followed."""
    sym = normalize_symbol(symbol)
    return sym in [normalize_symbol(s) for s in (watchlist or [])]


# --- Price history (for the chart) ---------------------------------------

@dataclass
class PriceHistory:
    """
    A clean, structured result for a price-history (chart) lookup.

    `found` is the "did we get usable data?" signal:
      - found == True  -> `data` is a DataFrame with columns
                          Date, Open, High, Low, Close, Volume.
      - found == False -> no data for this range (show a gentle note);
                          `reason` explains why.
    """
    found: bool
    symbol: str
    range_key: str
    period: str = ""
    interval: str = ""
    data: Optional[pd.DataFrame] = None
    reason: str = ""


# Each user-facing range maps to a (yfinance period, interval) pair.
# Short ranges use intraday intervals; long ranges use daily/weekly so the
# chart stays readable and the download stays small (free tier).
RANGES = {
    "1D": ("1d",  "5m"),    # one day, every 5 minutes
    "1W": ("5d",  "30m"),   # ~one week, every 30 minutes
    "1M": ("1mo", "1d"),    # one month, daily
    "6M": ("6mo", "1d"),    # six months, daily
    "1Y": ("1y",  "1d"),    # one year, daily
    "5Y": ("5y",  "1wk"),   # five years, weekly
}


def get_price_history(ticker: str, range_key: str) -> PriceHistory:
    """
    Fetch price history for `ticker` over the time window named by `range_key`
    (one of the keys in RANGES, e.g. "1M").

    Always returns a PriceHistory. If the range is empty or anything goes wrong
    (intraday data is often missing on the free tier), returns found=False with
    a human-readable `reason` instead of raising.
    """
    symbol = (ticker or "").strip()
    display_symbol = symbol.upper()

    # Guard against a blank ticker or an unknown range key.
    if not symbol:
        return PriceHistory(False, display_symbol, range_key,
                            reason="No ticker given.")
    if range_key not in RANGES:
        return PriceHistory(False, display_symbol, range_key,
                            reason=f"Unknown range '{range_key}'.")

    period, interval = RANGES[range_key]

    # Download the candles. Any network/library error becomes a clean "no data".
    try:
        raw = yf.Ticker(symbol).history(period=period, interval=interval)
    except Exception as error:
        return PriceHistory(False, display_symbol, range_key, period, interval,
                            reason=f"Error fetching data: {error}")

    # Empty frame = nothing for this range (common for 1D intraday on free tier).
    if raw is None or raw.empty:
        return PriceHistory(
            False, display_symbol, range_key, period, interval,
            reason="No data for this range (the free data source sometimes has "
                   "gaps, especially for intraday ranges like 1D/1W).")

    # Tidy up the table for charting:
    #   - move the date/time index into a normal column,
    #   - the column is "Date" for daily data and "Datetime" for intraday,
    #     so rename either one to "Date",
    #   - keep just the columns we care about and drop rows with no close price.
    df = raw.reset_index()
    date_col = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={date_col: "Date"})

    wanted = ["Date", "Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in wanted if c in df.columns]].dropna(subset=["Close"])

    if df.empty:
        return PriceHistory(False, display_symbol, range_key, period, interval,
                            reason="No valid price rows for this range.")

    return PriceHistory(True, display_symbol, range_key, period, interval, data=df)


def first_close(df) -> Optional[float]:
    """
    The first (range-start) closing price in a price-history table.

    This is the 0% baseline for the "% change from start of range" views:
    every later price is expressed relative to it, so the start bar itself is
    exactly 0%. Returns None when there's no usable Close column (so callers
    can skip the % view rather than divide by nothing).
    """
    if df is None or "Close" not in getattr(df, "columns", []):
        return None
    closes = df["Close"].dropna()
    if closes.empty:
        return None
    return float(closes.iloc[0])


def price_to_pct_change(price, base):
    """
    Map an absolute price to its cumulative % change from `base`.

    `base` is the range-start close (see first_close), so the start bar maps to
    exactly 0%, a price 10% above it maps to +10, and so on. Works for a single
    number or a whole pandas Series/array (vectorised), which is why both the
    normalised % line and the candlestick's linked right-hand % axis use it:

        pct = (price / base - 1) * 100
    """
    return (price / base - 1) * 100


# --- Company & stock metrics (Step 3) ------------------------------------

@dataclass
class Metric:
    """
    One labelled metric.

    - `value`     : the raw value (number / text / date) or None if missing.
    - `available` : True when we actually got a usable value (else show "n/a").
    - `fmt`       : a hint telling the UI how to display it (see app.py).
    - `source`    : where it came from, for the Debug panel.
    """
    label: str
    value: object
    available: bool
    fmt: str = "text"
    source: str = ""


@dataclass
class MetricGroup:
    """
    A named group of metrics (used for both Company analysis and Stock
    analysis). `metrics` is an ordered dict of key -> Metric.

    `found` is the "is this a real ticker?" signal: False means show a gentle
    note instead of a table. `source_note` records which data path supplied the
    values (handy for the Debug panel — e.g. diagnosing empty .info fetches).
    """
    found: bool
    symbol: str
    currency: str = ""
    metrics: dict = field(default_factory=dict)
    source_note: str = ""


def _metric(label, value, fmt, source):
    """
    Build a Metric, deciding `available` from the raw value:
    None or NaN or an empty string -> not available (UI shows "n/a").
    """
    if isinstance(value, str):
        available = bool(value.strip())
    elif value is None:
        available = False
    else:
        # Numbers: reject NaN. Non-numeric (e.g. a date) is fine as long as
        # it isn't None.
        try:
            available = not math.isnan(float(value))
        except (TypeError, ValueError):
            available = True
    return Metric(label=label, value=value if available else None,
                  available=available, fmt=fmt, source=source)


def _has_identity(info) -> bool:
    """A real ticker has at least one of these headline fields filled in."""
    return any(info.get(k) for k in
               ("longName", "shortName", "marketCap",
                "regularMarketPrice", "currentPrice"))


def _fetch_info_resilient(symbol, attempts: int = 3, pause: float = 0.5):
    """
    Fetch yfinance's `info` robustly.

    Problem: `.info` is sometimes empty or partial on a transient rate-limit,
    even for a perfectly real ticker (the symptom behind "No company metrics
    available" for MSFT). Fix: try a few times — with a fresh Ticker each time
    (so we don't get a cached-empty), trying both `.info` and `.get_info()` —
    and keep the richest response we see.

    Returns (info_dict, source_note). `source_note` records which path won, for
    the Debug panel.
    """
    best, note = {}, "no info returned"
    for attempt in range(1, attempts + 1):
        stock = yf.Ticker(symbol)  # fresh each attempt to dodge cached empties
        for getter, label in (
            (lambda s: s.info, "ticker.info"),
            (lambda s: s.get_info(), "ticker.get_info()"),
        ):
            try:
                data = getter(stock) or {}
            except Exception:
                data = {}
            # A response that identifies the company is good enough — stop here.
            if _has_identity(data):
                return data, f"{label} (attempt {attempt})"
            if len(data) > len(best):
                best, note = data, f"{label} partial (attempt {attempt})"
        if attempt < attempts:
            time.sleep(pause)  # brief backoff before retrying
    return best, note


def get_company_metrics(ticker: str) -> MetricGroup:
    """
    Fundamentals about the *business* behind the stock (market cap, P/E, EPS,
    revenue, margins, dividend, debt, free cash flow, next earnings, sector,
    industry). Each field may be missing -> we mark it not-available rather
    than crashing.
    """
    symbol = (ticker or "").strip()
    display = symbol.upper()
    if not symbol:
        return MetricGroup(False, display)

    # Resilient fetch: retries + .get_info() fallback (Part A fix).
    info, source_note = _fetch_info_resilient(symbol)

    stock = yf.Ticker(symbol)  # for calendar + fast_info fallbacks

    # Decide if this is a real ticker. Prefer info identity, but if info came
    # back empty/partial (transient), confirm via fast_info's price — we do NOT
    # want to falsely report "not found" just because .info was rate-limited.
    real = _has_identity(info)
    if not real:
        fast_price = _safe(stock.fast_info, "last_price")
        if _is_number(fast_price):
            real = True
            source_note += " + fast_info price (info was empty)"
    if not real:
        # Genuinely nothing — treat as not found.
        return MetricGroup(False, display, source_note=source_note)

    # Currency / market cap can fall back to fast_info when info is sparse.
    currency = info.get("currency") or _safe(stock.fast_info, "currency") or ""
    market_cap = info.get("marketCap")
    if not _is_number(market_cap):
        market_cap = _safe(stock.fast_info, "market_cap")

    # Next earnings date reads cleanest from the calendar (a real date object)
    # rather than the raw unix timestamp in info.
    next_earnings = None
    try:
        calendar = stock.calendar or {}
        dates = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
        if dates:
            next_earnings = dates[0]
    except Exception:
        next_earnings = None

    # PEG = P/E relative to growth (a value below ~1 is cheap FOR its growth).
    peg = info.get("trailingPegRatio")
    if not _is_number(peg):
        peg = info.get("pegRatio")

    metrics = {
        "market_cap":     _metric("Market cap", market_cap, "large_money", "info/fast_info.marketCap"),
        "pe":             _metric("P/E (trailing)", info.get("trailingPE"), "ratio", "info.trailingPE"),
        "forward_pe":     _metric("Forward P/E", info.get("forwardPE"), "ratio", "info.forwardPE"),
        "peg":            _metric("PEG ratio", peg, "ratio", "info.trailingPegRatio"),
        "eps":            _metric("EPS (trailing)", info.get("trailingEps"), "money", "info.trailingEps"),
        "revenue":        _metric("Revenue (ttm)", info.get("totalRevenue"), "large_money", "info.totalRevenue"),
        # Year-over-year growth — fractions (0.48 = 48%) -> percent_frac.
        "earnings_growth": _metric("Earnings growth (yoy)", info.get("earningsGrowth"), "percent_frac", "info.earningsGrowth"),
        "revenue_growth":  _metric("Revenue growth (yoy)", info.get("revenueGrowth"), "percent_frac", "info.revenueGrowth"),
        # profitMargins is a FRACTION (0.27 = 27%) -> percent_frac multiplies by 100.
        "profit_margin":  _metric("Profit margin", info.get("profitMargins"), "percent_frac", "info.profitMargins"),
        # dividendYield is ALREADY a percent (0.36 = 0.36%) -> percent shows as-is.
        "dividend_yield": _metric("Dividend yield", info.get("dividendYield"), "percent", "info.dividendYield"),
        "debt_to_equity": _metric("Debt-to-equity", info.get("debtToEquity"), "ratio", "info.debtToEquity"),
        "free_cash_flow": _metric("Free cash flow", info.get("freeCashflow"), "large_money", "info.freeCashflow"),
        "next_earnings":  _metric("Next earnings", next_earnings, "date", "calendar.EarningsDate"),
        "sector":         _metric("Sector", info.get("sector"), "text", "info.sector"),
        "industry":       _metric("Industry", info.get("industry"), "text", "info.industry"),
    }
    return MetricGroup(True, display, currency, metrics, source_note=source_note)


def _rsi(closes, period: int = 14):
    """
    Relative Strength Index (RSI) — a classic momentum gauge (0..100).

    Intuition: compare the average size of UP days vs DOWN days over the last
    `period` days. High (70+) = lots of recent gains ("overbought"); low (30-)
    = lots of recent losses ("oversold").

    Math, step by step:
      1. delta    = today's close minus yesterday's close (day-to-day change)
      2. gains    = the positive deltas (down days become 0)
         losses   = the SIZE of the negative deltas (up days become 0)
      3. avg_gain = average gain over `period` days
         avg_loss = average loss over `period` days
      4. RS  = avg_gain / avg_loss
         RSI = 100 - 100 / (1 + RS)
    Returns the most recent RSI, or None if there isn't enough data.
    """
    if closes is None or len(closes) < period + 1:
        return None
    delta = closes.diff()                 # step 1
    gains = delta.clip(lower=0)           # step 2: keep positives, else 0
    losses = -delta.clip(upper=0)         # step 2: negatives as positive sizes
    avg_gain = gains.rolling(period).mean()   # step 3
    avg_loss = losses.rolling(period).mean()
    rs = avg_gain / avg_loss              # step 4 (avg_loss 0 -> inf -> RSI 100)
    rsi = 100 - (100 / (1 + rs))
    last = rsi.iloc[-1]
    return float(last) if _is_number(last) else None


def _ema(series, span):
    """
    Exponential Moving Average: like a moving average, but recent prices count
    for more (older prices fade away smoothly). `span` is the EMA "length".
    """
    return series.ewm(span=span, adjust=False).mean()


def _macd(closes, fast: int = 12, slow: int = 26, signal: int = 9):
    """
    MACD (Moving Average Convergence Divergence) — a trend/momentum indicator.

    Math:
      1. MACD line   = (fast EMA, 12) minus (slow EMA, 26)
                       -> positive when short-term trend is above long-term.
      2. Signal line = 9-period EMA of the MACD line (a smoothed version).
      3. Histogram   = MACD line minus Signal line
                       -> positive & rising = strengthening up-move.

    Returns the latest (macd_line, signal_line, histogram) as floats, or
    (None, None, None) if there isn't enough history.
    """
    if closes is None or len(closes) < slow + signal:
        return None, None, None
    macd_line = _ema(closes, fast) - _ema(closes, slow)
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    m, s, h = macd_line.iloc[-1], signal_line.iloc[-1], histogram.iloc[-1]
    if not (_is_number(m) and _is_number(s) and _is_number(h)):
        return None, None, None
    return float(m), float(s), float(h)


def _bollinger(closes, period: int = 20, num_std: float = 2.0):
    """
    Bollinger Bands — a volatility envelope around a moving average.

    Math (using the last `period` closes):
      middle = simple moving average (the 20-day SMA)
      band   = standard deviation of those closes
      upper  = middle + 2 * band
      lower  = middle - 2 * band
    Price near the upper band = stretched high; near the lower band = stretched
    low. We use population std (ddof=0), the textbook Bollinger convention.

    Returns latest (upper, middle, lower) as floats, or (None, None, None).
    """
    if closes is None or len(closes) < period:
        return None, None, None
    window = closes.tail(period)
    middle = window.mean()
    band = window.std(ddof=0)
    if not (_is_number(middle) and _is_number(band)):
        return None, None, None
    return float(middle + num_std * band), float(middle), float(middle - num_std * band)


# --- Volume-based indicators (estimates of buy/sell pressure) -------------
# NOTE: OBV and A/D are ESTIMATES of buying/selling pressure inferred from
# price + volume. They are NOT real order-flow data (which isn't free).

def _obv(closes, volumes):
    """
    On-Balance Volume: a running total that ADDS the day's volume on up-close
    days and SUBTRACTS it on down-close days. Rising OBV = volume favouring
    up-days (buying pressure); falling = the opposite. Returns the OBV Series,
    or None if data is too short.
    """
    if closes is None or volumes is None or len(closes) < 2:
        return None
    direction = closes.diff()          # today's close minus yesterday's
    step = volumes.astype("float64").copy()
    step[direction < 0] *= -1          # down day -> subtract that volume
    step[direction == 0] = 0           # unchanged -> contributes nothing
    step.iloc[0] = 0                   # no "previous day" for the first row
    return step.cumsum()


def _accum_dist(df):
    """
    Accumulation/Distribution line. For each bar:
      money-flow multiplier = ((Close-Low) - (High-Close)) / (High-Low)
            (+1 = closed at the high, -1 = closed at the low)
      money-flow volume     = multiplier * Volume
      A/D                   = running sum of money-flow volume
    Rising A/D = accumulation (buying pressure); falling = distribution.
    Returns the A/D Series, or None if data is too short.
    """
    if df is None or len(df) < 2:
        return None
    if not all(c in df.columns for c in ("High", "Low", "Close", "Volume")):
        return None
    high, low, close, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    span = (high - low).replace(0, pd.NA)            # avoid divide-by-zero
    multiplier = ((close - low) - (high - close)) / span
    multiplier = multiplier.fillna(0.0)              # flat bars -> 0
    return (multiplier * vol).cumsum()


def _trend_state(series, lookback: int = 20):
    """
    Classify a series as 'rising' / 'falling' / 'flat' by comparing its latest
    value to where it was `lookback` bars ago. Returns None if too short.
    """
    if series is None or len(series) < lookback + 1:
        return None
    now = series.iloc[-1]
    past = series.iloc[-1 - lookback]
    if not (_is_number(now) and _is_number(past)):
        return None
    if now > past:
        return "rising"
    if now < past:
        return "falling"
    return "flat"


def _volume_confirmation(closes, volumes, recent: int = 5, baseline: int = 50,
                         move_threshold: float = 3.0):
    """
    Is a recent notable PRICE move backed by VOLUME?

      recent_vol = average volume over the last `recent` days
      avg_vol    = average volume over the last `baseline` days
      move_pct   = % price change over the last `recent` days
      ratio      = recent_vol / avg_vol

    A notable move (|move| >= threshold %) on clearly above-average volume is
    "confirmed"; on clearly below-average volume it's "unconfirmed"; otherwise
    "neutral". Returns a dict of those values, or None if data is too short.
    """
    if (closes is None or volumes is None
            or len(closes) <= baseline or len(volumes) <= baseline):
        return None
    recent_vol = float(volumes.tail(recent).mean())
    avg_vol = float(volumes.tail(baseline).mean())
    if not (_is_number(recent_vol) and _is_number(avg_vol)) or avg_vol == 0:
        return None
    ratio = recent_vol / avg_vol
    start = closes.iloc[-1 - recent]
    move_pct = ((closes.iloc[-1] / start - 1) * 100
                if _is_number(start) and start != 0 else 0.0)
    notable = abs(move_pct) >= move_threshold
    if notable and ratio >= 1.2:
        state = "confirmed"
    elif notable and ratio < 0.8:
        state = "unconfirmed"
    else:
        state = "neutral"
    return {"recent_vol": recent_vol, "avg_vol": avg_vol, "ratio": ratio,
            "move_pct": move_pct, "state": state}


def get_stock_technicals(ticker: str) -> MetricGroup:
    """
    Indicators about the *share price behaviour*: 52-week high/low, 50-day and
    200-day moving averages, RSI, beta, average volume.

    Moving averages and RSI are COMPUTED from daily closing prices (a year of
    history); the rest come from yfinance's info, with sensible fallbacks.
    """
    symbol = (ticker or "").strip()
    display = symbol.upper()
    if not symbol:
        return MetricGroup(False, display)

    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        info = {}

    # A year of daily closes lets us compute the 50/200-day MAs and RSI.
    history = get_price_history(symbol, "1Y")
    price_df = history.data if history.found else None
    closes = price_df["Close"] if price_df is not None else None
    volumes = (price_df["Volume"]
               if price_df is not None and "Volume" in price_df.columns else None)

    # Real ticker if it has identity fields OR we got price history.
    if not _has_identity(info) and not history.found:
        return MetricGroup(False, display)

    currency = info.get("currency") or ""

    # Moving averages = the average of the last N daily closes.
    ma50 = float(closes.tail(50).mean()) if closes is not None and len(closes) >= 50 else None
    ma200 = float(closes.tail(200).mean()) if closes is not None and len(closes) >= 200 else None
    rsi = _rsi(closes)

    # 52-week high/low: prefer yfinance's numbers, else derive from the year.
    high52 = info.get("fiftyTwoWeekHigh")
    low52 = info.get("fiftyTwoWeekLow")
    if not _is_number(high52) and closes is not None:
        high52 = float(closes.max())
    if not _is_number(low52) and closes is not None:
        low52 = float(closes.min())

    # MACD (trend/momentum) computed from the daily closes.
    macd_line, macd_signal, macd_hist = _macd(closes)
    macd_state = None
    if _is_number(macd_line) and _is_number(macd_signal):
        macd_state = "bullish" if macd_line > macd_signal else "bearish"

    # Bollinger Bands, plus where the latest close sits relative to them.
    bb_upper, bb_middle, bb_lower = _bollinger(closes)
    last_close = float(closes.iloc[-1]) if closes is not None and len(closes) else None
    bb_state = None
    if _is_number(bb_upper) and _is_number(bb_lower) and _is_number(last_close):
        if last_close > bb_upper:
            bb_state = "above upper / overbought"
        elif last_close < bb_lower:
            bb_state = "below lower / oversold"
        else:
            bb_state = "within bands"

    # Volume-based pressure estimates (OBV, A/D) and volume confirmation.
    obv_series = _obv(closes, volumes)
    obv_trend = _trend_state(obv_series)
    obv_last = (float(obv_series.iloc[-1])
                if obv_series is not None and len(obv_series) else None)

    ad_series = _accum_dist(price_df)
    ad_trend = _trend_state(ad_series)
    ad_last = (float(ad_series.iloc[-1])
               if ad_series is not None and len(ad_series) else None)

    volconf = _volume_confirmation(closes, volumes)

    metrics = {
        "week52_high": _metric("52-week high", high52, "money", "info/history"),
        "week52_low":  _metric("52-week low", low52, "money", "info/history"),
        "ma50":        _metric("50-day MA", ma50, "money", "computed:closes"),
        "ma200":       _metric("200-day MA", ma200, "money", "computed:closes"),
        "rsi":         _metric("RSI (14)", rsi, "ratio", "computed:closes"),
        "beta":        _metric("Beta", info.get("beta"), "ratio", "info.beta"),
        "avg_volume":  _metric("Avg volume", info.get("averageVolume"), "int_large", "info.averageVolume"),
        # New in this run: MACD and Bollinger Bands.
        "macd":        _metric("MACD line", macd_line, "ratio", "computed:closes"),
        "macd_signal": _metric("MACD signal", macd_signal, "ratio", "computed:closes"),
        "macd_hist":   _metric("MACD histogram", macd_hist, "ratio", "computed:closes"),
        "macd_state":  _metric("MACD trend", macd_state, "text", "computed:closes"),
        "bb_upper":    _metric("Bollinger upper", bb_upper, "money", "computed:closes"),
        "bb_middle":   _metric("Bollinger mid (20d SMA)", bb_middle, "money", "computed:closes"),
        "bb_lower":    _metric("Bollinger lower", bb_lower, "money", "computed:closes"),
        "bb_state":    _metric("Bollinger position", bb_state, "text", "computed:closes"),
        # New in this run: volume-based pressure estimates.
        "vol_recent":  _metric("Recent volume (5d avg)",
                               volconf["recent_vol"] if volconf else None,
                               "int_large", "computed:volume"),
        "vol_avg":     _metric("Avg volume (50d)",
                               volconf["avg_vol"] if volconf else None,
                               "int_large", "computed:volume"),
        "vol_move":    _metric("5-day price move",
                               volconf["move_pct"] if volconf else None,
                               "percent", "computed:price"),
        "vol_confirm": _metric("Volume confirmation",
                               volconf["state"] if volconf else None,
                               "text", "computed:price+volume"),
        "obv_value":   _metric("OBV (est. pressure)", obv_last, "int_large",
                               "computed:price+volume"),
        "obv_trend":   _metric("OBV trend", obv_trend, "text",
                               "computed:price+volume"),
        "ad_value":    _metric("Accum/Dist (est.)", ad_last, "int_large",
                               "computed:price+volume"),
        "ad_trend":    _metric("A/D trend", ad_trend, "text",
                               "computed:price+volume"),
    }
    return MetricGroup(True, display, currency, metrics)


# --- Color-coding metric tiles (rules of thumb, NOT financial advice) ------
#
# classify_metric() returns "good" / "neutral" / "bad" for metrics that carry a
# clear good/bad direction, or None for descriptive / identifier fields and
# missing data (those render uncolored). It uses only metrics we already compute
# — no new data — and the thresholds mirror the verdict's own rules (see
# compute_verdict) so the colors stay consistent with the score.
#
# Valuation metrics (P/E, forward P/E) are SECTOR-AWARE where yfinance gives a
# sector: a tech P/E of 45 shouldn't read "bad" the way a utility's would. With
# no sector we fall back to a generic absolute band, and threshold_note() makes
# the tooltip say so. These are conventional rules of thumb, not advice.

# Typical trailing-P/E bands per sector: (good at/below, bad above); the span in
# between is "neutral". Rough order-of-magnitude rules of thumb, not benchmarks.
_SECTOR_PE_BANDS = {
    "Technology":             (35, 60),
    "Communication Services": (30, 50),
    "Consumer Cyclical":      (28, 50),
    "Healthcare":             (28, 50),
    "Industrials":            (25, 45),
    "Consumer Defensive":     (25, 40),
    "Basic Materials":        (20, 38),
    "Energy":                 (18, 32),
    "Financial Services":     (16, 30),
    "Utilities":              (20, 34),
    "Real Estate":            (28, 50),
}
# Used when the sector is unknown (and flagged as generic in the tooltip).
_GENERIC_PE_BAND = (25, 40)

# Metrics with no meaningful good/bad direction — always uncolored (descriptive
# identifiers, raw levels, and informational fields). The MA *levels* live here;
# the ma50/ma200 tiles are instead colored by PRICE-vs-MA (handled below), not by
# the level itself.
_UNCOLORED_KEYS = frozenset({
    "market_cap", "eps", "revenue", "dividend_yield", "next_earnings",
    "sector", "industry",
    "week52_high", "week52_low", "beta", "avg_volume",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower", "bb_state",
    "vol_recent", "vol_avg", "vol_move", "vol_confirm",
    "obv_value", "obv_trend", "ad_value", "ad_trend",
})


def _pe_class(value, sector):
    """Sector-aware P/E classification (lower is better)."""
    if value <= 0:
        return "bad"  # no positive earnings
    good_below, bad_above = _SECTOR_PE_BANDS.get(sector, _GENERIC_PE_BAND)
    if value <= good_below:
        return "good"
    if value > bad_above:
        return "bad"
    return "neutral"


def classify_metric(key, value, context=None):
    """
    Classify a displayed metric for color-coding:
      "good" (green) | "neutral" (amber) | "bad" (red) | None (don't color).

    None covers descriptive/identifier fields (market cap, sector, EPS, raw
    volumes, MA price levels, Bollinger position, ...), informational fields
    (dividend yield), and any missing value. `context` may carry "sector" (for
    sector-aware valuation) and "price" (for price-vs-MA). Thresholds mirror the
    verdict's rules. These are rules of thumb, NOT financial advice.
    """
    context = context or {}
    sector = context.get("sector")
    price = context.get("price")

    if value is None or key in _UNCOLORED_KEYS:
        return None

    # Text-valued signal: MACD trend (its "value" is a label, not a number).
    if key == "macd_state":
        if value == "bullish":
            return "good"
        if value == "bearish":
            return "bad"
        return None

    # Everything else colored is numeric — require a real number.
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None

    # --- Company fundamentals ---
    if key in ("pe", "forward_pe"):
        return _pe_class(v, sector)
    if key == "peg":
        if v <= 0:
            return None       # a negative PEG isn't a meaningful cheap/dear read
        if v <= 1.5:
            return "good"
        if v > 2.5:
            return "bad"
        return "neutral"
    if key == "profit_margin":            # fraction: <=0 unprofitable, <5% thin
        if v <= 0:
            return "bad"
        if v < 0.05:
            return "neutral"
        return "good"
    if key in ("earnings_growth", "revenue_growth"):  # fraction
        if v <= 0:
            return "bad"
        if v < 0.10:
            return "neutral"
        return "good"
    if key == "debt_to_equity":           # %-style (79 = 0.79x); lower is better
        if v < 0:
            return "bad"      # negative equity
        if v < 50:
            return "good"
        if v <= 150:
            return "neutral"
        return "bad"
    if key == "free_cash_flow":
        return "good" if v > 0 else "bad"

    # --- Stock technicals ---
    if key == "rsi":          # extremes = amber (watch), healthy middle = green
        return "neutral" if (v > 70 or v < 30) else "good"
    if key in ("ma50", "ma200"):          # color the tile by PRICE vs this MA
        if price is None:
            return None
        try:
            p = float(price)
        except (TypeError, ValueError):
            return None
        if math.isnan(p):
            return None
        return "good" if p >= v else "bad"

    return None


def threshold_note(key, context=None):
    """A short tooltip add-on explaining a color threshold's basis (or "").

    Honest about sector-adjusted vs generic thresholds, per the design.
    """
    context = context or {}
    sector = context.get("sector")
    if key in ("pe", "forward_pe"):
        if sector:
            return f"Color threshold is adjusted for the {sector} sector."
        return ("Color threshold is generic (sector unknown), so it is NOT "
                "sector-adjusted.")
    if key == "peg":
        return ("Color uses a generic PEG rule of thumb (<=1.5 good, >2.5 "
                "expensive); PEG already accounts for growth.")
    return ""


# --- Deterministic verdict (Step 4, now across three time horizons) -------

# The four possible verdict labels, worst -> best.
VERDICT_LABELS = ["Sell", "Hold", "Buy", "Strong Buy"]

# The three time horizons we report, short -> long.
HORIZONS = ["6M", "1Y", "5Y"]

# We need at least this many weighted signals to dare give a verdict.
MIN_SIGNALS = 4

# Each signal contributes at most this many BASE points (positive or negative),
# before horizon weighting. Used to scale the score onto 0..100.
_MAX_POINTS_PER_SIGNAL = 2


# --- Horizon weight sets --------------------------------------------------
# The THREE horizons use the SAME signals (evaluated once); they differ ONLY by
# how much each signal counts. Rationale (tune these freely later):
#   * 6M  (short term): price action dominates -> momentum/technicals carry the
#         big weights (RSI, MACD, Bollinger, 50-day MA, 52w position, MA cross);
#         slow-moving fundamentals barely matter over six months.
#   * 1Y  (medium term): a balanced 1.0 weight on everything.
#   * 5Y  (long term): the business matters most -> fundamentals dominate
#         (margin, P/E, free cash flow, debt, 200-day trend); short-term
#         momentum is almost ignored.
# A weight of 0 would mean "ignore this signal for this horizon".
HORIZON_WEIGHTS = {
    "6M": {
        "rsi": 2.0, "macd": 2.0, "bollinger": 2.0, "ma50": 2.0,
        "range52": 1.5, "ma_cross": 1.5, "ma200": 1.0,
        "pe": 0.5, "fwd_pe": 0.5, "margin": 0.5, "d2e": 0.5, "fcf": 0.5,
        "growth": 0.5,  # growth barely moves a 6-month view
        # Volume signals matter MOST short-term: a move not backed by volume
        # is fragile, and accumulation/distribution shows near-term pressure.
        "vol_confirm": 2.0, "obv": 1.5, "ad": 1.5,
    },
    "1Y": {
        "rsi": 1.0, "macd": 1.0, "bollinger": 1.0, "ma50": 1.0,
        "range52": 1.0, "ma_cross": 1.0, "ma200": 1.0,
        "pe": 1.0, "fwd_pe": 1.0, "margin": 1.0, "d2e": 1.0, "fcf": 1.0,
        "growth": 1.5,  # growth is a key medium-term driver
        "vol_confirm": 1.0, "obv": 1.0, "ad": 1.0,
    },
    "5Y": {
        "pe": 2.0, "margin": 2.0, "fcf": 2.0, "d2e": 1.5, "fwd_pe": 1.5,
        "ma200": 1.5, "ma_cross": 1.0, "range52": 0.5,
        "growth": 2.0,  # over five years, growth dominates
        "rsi": 0.3, "macd": 0.3, "bollinger": 0.3, "ma50": 0.3,
        # Over five years, short-term volume noise barely matters.
        "vol_confirm": 0.3, "obv": 0.3, "ad": 0.5,
    },
}

# If a horizon doesn't list a signal, fall back to this neutral weight.
_DEFAULT_WEIGHT = 1.0


@dataclass
class Signal:
    """
    One rule's BASE contribution (before horizon weighting).

    - `key`      : stable id used to look up a horizon weight, e.g. "rsi".
    - `name`     : human label, e.g. "Momentum (RSI)".
    - `measured` : the value it saw, in words.
    - `points`   : base points, capped at +/-2.
    """
    key: str
    name: str
    measured: str
    points: int


@dataclass
class WeightedSignal:
    """A Signal as it counts for ONE horizon: base points x horizon weight."""
    key: str
    name: str
    measured: str
    points: int
    weight: float
    weighted: float   # points * weight (the actual contribution)


@dataclass
class HorizonVerdict:
    """The verdict for a single horizon (6M / 1Y / 5Y)."""
    horizon: str
    label: str = ""
    score: Optional[float] = None
    enough_data: bool = True
    reason: str = ""
    breakdown: list = field(default_factory=list)  # list[WeightedSignal]


@dataclass
class Verdict:
    """
    The full rule-based verdict across all three horizons.

    - found    : False if the ticker doesn't exist at all.
    - horizons : dict "6M"/"1Y"/"5Y" -> HorizonVerdict.
    - signals  : the base Signals, evaluated ONCE and shared by all horizons.
    """
    found: bool
    symbol: str
    horizons: dict = field(default_factory=dict)
    signals: list = field(default_factory=list)
    reason: str = ""


def _label_for_score(score: float) -> str:
    """Map a 0..100 score onto a verdict label, with conservative bands."""
    if score < 35:
        return "Sell"
    if score < 55:
        return "Hold"
    if score < 75:
        return "Buy"
    return "Strong Buy"


def _score_horizon(horizon: str, signals: list) -> HorizonVerdict:
    """
    Apply one horizon's weights to the shared base signals and produce its
    HorizonVerdict (weighted score on the same 0..100 scale + a per-signal
    breakdown showing base points, weight, and weighted contribution).
    """
    weights = HORIZON_WEIGHTS[horizon]
    breakdown = []
    weighted_sum = 0.0
    max_swing = 0.0
    effective = 0  # how many signals actually count (weight > 0)

    for s in signals:
        weight = weights.get(s.key, _DEFAULT_WEIGHT)
        contribution = s.points * weight
        breakdown.append(WeightedSignal(s.key, s.name, s.measured, s.points,
                                        weight, contribution))
        if weight > 0:
            effective += 1
            weighted_sum += contribution
            max_swing += _MAX_POINTS_PER_SIGNAL * weight

    if effective < MIN_SIGNALS or max_swing == 0:
        return HorizonVerdict(
            horizon, enough_data=False, breakdown=breakdown,
            reason=f"Not enough data for the {horizon} horizon "
                   f"({effective} weighted signal(s)).")

    score = 50 + 50 * (weighted_sum / max_swing)
    score = max(0.0, min(100.0, score))  # clamp into [0, 100]
    return HorizonVerdict(horizon, label=_label_for_score(score), score=score,
                          enough_data=True, breakdown=breakdown)


def compute_verdict(ticker: str) -> Verdict:
    """
    Produce transparent, rule-based verdicts for THREE time horizons
    (6M / 1Y / 5Y) from the fundamentals and technicals we already collect.

    The signals are evaluated ONCE; the horizons differ only by how heavily
    each signal is weighted (see HORIZON_WEIGHTS). No LLM, no analyst data, no
    predicting the future — just today's data, weighted three ways. Rules are
    skipped gracefully when inputs are missing.
    """
    symbol = (ticker or "").strip()
    display = symbol.upper()
    if not symbol:
        return Verdict(False, display, reason="No ticker given.")

    # Reuse the building blocks we already have. Any failure -> not found.
    try:
        quote = get_stock_quote(symbol)
        company = get_company_metrics(symbol)
        technicals = get_stock_technicals(symbol)
    except Exception as error:
        return Verdict(False, display, reason=f"Error computing verdict: {error}")

    # If none of the three sources recognised the ticker, it doesn't exist.
    if not quote.found and not company.found and not technicals.found:
        return Verdict(False, display, reason="Ticker not found.")

    # Small helper: pull a metric's value, or None if missing/not available.
    def value_of(group, key):
        metric = group.metrics.get(key) if group.found else None
        return metric.value if (metric and metric.available) else None

    price = quote.price if quote.found else None
    pe = value_of(company, "pe")
    forward_pe = value_of(company, "forward_pe")
    peg = value_of(company, "peg")
    earnings_growth = value_of(company, "earnings_growth")
    revenue_growth = value_of(company, "revenue_growth")
    margin = value_of(company, "profit_margin")
    debt_to_equity = value_of(company, "debt_to_equity")
    free_cash_flow = value_of(company, "free_cash_flow")
    ma50 = value_of(technicals, "ma50")
    ma200 = value_of(technicals, "ma200")
    rsi = value_of(technicals, "rsi")
    high52 = value_of(technicals, "week52_high")
    low52 = value_of(technicals, "week52_low")
    macd_line = value_of(technicals, "macd")
    macd_signal = value_of(technicals, "macd_signal")
    bb_upper = value_of(technicals, "bb_upper")
    bb_lower = value_of(technicals, "bb_lower")
    vol_confirm = value_of(technicals, "vol_confirm")  # confirmed/unconfirmed/neutral
    vol_move = value_of(technicals, "vol_move")        # recent % price move
    obv_trend = value_of(technicals, "obv_trend")      # rising/falling/flat
    ad_trend = value_of(technicals, "ad_trend")        # rising/falling/flat

    signals: list = []

    def add(key, name, measured, points):
        signals.append(Signal(key, name, measured, points))

    # 1) Valuation — GROWTH-ADJUSTED. Prefer PEG (P/E relative to growth): a high
    #    P/E backed by fast growth isn't truly expensive. PEG < ~1 is cheap for
    #    the growth; > ~2.5 is pricey even allowing for growth. Without a PEG we
    #    fall back to plain trailing-P/E bands.
    if _is_number(peg) and peg > 0:
        if peg <= 1.0:
            add("pe", "Valuation (PEG)", f"PEG = {peg:.2f} (cheap for its growth)", +2)
        elif peg <= 1.5:
            add("pe", "Valuation (PEG)", f"PEG = {peg:.2f} (fair for its growth)", +1)
        elif peg <= 2.5:
            add("pe", "Valuation (PEG)", f"PEG = {peg:.2f} (full)", 0)
        else:
            add("pe", "Valuation (PEG)", f"PEG = {peg:.2f} (expensive even for growth)", -1)
    elif _is_number(pe):
        if pe <= 0:
            add("pe", "Valuation (P/E)", f"P/E = {pe:.1f} (no positive earnings)", -1)
        elif pe <= 15:
            add("pe", "Valuation (P/E)", f"P/E = {pe:.1f} (cheap)", +2)
        elif pe <= 25:
            add("pe", "Valuation (P/E)", f"P/E = {pe:.1f} (fair)", +1)
        elif pe <= 40:
            add("pe", "Valuation (P/E)", f"P/E = {pe:.1f} (full)", 0)
        else:
            add("pe", "Valuation (P/E)", f"P/E = {pe:.1f} (expensive)", -1)

    # 1b) Growth — the business expanding? (analysts weigh this heavily; our old
    #     model ignored it entirely). Prefer earnings growth, else revenue growth.
    growth_value = earnings_growth if _is_number(earnings_growth) else revenue_growth
    growth_label = "earnings" if _is_number(earnings_growth) else "revenue"
    if _is_number(growth_value):
        pct = growth_value * 100
        if growth_value <= 0:
            add("growth", "Growth", f"{growth_label} growth {pct:.0f}% (shrinking)", -1)
        elif growth_value < 0.10:
            add("growth", "Growth", f"{growth_label} growth {pct:.0f}% (modest)", 0)
        elif growth_value < 0.25:
            add("growth", "Growth", f"{growth_label} growth {pct:.0f}% (solid)", +1)
        else:
            add("growth", "Growth", f"{growth_label} growth {pct:.0f}% (rapid)", +2)

    # 2) Earnings outlook — forward P/E lower than trailing means earnings are
    #    expected to grow (cheaper on next year's profits).
    if _is_number(pe) and _is_number(forward_pe) and pe > 0 and forward_pe > 0:
        if forward_pe < pe:
            add("fwd_pe", "Earnings outlook (fwd P/E)",
                f"forward {forward_pe:.1f} < trailing {pe:.1f} (improving)", +1)
        elif forward_pe > pe:
            add("fwd_pe", "Earnings outlook (fwd P/E)",
                f"forward {forward_pe:.1f} > trailing {pe:.1f} (softening)", -1)
        else:
            add("fwd_pe", "Earnings outlook (fwd P/E)", f"forward = trailing ({pe:.1f})", 0)

    # 3) Profitability — net profit margin (stored as a fraction).
    if _is_number(margin):
        pct = margin * 100
        if margin <= 0:
            add("margin", "Profitability (margin)", f"margin {pct:.1f}% (unprofitable)", -2)
        elif margin < 0.05:
            add("margin", "Profitability (margin)", f"margin {pct:.1f}% (thin)", 0)
        elif margin < 0.20:
            add("margin", "Profitability (margin)", f"margin {pct:.1f}% (healthy)", +1)
        else:
            add("margin", "Profitability (margin)", f"margin {pct:.1f}% (strong)", +2)

    # 4) Financial health — debt-to-equity (yfinance reports it %-style, e.g. 79).
    if _is_number(debt_to_equity):
        if debt_to_equity < 50:
            add("d2e", "Leverage (debt/equity)", f"D/E = {debt_to_equity:.0f} (low)", +1)
        elif debt_to_equity <= 150:
            add("d2e", "Leverage (debt/equity)", f"D/E = {debt_to_equity:.0f} (moderate)", 0)
        else:
            add("d2e", "Leverage (debt/equity)", f"D/E = {debt_to_equity:.0f} (high)", -1)

    # 5) Financial health — free cash flow positive or negative.
    if _is_number(free_cash_flow):
        if free_cash_flow > 0:
            add("fcf", "Free cash flow", "positive free cash flow", +1)
        else:
            add("fcf", "Free cash flow", "negative free cash flow", -1)

    # 6) Trend — price above/below its 50-day moving average.
    if _is_number(price) and _is_number(ma50):
        if price >= ma50:
            add("ma50", "Trend (vs 50-day MA)", f"price {price:.2f} >= 50d MA {ma50:.2f}", +1)
        else:
            add("ma50", "Trend (vs 50-day MA)", f"price {price:.2f} < 50d MA {ma50:.2f}", -1)

    # 7) Trend — price above/below its 200-day moving average.
    if _is_number(price) and _is_number(ma200):
        if price >= ma200:
            add("ma200", "Trend (vs 200-day MA)", f"price {price:.2f} >= 200d MA {ma200:.2f}", +1)
        else:
            add("ma200", "Trend (vs 200-day MA)", f"price {price:.2f} < 200d MA {ma200:.2f}", -1)

    # 8) Trend — 50-day vs 200-day (golden cross = up, death cross = down).
    if _is_number(ma50) and _is_number(ma200):
        if ma50 >= ma200:
            add("ma_cross", "MA cross (50 vs 200)",
                f"50d {ma50:.2f} >= 200d {ma200:.2f} (golden cross)", +1)
        else:
            add("ma_cross", "MA cross (50 vs 200)",
                f"50d {ma50:.2f} < 200d {ma200:.2f} (death cross)", -1)

    # 9) Momentum — RSI overbought (>70) / oversold (<30) / neutral.
    if _is_number(rsi):
        if rsi > 70:
            add("rsi", "Momentum (RSI)", f"RSI {rsi:.0f} (overbought)", -1)
        elif rsi < 30:
            add("rsi", "Momentum (RSI)", f"RSI {rsi:.0f} (oversold)", +1)
        else:
            add("rsi", "Momentum (RSI)", f"RSI {rsi:.0f} (neutral)", 0)

    # 10) Position within the 52-week range (relative strength).
    if (_is_number(price) and _is_number(high52) and _is_number(low52)
            and high52 > low52):
        position = (price - low52) / (high52 - low52)
        pct = position * 100
        if position >= 0.5:
            add("range52", "52-week range position", f"{pct:.0f}% of range (upper half)", +1)
        else:
            add("range52", "52-week range position", f"{pct:.0f}% of range (lower half)", -1)

    # 11) Momentum — MACD line above/below its signal line.
    if _is_number(macd_line) and _is_number(macd_signal):
        if macd_line > macd_signal:
            add("macd", "Momentum (MACD)",
                f"MACD {macd_line:.2f} > signal {macd_signal:.2f} (bullish)", +1)
        else:
            add("macd", "Momentum (MACD)",
                f"MACD {macd_line:.2f} < signal {macd_signal:.2f} (bearish)", -1)

    # 12) Volatility position — current price vs the Bollinger Bands.
    if _is_number(price) and _is_number(bb_upper) and _is_number(bb_lower):
        if price > bb_upper:
            add("bollinger", "Bollinger position",
                f"price {price:.2f} above upper {bb_upper:.2f} (overbought)", -1)
        elif price < bb_lower:
            add("bollinger", "Bollinger position",
                f"price {price:.2f} below lower {bb_lower:.2f} (oversold)", +1)
        else:
            add("bollinger", "Bollinger position",
                f"price {price:.2f} within bands", 0)

    # 13) Volume confirmation — a notable price move SHOULD be backed by volume.
    #     A strong GAIN on weak volume is the classic unreliable "fake" move, so
    #     it scores a big NEGATIVE (-2) — enough to pull the (volume-heavy) 6M
    #     verdict down. A drop on weak volume may not stick (+1). Moves confirmed
    #     by volume score with their direction.
    if vol_confirm in ("confirmed", "unconfirmed") and _is_number(vol_move):
        gain = vol_move > 0
        if vol_confirm == "unconfirmed" and gain:
            add("vol_confirm", "Volume confirmation",
                f"+{vol_move:.1f}% move on weak volume (unconfirmed)", -2)
        elif vol_confirm == "unconfirmed" and not gain:
            add("vol_confirm", "Volume confirmation",
                f"{vol_move:.1f}% move on weak volume (unconfirmed)", +1)
        elif vol_confirm == "confirmed" and gain:
            add("vol_confirm", "Volume confirmation",
                f"+{vol_move:.1f}% move on strong volume (confirmed)", +1)
        else:  # confirmed drop
            add("vol_confirm", "Volume confirmation",
                f"{vol_move:.1f}% move on strong volume (confirmed)", -1)

    # 14) OBV trend — buying pressure (rising) vs selling pressure (falling).
    if obv_trend == "rising":
        add("obv", "OBV (buy/sell pressure)", "OBV rising (accumulation)", +1)
    elif obv_trend == "falling":
        add("obv", "OBV (buy/sell pressure)", "OBV falling (distribution)", -1)

    # 15) Accumulation/Distribution trend — same idea, price-position weighted.
    if ad_trend == "rising":
        add("ad", "Accum/Distribution", "A/D rising (accumulation)", +1)
    elif ad_trend == "falling":
        add("ad", "Accum/Distribution", "A/D falling (distribution)", -1)

    # Weight the shared signals three ways — once per horizon.
    horizons = {h: _score_horizon(h, signals) for h in HORIZONS}
    return Verdict(True, display, horizons=horizons, signals=signals)


# --- Analyst consensus (Step 5) ------------------------------------------

# Yahoo's recommendationKey -> a human label.
_REC_LABELS = {
    "strong_buy": "Strong Buy", "buy": "Buy", "hold": "Hold",
    "underperform": "Underperform", "sell": "Sell", "strong_sell": "Strong Sell",
}
# Yahoo's "Action" code on an upgrade/downgrade row -> a readable word.
_ACTION_WORDS = {
    "up": "upgraded", "down": "downgraded", "main": "maintained",
    "reit": "reiterated", "init": "initiated",
}


@dataclass
class AnalystConsensus:
    """
    Wall-Street consensus for a stock (from Yahoo's free analyst fields).

    - found        : False if the ticker doesn't exist.
    - has_coverage : False if the stock simply isn't covered by analysts
                     (common for small/Tel-Aviv names) -> show a gentle note.
    - label        : humanized consensus, e.g. "Buy" (for side-by-side vs ours).
    - mean         : recommendationMean on Yahoo's 1 (Strong Buy)..5 (Sell) scale.
    - target_*     : mean / high / low / median 12-month price targets.
    - upside_pct   : mean target vs current price, in %.
    - actions      : recent upgrades/downgrades (date, firm, grades, target).
    """
    found: bool
    symbol: str
    has_coverage: bool = False
    recommendation_key: str = ""
    label: str = ""
    mean: Optional[float] = None
    num_analysts: Optional[int] = None
    currency: str = ""
    current_price: Optional[float] = None
    target_mean: Optional[float] = None
    target_high: Optional[float] = None
    target_low: Optional[float] = None
    target_median: Optional[float] = None
    upside_pct: Optional[float] = None
    actions: list = field(default_factory=list)
    reason: str = ""


def _humanize_recommendation(rec_key, mean):
    """Turn Yahoo's recommendationKey (or the numeric mean) into a label."""
    if rec_key in _REC_LABELS:
        return _REC_LABELS[rec_key]
    if _is_number(mean):
        if mean <= 1.5:
            return "Strong Buy"
        if mean <= 2.5:
            return "Buy"
        if mean <= 3.5:
            return "Hold"
        if mean <= 4.5:
            return "Sell"
        return "Strong Sell"
    return "n/a"


def get_analyst_consensus(ticker: str) -> AnalystConsensus:
    """
    Fetch the analyst consensus, mean price target, and recent upgrades/
    downgrades for a ticker. Never crashes; returns a clear "no coverage" state
    for stocks analysts don't follow.
    """
    symbol = (ticker or "").strip()
    display = symbol.upper()
    if not symbol:
        return AnalystConsensus(False, display)

    info, _ = _fetch_info_resilient(symbol)  # resilient to transient empty .info
    stock = yf.Ticker(symbol)

    # Real ticker? identity OR a live price.
    if not _has_identity(info) and not _is_number(_safe(stock.fast_info, "last_price")):
        return AnalystConsensus(False, display, reason="Ticker not found.")

    currency = info.get("currency") or _safe(stock.fast_info, "currency") or ""
    current_price = (info.get("currentPrice") or info.get("regularMarketPrice")
                     or _safe(stock.fast_info, "last_price"))

    rec_key = (info.get("recommendationKey") or "").lower()
    mean = info.get("recommendationMean")
    num = info.get("numberOfAnalystOpinions")
    target_mean = info.get("targetMeanPrice")
    target_high = info.get("targetHighPrice")
    target_low = info.get("targetLowPrice")
    target_median = info.get("targetMedianPrice")

    # "Coverage" = analysts actually rate it.
    has_coverage = (_is_number(mean) or _is_number(target_mean)
                    or (rec_key and rec_key != "none"))
    if not has_coverage:
        return AnalystConsensus(True, display, has_coverage=False, currency=currency,
                                current_price=(current_price if _is_number(current_price) else None),
                                reason="No analyst coverage for this stock.")

    upside_pct = None
    if _is_number(target_mean) and _is_number(current_price) and current_price:
        upside_pct = (target_mean / current_price - 1) * 100

    # Recent upgrades/downgrades (most recent first).
    actions = []
    try:
        ud = stock.upgrades_downgrades
        if ud is not None and len(ud):
            for date, row in ud.sort_index(ascending=False).head(6).iterrows():
                target = row.get("currentPriceTarget")
                actions.append({
                    "date": str(date)[:10],
                    "firm": row.get("Firm", "") or "",
                    "from_grade": row.get("FromGrade", "") or "",
                    "to_grade": row.get("ToGrade", "") or "",
                    "action": (row.get("Action", "") or "").lower(),
                    "price_target": (float(target) if _is_number(target) and target else None),
                })
    except Exception:
        actions = []

    return AnalystConsensus(
        True, display, has_coverage=True, recommendation_key=rec_key,
        label=_humanize_recommendation(rec_key, mean),
        mean=(float(mean) if _is_number(mean) else None),
        num_analysts=(int(num) if _is_number(num) else None),
        currency=currency,
        current_price=(float(current_price) if _is_number(current_price) else None),
        target_mean=(float(target_mean) if _is_number(target_mean) else None),
        target_high=(float(target_high) if _is_number(target_high) else None),
        target_low=(float(target_low) if _is_number(target_low) else None),
        target_median=(float(target_median) if _is_number(target_median) else None),
        upside_pct=upside_pct, actions=actions)


def humanize_action(action_code):
    """Public helper for the UI: 'up' -> 'upgraded', etc."""
    return _ACTION_WORDS.get(action_code, action_code or "rated")


# --- Explain a divergence between our verdict and the analysts ------------

# Put every label on a simple 0..3 bullishness ladder so we can compare ours
# (Sell/Hold/Buy/Strong Buy) with the analysts' (which may also be Underperform).
_RANK = {"Strong Sell": 0, "Sell": 0, "Underperform": 0,
         "Hold": 1, "Buy": 2, "Strong Buy": 3}


@dataclass
class Divergence:
    """
    Why our verdict and the analyst consensus differ (when they do).

    - diverges  : True if they differ by at least one rung on the ladder.
    - direction : "analysts_more_bullish" / "we_more_bullish" / "aligned".
    - drivers   : the signals most responsible for the gap, as
                  (name, measured, weighted) tuples.
    - note      : a plain-language explanation.
    """
    diverges: bool
    direction: str
    our_label: str
    analyst_label: str
    gap: int
    drivers: list
    note: str


def explain_divergence(verdict, analyst, horizon: str = "1Y") -> Divergence:
    """
    Compare our `horizon` verdict with the analyst consensus and, if they differ
    meaningfully, explain why — naming the signals that drive the gap. We compare
    against the 1-year horizon by default, since analyst targets are ~12 months.
    """
    if (verdict is None or not verdict.found
            or analyst is None or not analyst.found or not analyst.has_coverage):
        return Divergence(False, "aligned", "", "", 0, [], "")

    hv = verdict.horizons.get(horizon)
    if hv is None or not hv.enough_data:
        return Divergence(False, "aligned", "", analyst.label, 0, [], "")

    our_label, analyst_label = hv.label, analyst.label
    gap = _RANK.get(analyst_label, 1) - _RANK.get(our_label, 1)

    if gap >= 1:
        # Analysts more bullish -> what's holding OUR score down (most negative).
        drivers = sorted((w for w in hv.breakdown if w.weight > 0 and w.weighted < 0),
                         key=lambda w: w.weighted)[:3]
        note = ("Analysts are more bullish than our model. Our score is rule-based "
                "on today's numbers and is held back by the items below. Analysts "
                "usually look further ahead and weigh forward earnings growth, "
                "12-month price targets, and qualitative factors our rules don't "
                "capture.")
    elif gap <= -1:
        # We're more bullish -> what's lifting OUR score (most positive).
        drivers = sorted((w for w in hv.breakdown if w.weight > 0 and w.weighted > 0),
                         key=lambda w: w.weighted, reverse=True)[:3]
        note = ("Our model is more bullish than analysts. It rewards the strengths "
                "below; analysts may be more cautious on valuation, near-term risks, "
                "or information beyond public price/fundamentals data.")
    else:
        # Same rung -> aligned. Still return a (positive) note so the UI is
        # never mysteriously empty.
        return Divergence(
            False, "aligned", our_label, analyst_label, 0, [],
            f"Our 1-year verdict ({our_label}) is in line with the analyst "
            f"consensus ({analyst_label}).")

    driver_list = [(w.name, w.measured, w.weighted) for w in drivers]
    return Divergence(True, ("analysts_more_bullish" if gap >= 1 else "we_more_bullish"),
                      our_label, analyst_label, gap, driver_list, note)


# --- Plain-language help tooltips (one place, easy to edit) ----------------
# Keyed by the metric key used in get_company_metrics / get_stock_technicals
# (plus a couple of UI items). Each is a short, beginner-friendly one-liner the
# UI shows as a "?" tooltip next to the metric.
HELP_TEXTS = {
    # --- Company fundamentals ---
    "market_cap": "The total market value of the company (share price times the number of shares).",
    "pe": "Price-to-Earnings: share price divided by yearly earnings per share. High can mean expensive or high growth expectations.",
    "forward_pe": "Like P/E but using analysts' forecast of NEXT year's earnings. Lower than the trailing P/E hints earnings should grow.",
    "peg": "P/E divided by the earnings growth rate. Below about 1 suggests the stock is cheap relative to how fast it's growing.",
    "eps": "Earnings Per Share: the company's yearly profit divided by its number of shares.",
    "revenue": "Total sales (the 'top line') over the last twelve months.",
    "earnings_growth": "How much profit grew compared with a year ago.",
    "revenue_growth": "How much sales grew compared with a year ago.",
    "profit_margin": "The share of revenue kept as profit after all costs. Higher means more profitable.",
    "dividend_yield": "Yearly dividend as a percent of the share price - the cash income from holding the stock.",
    "debt_to_equity": "How much debt the company has versus shareholders' equity. Higher means more borrowing (more risk).",
    "free_cash_flow": "Cash left after running and investing in the business - money it can return or reinvest.",
    "next_earnings": "The date of the company's next quarterly earnings report.",
    "sector": "The broad part of the economy the company operates in.",
    "industry": "The specific business area within its sector.",
    # --- Price levels, trend & momentum ---
    "week52_high": "The highest price the stock reached in the past year.",
    "week52_low": "The lowest price the stock reached in the past year.",
    "ma50": "The average closing price over the last 50 trading days - a short-to-medium-term trend line.",
    "ma200": "The average closing price over the last 200 trading days - a long-term trend line.",
    "rsi": "Relative Strength Index (0-100). Above 70 is 'overbought' (may pause); below 30 is 'oversold' (may bounce).",
    "beta": "How much the stock moves versus the market. 1 = moves with the market; above 1 = more volatile.",
    "macd": "MACD line: the gap between the 12-day and 26-day average prices. Positive means short-term momentum is upward.",
    "macd_signal": "A 9-day smoothed version of the MACD line, used as a trigger for crossovers.",
    "macd_hist": "MACD line minus its signal line. Rising bars suggest strengthening momentum.",
    "macd_state": "'Bullish' when the MACD line is above its signal line; 'bearish' when below.",
    "bb_upper": "Bollinger upper band: about 2 standard deviations above the 20-day average - a 'stretched high' zone.",
    "bb_middle": "Bollinger middle band: the 20-day average price.",
    "bb_lower": "Bollinger lower band: about 2 standard deviations below the 20-day average - a 'stretched low' zone.",
    "bb_state": "Where today's price sits versus the bands: near the upper, near the lower, or within them.",
    # --- Volume & buy/sell pressure (estimates) ---
    "avg_volume": "The typical number of shares traded per day.",
    "vol_recent": "Average daily trading volume over the last 5 days.",
    "vol_avg": "Average daily trading volume over the last 50 days, used as the baseline.",
    "vol_move": "The stock's percent price change over the last 5 days.",
    "vol_confirm": "Whether a recent notable price move was backed by above-average volume ('confirmed') or not ('unconfirmed').",
    "obv_value": "On-Balance Volume: adds volume on up-days and subtracts it on down-days - an ESTIMATE of buying vs selling pressure.",
    "obv_trend": "Whether OBV has been rising (buying pressure) or falling (selling pressure) recently.",
    "ad_value": "Accumulation/Distribution: estimates buying vs selling pressure from where each day closes in its range, times volume.",
    "ad_trend": "Whether the A/D line has been rising (accumulation) or falling (distribution) recently.",
    # --- UI items ---
    "verdict_score": "Our rule-based score from 0 to 100, where 50 is neutral. Higher is more bullish. It's an automated opinion, not advice.",
    "analyst_mean": "The average analyst rating on a 1-to-5 scale, where 1 = Strong Buy and 5 = Sell.",
}
