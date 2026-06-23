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


@st.cache_data(ttl=600)
def load_company(symbol):
    """Cached wrapper for the company fundamentals."""
    return engine.get_company_metrics(symbol)


@st.cache_data(ttl=600)
def load_technicals(symbol):
    """Cached wrapper for the stock/technical indicators."""
    return engine.get_stock_technicals(symbol)


@st.cache_data(ttl=600)
def load_verdict(symbol):
    """Cached wrapper for the rule-based verdict."""
    return engine.compute_verdict(symbol)


# Colour for each verdict label, on a red (bad) -> green (good) scale.
# These names are Streamlit's built-in markdown colours (e.g. :green[...]).
VERDICT_COLORS = {
    "Sell": "red",
    "Hold": "orange",
    "Buy": "green",
    "Strong Buy": "green",
}

# Friendly display names for the three verdict horizons.
HORIZON_NAMES = {
    "6M": "6-month (short term)",
    "1Y": "1-year (medium term)",
    "5Y": "5-year (long term)",
}


def _abbreviate(number):
    """Shorten big numbers for display: 4_362_291_642_368 -> '4.36T'."""
    number = float(number)
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(number) >= size:
            return f"{number / size:,.2f}{suffix}"
    return f"{number:,.0f}"


def format_metric(metric, currency):
    """
    Turn a Metric into a display string, using its `fmt` hint. Missing values
    show as a friendly "n/a". `currency` (e.g. "USD") is appended to money values.
    """
    if not metric.available:
        return "n/a"

    value = metric.value
    fmt = metric.fmt
    cur = f" {currency}" if currency else ""

    if fmt == "large_money":   # market cap, revenue, free cash flow
        return f"{_abbreviate(value)}{cur}"
    if fmt == "money":         # per-share prices: EPS, 52w high/low, MAs
        return f"{value:,.2f}{cur}"
    if fmt == "ratio":         # P/E, debt-to-equity, beta, RSI
        return f"{value:,.2f}"
    if fmt == "percent_frac":  # stored as a fraction (0.27) -> 27.15%
        return f"{value * 100:.2f}%"
    if fmt == "percent":       # already a percent (0.36) -> 0.36%
        return f"{value:.2f}%"
    if fmt == "int_large":     # average volume
        return _abbreviate(value)
    # "date" and "text" both just print the value as-is.
    return str(value)


def render_metrics(group, columns_per_row=3):
    """Show a MetricGroup as a tidy grid of st.metric tiles (n/a when missing)."""
    items = list(group.metrics.values())
    for start in range(0, len(items), columns_per_row):
        row = items[start:start + columns_per_row]
        columns = st.columns(columns_per_row)
        for column, metric in zip(columns, row):
            column.metric(metric.label, format_metric(metric, group.currency))


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


