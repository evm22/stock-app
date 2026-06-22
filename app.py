"""
Stock Analysis App — entry point.

Phase 1, step 1: look up a single stock by its ticker symbol and show a few
basic facts (name, latest price, day's change, exchange). No charts or
analysis yet — that comes later.

Run locally with:
    streamlit run app.py
"""

import streamlit as st
import yfinance as yf  # free Yahoo Finance data — our source for stock prices

# Basic page configuration (title shown in the browser tab, etc.).
st.set_page_config(page_title="Stock Analysis App", page_icon="📈")


def _safe(fast_info, key):
    """
    Read one value from yfinance's `fast_info` without crashing.

    `fast_info` behaves like a dictionary, but a missing key raises an error.
    This little helper returns None instead, so the rest of the code stays
    simple and never blows up on a missing field.
    """
    try:
        return fast_info[key]
    except Exception:
        return None


# @st.cache_data remembers the result of this function for each ticker, so if
# Streamlit reruns the script (it reruns on every interaction) we reuse the
# data instead of asking Yahoo again. ttl=600 means "forget after 600 seconds
# (10 minutes)" so prices still stay reasonably fresh.
@st.cache_data(ttl=600)
def fetch_stock(symbol):
    """
    Look up one stock by its ticker symbol.

    Returns a dictionary of the fields we want to display, or None if the
    ticker doesn't seem to exist (no price available).
    """
    ticker = yf.Ticker(symbol)

    # `fast_info` is a lightweight, reliable source for price/currency/exchange.
    fast = ticker.fast_info

    # The latest price is also our "does this ticker exist?" check:
    # a bad symbol won't have one.
    last_price = _safe(fast, "last_price")
    if not last_price:
        return None

    # The full company name lives in the heavier `info` dictionary, which can
    # occasionally be missing or slow — so we fall back to the symbol itself.
    try:
        info = ticker.info
        name = info.get("longName") or info.get("shortName") or symbol.upper()
    except Exception:
        name = symbol.upper()

    return {
        "name": name,
        "symbol": symbol.upper(),
        "price": last_price,
        "previous_close": _safe(fast, "previous_close"),
        "currency": _safe(fast, "currency"),
        "exchange": _safe(fast, "exchange"),
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
        # Either the ticker is invalid or Yahoo returned nothing useful.
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
            change = data["price"] - prev          # absolute change, e.g. -1.23
            percent = (change / prev) * 100         # percent change
            arrow = "🔺" if change >= 0 else "🔻"   # quick visual up/down
            sign = "+" if change >= 0 else ""        # show "+" for gains
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
