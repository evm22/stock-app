"""
Stock Analysis App — Streamlit UI (display layer only).

All the data logic lives in engine.py (pure Python). This file just:
  1. draws the page,
  2. asks the engine for a quote,
  3. shows the result (or a friendly "couldn't find" message).

Run locally with:
    streamlit run app.py
"""

import altair as alt  # charting library that ships with Streamlit
import streamlit as st

import engine  # our pure-Python data engine (no Streamlit inside it)

# Basic page configuration (title shown in the browser tab, etc.).
st.set_page_config(page_title="Stock Analysis App", page_icon="📈")


# @st.cache_data remembers this function's result for each ticker, so when
# Streamlit reruns the script (it reruns on every interaction) we reuse the
# data instead of re-asking Yahoo. ttl=600 means "forget after 600 seconds
# (10 minutes)" so prices still stay reasonably fresh.
#
# Caching lives here in the UI layer; the engine itself stays plain Python.
@st.cache_data(ttl=600)
def load_quote(symbol):
    """Thin cached wrapper around the engine so reruns don't re-hit Yahoo."""
    return engine.get_stock_quote(symbol)


@st.cache_data(ttl=600)
def load_history(symbol, range_key):
    """Cached wrapper for price history, so flipping ranges back and forth is fast."""
    return engine.get_price_history(symbol, range_key)


def make_candlestick(df):
    """
    Build a simple candlestick chart from a price-history table using Altair.

    Each candle has a thin "wick" (the day's low-to-high range) and a thick
    "body" (open-to-close). Green means the price closed up, red means down.
    """
    # Green when close >= open (up day), red otherwise (down day).
    up_down_color = alt.condition(
        "datum.Open <= datum.Close",
        alt.value("#26a69a"),  # green
        alt.value("#ef5350"),  # red
    )
    base = alt.Chart(df).encode(
        x=alt.X("Date:T", title=None),
        color=up_down_color,
    )
    # The wick: a vertical line from Low to High.
    wicks = base.mark_rule().encode(
        y=alt.Y("Low:Q", title="Price", scale=alt.Scale(zero=False)),
        y2="High:Q",
    )
    # The body: a bar from Open to Close.
    bodies = base.mark_bar().encode(
        y="Open:Q",
        y2="Close:Q",
    )
    return wicks + bodies


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
        quote = load_quote(symbol)
    except Exception:
        quote = None

    if quote is None or not quote.found:
        # Reached when the engine signalled "not found" (every price source
        # failed) or an unexpected error occurred.
        st.error("Couldn't find that ticker — please check the symbol.")
    else:
        # Company name as a small heading.
        st.subheader(quote.name)

        # Show the latest price together with its currency (e.g. "USD").
        currency = quote.currency or ""
        st.write(f"**Current price:** {quote.price:,.2f} {currency}")

        # The engine already worked out the day's change for us.
        if quote.change_abs is not None:
            arrow = "🔺" if quote.change_abs >= 0 else "🔻"  # quick visual up/down
            sign = "+" if quote.change_abs >= 0 else ""        # show "+" for gains
            # (negative numbers already print their own minus sign)
            st.write(
                f"**Today:** {arrow} {sign}{quote.change_abs:,.2f} {currency} "
                f"({sign}{quote.change_pct:.2f}%)"
            )
        else:
            st.write("**Today:** change not available.")

        # One-line note of which exchange this is and the exact symbol used.
        exchange = quote.exchange or "unknown exchange"
        st.caption(f"{quote.symbol} · {exchange}")

        # --- Price history chart ----------------------------------------
        st.divider()
        st.markdown("### Price history")

        # Range selector as a row of buttons (segmented control). The keys come
        # straight from the engine so the two never drift apart. Default = 1M.
        range_key = st.segmented_control(
            "Range",
            options=list(engine.RANGES.keys()),
            default="1M",
            label_visibility="collapsed",
        )
        # If the user clicks the active button it deselects (returns None);
        # fall back to the default so a chart is always shown.
        range_key = range_key or "1M"

        # Toggle between a clean line of closing prices and full candlesticks.
        chart_type = st.radio(
            "Chart type",
            options=["Line", "Candlestick"],
            horizontal=True,
        )

        # Fetch the candles for the chosen range (cached).
        history = load_history(symbol, range_key)

        if not history.found:
            # Empty range (common for 1D/1W intraday on the free tier).
            st.info(f"No chart data for {range_key}: {history.reason}")
        elif chart_type == "Line":
            # Built-in line chart: closing price over time (Date as the x-axis).
            st.line_chart(history.data.set_index("Date")["Close"])
        else:
            # Candlestick via Altair (open/high/low/close).
            st.altair_chart(make_candlestick(history.data), width="stretch")

        # Collapsed by default. Open it to see WHICH source gave us the price
        # and every raw value we tried — handy for diagnosing odd tickers.
        with st.expander("🔧 Debug details", expanded=False):
            st.write(f"**Price came from:** `{quote.price_source}`")
            st.write(
                f"**Previous close came from:** "
                f"`{quote.prev_source or 'not available'}`"
            )
            st.write("**Price candidates tried:**")
            st.write(quote.sources.get("price_candidates", {}))
            st.write("**Previous-close candidates tried:**")
            st.write(quote.sources.get("prev_candidates", {}))
