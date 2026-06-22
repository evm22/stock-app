"""
Stock Analysis App — entry point.

Phase 1, step 1: look up a single stock by its ticker symbol and show a few
basic facts (name, latest price, day's change, exchange). No charts or
analysis yet — that comes later.

Run locally with:
    streamlit run app.py
"""

import math

import streamlit as st
import yfinance as yf  # free Yahoo Finance data — our source for stock prices

# Basic page configuration (title shown in the browser tab, etc.).
st.set_page_config(page_title="Stock Analysis App", page_icon="📈")


def _is_number(value):
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


def _first_valid(candidates, debug, debug_key):
    """
    Given a list of (source_name, value) pairs, return the first pair whose
    value is a real number — as (value, source_name). If none are valid,
    return (None, None).

    We also record every raw value we looked at into `debug` so the on-screen
    "Debug details" expander can show exactly what each source returned. This
    is how we diagnose tickers that misbehave (like AAPL did).
    """
    # Save the raw values (as text) for the debug panel.
    debug[debug_key] = {name: ("None" if val is None else str(val))
                        for name, val in candidates}
    for name, value in candidates:
        if _is_number(value):
            return float(value), name
    return None, None


# @st.cache_data remembers this function's result for each ticker, so when
# Streamlit reruns the script (it reruns on every interaction) we reuse the
# data instead of re-asking Yahoo. ttl=600 means "forget after 600 seconds
# (10 minutes)" so prices still stay reasonably fresh.
@st.cache_data(ttl=600)
def fetch_stock(symbol):
    """
    Look up one stock by its ticker symbol, robustly.

    Different tickers expose their price through different fields depending on
    the yfinance / pandas versions in use, so instead of trusting one field we
    try several sources in order and use the first that gives a real number.

    Returns a dictionary of the fields we want to display, or None only if
    EVERY price source failed (then we treat the ticker as "not found").
    """
    ticker = yf.Ticker(symbol)
    debug = {}  # collected raw values, shown in the Debug details expander

    # --- Gather the three raw data sources once -------------------------
    # 1) fast_info: a lightweight bundle of quote fields.
    fast = ticker.fast_info

    # 2) info: a heavier dictionary with lots of fields (can be slow/missing).
    try:
        info = ticker.info
    except Exception:
        info = {}

    # 3) history: the last few days of prices, used as a final fallback.
    #    We keep only the "Close" column and drop any empty (NaN) rows.
    try:
        hist = ticker.history(period="5d")
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
    ], debug, "price_candidates")

    # If we couldn't get a price from ANY source, the ticker is unusable.
    if price is None:
        return None

    # --- Previous close (for the day's change): same fallback idea ------
    previous_close, prev_source = _first_valid([
        ("fast_info.previous_close",          _safe(fast, "previous_close")),
        ("info.regularMarketPreviousClose",   info.get("regularMarketPreviousClose")),
        ("history.prev_close",                hist_prev),
    ], debug, "prev_candidates")

    # --- Company name: longName → shortName → the symbol itself ---------
    name = info.get("longName") or info.get("shortName") or symbol.upper()

    # Currency / exchange: prefer fast_info, fall back to info.
    currency = _safe(fast, "currency") or info.get("currency")
    exchange = _safe(fast, "exchange") or info.get("exchange")

    return {
        "name": name,
        "symbol": symbol.upper(),
        "price": price,
        "price_source": price_source,
        "previous_close": previous_close,
        "prev_source": prev_source,
        "currency": currency,
        "exchange": exchange,
        "debug": debug,
    }


# --- Page content ---------------------------------------------------------

st.title("📈 Stock Analysis App")
st.write("Type a stock ticker symbol and press Enter to look it up.")

# A text box for the ticker. Whatever the user types is stored in `symbol`.
# .strip() removes accidental spaces; pressing Enter reruns the script.
symbol = st.text_input(
    "Stock ticker",
    placeholder="e.g. AAPL, MSFT, or an Israeli stock like TEVA.TA",
).strip()

# Small hint: Israeli (Tel Aviv) stocks need a ".TA" suffix on Yahoo Finance.
st.caption("Tip: for Israeli stocks add `.TA`, e.g. `TEVA.TA`.")

# Only do something once the user has actually typed a symbol.
if symbol:
    # Wrap the data fetch in try/except so a network glitch or unexpected
    # error shows a friendly message instead of a red crash screen.
    try:
        data = fetch_stock(symbol)
    except Exception:
        data = None

    if data is None:
        # Only reached when EVERY price source failed.
        st.error("Couldn't find that ticker — please check the symbol.")
    else:
        # Company name as a small heading.
        st.subheader(data["name"])

        # Show the latest price together with its currency (e.g. "USD").
        currency = data["currency"] or ""
        st.write(f"**Current price:** {data['price']:,.2f} {currency}")

        # The day's change needs yesterday's close to compare against.
        prev = data["previous_close"]
        if prev:
            change = data["price"] - prev           # absolute change, e.g. -1.23
            percent = (change / prev) * 100          # percent change
            arrow = "🔺" if change >= 0 else "🔻"    # quick visual up/down
            sign = "+" if change >= 0 else ""         # show "+" for gains
            # (negative numbers already print their own minus sign)
            st.write(
                f"**Today:** {arrow} {sign}{change:,.2f} {currency} "
                f"({sign}{percent:.2f}%)"
            )
        else:
            st.write("**Today:** change not available.")

        # One-line note of which exchange this is and the exact symbol used.
        exchange = data["exchange"] or "unknown exchange"
        st.caption(f"{data['symbol']} · {exchange}")

        # Collapsed by default. Open it to see WHICH source gave us the price
        # and every raw value we tried — handy for diagnosing odd tickers.
        with st.expander("🔧 Debug details", expanded=False):
            st.write(f"**Price came from:** `{data['price_source']}`")
            st.write(
                f"**Previous close came from:** "
                f"`{data['prev_source'] or 'not available'}`"
            )
            st.write("**Price candidates tried:**")
            st.write(data["debug"].get("price_candidates", {}))
            st.write("**Previous-close candidates tried:**")
            st.write(data["debug"].get("prev_candidates", {}))