def make_volume_chart(df):
    """
    A subtle volume sub-panel (muted grey bars, short height) to sit beneath the
    price chart. Kept visually secondary so the price stays the focus.
    """
    return (
        alt.Chart(df)
        .mark_bar(color="#9aa0a6", opacity=0.6)  # muted grey, semi-transparent
        .encode(
            x=alt.X("Date:T", title=None),
            y=alt.Y("Volume:Q", title="Volume"),
        )
        .properties(height=120)  # short, so it doesn't dominate the price chart
    )


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

        # --- Verdict (rule-based, three time horizons) ------------------
        st.divider()
        st.markdown("### Verdict")
        try:
            verdict = load_verdict(symbol)
        except Exception:
            verdict = None

        if verdict is None or not verdict.found:
            st.info("No verdict available for this ticker.")
        else:
            # Three colour-coded tiles, short -> long horizon.
            columns = st.columns(len(engine.HORIZONS))
            for column, horizon in zip(columns, engine.HORIZONS):
                hv = verdict.horizons.get(horizon)
                column.markdown(f"**{HORIZON_NAMES.get(horizon, horizon)}**")
                if hv is None or not hv.enough_data:
                    column.info("Not enough data")
                    continue
                color = VERDICT_COLORS.get(hv.label, "gray")
                column.markdown(f":{color}[**{hv.label}**]")
                column.markdown(f"score **{hv.score:.0f}**/100")
                column.progress(int(round(hv.score)))

            st.caption(
                "⚠️ Automated, rule-based opinion from public data — "
                "**not financial advice.** The three horizons re-weight the "
                "**same current data** differently; they do **not** predict the future."
            )

            # Transparent per-horizon breakdown — one tab each.
            with st.expander("Why this verdict? (how it was calculated)"):
                tabs = st.tabs([HORIZON_NAMES.get(h, h) for h in engine.HORIZONS])
                for tab, horizon in zip(tabs, engine.HORIZONS):
                    hv = verdict.horizons.get(horizon)
                    with tab:
                        if hv is None or not hv.enough_data:
                            st.info(hv.reason if hv else "Not enough data.")
                            continue
                        st.caption(
                            f"Weighted score {hv.score:.1f}/100 (50 = neutral). "
                            "Each signal: base points × horizon weight = contribution."
                        )
                        for ws in hv.breakdown:
                            base_sign = "+" if ws.points > 0 else ""  # minus self-prints
                            w_sign = "+" if ws.weighted > 0 else ""
                            st.write(
                                f"- **{ws.name}** — {ws.measured} | "
                                f"base {base_sign}{ws.points} × weight {ws.weight:.1f} "
                                f"= **{w_sign}{ws.weighted:.1f}**"
                            )

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

        # Y-axis toggle: actual Price (default) vs % change from the start of
        # the selected range (normalised so the first point = 0%).
        as_percent = st.toggle(
            "Show as % change from start of range",
            value=False,
            help="Normalises the line so the start of the range = 0%, showing "
                 "the percentage gain/loss across the range.",
        )

        # Fetch the candles for the chosen range (cached).
        history = load_history(symbol, range_key)

        if not history.found:
            # Empty range (common for 1D/1W intraday on the free tier).
            st.info(f"No chart data for {range_key}: {history.reason}")
        else:
            # --- Price chart (line or candlestick) ---
            if chart_type == "Line":
                closes = history.data.set_index("Date")["Close"]
                if as_percent:
                    # Each point as % difference from the first close of the range.
                    pct = (closes / closes.iloc[0] - 1) * 100
                    pct.name = "% change"
                    st.line_chart(pct)
                else:
                    # Built-in line chart: closing price over time.
                    st.line_chart(closes)
            else:
                # Candlestick always shows price (open/high/low/close).
                if as_percent:
                    st.caption("ℹ️ % view applies to the line chart; "
                               "candlestick stays in price.")
                st.altair_chart(make_candlestick(history.data), width="stretch")

            # --- Subtle volume sub-panel ---
            # Only show it when there's real volume data for this range; if it's
            # missing or all-zero (can happen on some ranges), hide it quietly.
            if "Volume" in history.data.columns:
                volumes = history.data["Volume"].dropna()
                if len(volumes) > 0 and float(volumes.sum()) > 0:
                    st.caption("Volume")
                    st.altair_chart(make_volume_chart(history.data), width="stretch")

        # --- Company analysis (the business) ----------------------------
        st.divider()
        st.markdown("## Company analysis")
        st.caption("The business behind the stock.")
        try:
            company = load_company(symbol)
        except Exception:
            company = None
        if company is not None and company.found and company.metrics:
            render_metrics(company)
        else:
            st.info("No company metrics available for this ticker.")

        # --- Stock analysis (the share-price behaviour) -----------------
        st.divider()
        st.markdown("## Stock analysis")
        st.caption("How the share price itself has been behaving.")
        try:
            technicals = load_technicals(symbol)
        except Exception:
            technicals = None
        if technicals is not None and technicals.found and technicals.metrics:
            render_metrics(technicals)
            st.caption(
                "Note: **OBV** and **Accum/Dist** are *estimates* of buying/"
                "selling pressure derived from price + volume — not true "
                "order-flow data (which isn't available for free)."
            )
        else:
            st.info("No stock metrics available for this ticker.")

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
            # Which fundamentals path supplied the company metrics (Part A).
            if company is not None and getattr(company, "source_note", ""):
                st.write(f"**Company fundamentals via:** `{company.source_note}`")
