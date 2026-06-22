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
from dataclasses import dataclass, field
from typing import Optional

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
