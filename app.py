"""
Stock Analysis App — Streamlit UI (display layer only).

All the data logic lives in engine.py (pure Python). This file just:
  1. draws the page,
  2. asks the engine for a quote,
  3. shows the result (or a friendly "couldn't find" message).

Run locally with:
    streamlit run app.py
"""

import json
import os

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
def load_matches(query):
    """Cached wrapper for the search resolver (ticker / name / .TA candidates)."""
    return engine.find_tickers(query)


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


@st.cache_data(ttl=600)
def load_analyst(symbol):
    """Cached wrapper for the analyst consensus."""
    return engine.get_analyst_consensus(symbol)


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

# Colour for analyst consensus labels (adds the bearish ones to the scale).
ANALYST_COLORS = {
    "Strong Buy": "green", "Buy": "green", "Hold": "orange",
    "Underperform": "red", "Sell": "red", "Strong Sell": "red",
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


# Stock-analysis tiles are split into two readable groups.
PRICE_TREND_KEYS = ["week52_high", "week52_low", "ma50", "ma200", "rsi", "beta",
                    "macd", "macd_signal", "macd_hist", "macd_state",
                    "bb_upper", "bb_middle", "bb_lower", "bb_state"]
PRESSURE_KEYS = ["avg_volume", "vol_recent", "vol_avg", "vol_move", "vol_confirm",
                 "obv_value", "obv_trend", "ad_value", "ad_trend"]


def render_metrics(group, keys=None, columns_per_row=3):
    """
    Show a MetricGroup as a tidy grid of st.metric tiles (n/a when missing),
    each with a plain-language "?" help tooltip. Pass `keys` to render only a
    subset (and in that order), e.g. to split a section into sub-groups.
    """
    if keys is None:
        items = list(group.metrics.items())
    else:
        items = [(k, group.metrics[k]) for k in keys if k in group.metrics]
    for start in range(0, len(items), columns_per_row):
        row = items[start:start + columns_per_row]
        columns = st.columns(columns_per_row)
        for column, (key, metric) in zip(columns, row):
            column.metric(metric.label, format_metric(metric, group.currency),
                          help=engine.HELP_TEXTS.get(key))


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


# --- Watchlist (persisted in the browser's localStorage) ------------------

WATCHLIST_KEY = "watchlist"


def _local_storage():
    """A handle to the browser's localStorage, or None if we're not running
    under a real Streamlit server (e.g. AppTest / bare mode) — then we fall back
    to session-only state so the UI still works and stays testable. The check
    matters because the localStorage component blocks waiting for a browser that
    isn't there during tests."""
    try:
        # Test seam: AppTest sets this so we skip the (browser-blocking) component.
        if os.environ.get("STOCKAPP_DISABLE_BROWSER_STORAGE"):
            return None
        from streamlit.runtime import exists
        if not exists():
            return None
        from streamlit_local_storage import LocalStorage
        return LocalStorage()
    except Exception:
        return None


def init_watchlist():
    """Load the followed tickers into session_state once (from localStorage when
    available) and return the storage handle. localStorage returns None until
    its component mounts, so we load the first non-None value we see."""
    ls = _local_storage()
    if ls is not None and not st.session_state.get("_wl_loaded"):
        raw = ls.getItem(WATCHLIST_KEY)
        if raw is not None:
            try:
                st.session_state["watchlist"] = list(json.loads(raw) or [])
            except Exception:
                st.session_state["watchlist"] = []
            st.session_state["_wl_loaded"] = True
    st.session_state.setdefault("watchlist", [])
    return ls


def persist_watchlist(ls):
    """Write the current watchlist back to the browser (if available)."""
    if ls is None:
        return
    try:
        ls.setItem(WATCHLIST_KEY, json.dumps(st.session_state["watchlist"]),
                   key="watchlist_persist")
    except Exception:
        pass


# These callbacks run at the START of the next rerun (before the script body),
# so the page below renders with the already-updated watchlist.
def _toggle_watchlist(symbol):
    wl = st.session_state.get("watchlist", [])
    if engine.in_watchlist(wl, symbol):
        st.session_state["watchlist"] = engine.remove_from_watchlist(wl, symbol)
    else:
        st.session_state["watchlist"] = engine.add_to_watchlist(wl, symbol)
    st.session_state["_wl_dirty"] = True


def _remove_watchlist(symbol):
    st.session_state["watchlist"] = engine.remove_from_watchlist(
        st.session_state.get("watchlist", []), symbol)
    st.session_state["_wl_dirty"] = True


def _goto(symbol):
    """A 'View' click pre-fills the search box on the next rerun."""
    st.session_state["pending_query"] = symbol


def render_watchlist_sidebar():
    """Draw the followed stocks in the sidebar with a compact quote, plus
    'View' and 'Remove' buttons."""
    with st.sidebar:
        st.markdown("### ⭐ Watchlist")
        watchlist = st.session_state.get("watchlist", [])
        if not watchlist:
            st.caption("No stocks yet. Search one and click **Add to watchlist**.")
            return
        for sym in watchlist:
            line = f"**{sym}**"
            try:
                q = load_quote(sym)
                if q and q.found:
                    ccy = q.currency or ""
                    if q.change_pct is not None:
                        line += f" — {q.price:,.2f} {ccy} ({q.change_pct:+.2f}%)"
                    else:
                        line += f" — {q.price:,.2f} {ccy}"
            except Exception:
                pass
            st.markdown(line)
            cols = st.columns(2)
            cols[0].button("View", key=f"wl_view_{sym}", on_click=_goto, args=(sym,))
            cols[1].button("Remove", key=f"wl_rm_{sym}",
                           on_click=_remove_watchlist, args=(sym,))
        st.caption("Saved in your browser (localStorage).")


# --- Page content ---------------------------------------------------------

st.title("📈 Stock Analysis App")
st.write("Type a stock ticker symbol and press Enter to look it up.")

# Load the watchlist and draw it in the sidebar. A "View" click stores a
# pending query that we move into the search box BEFORE the widget is created.
ls = init_watchlist()
if "pending_query" in st.session_state:
    st.session_state["search_box"] = st.session_state.pop("pending_query")
render_watchlist_sidebar()

# A search box: accepts a ticker, a company name, or a Tel-Aviv (.TA) symbol.
# .strip() removes accidental spaces; pressing Enter reruns the script.
query = st.text_input(
    "Ticker, company name, or Tel-Aviv (.TA) symbol",
    placeholder="e.g. AAPL, Microsoft, AVIV.TA, TEVA.TA",
    key="search_box",
).strip()

# Israeli stocks use a .TA ticker on Yahoo (NOT their TASE security number).
st.caption("Tip: Israeli (Tel Aviv) stocks use a `.TA` ticker, e.g. `AVIV.TA` "
           "or `TEVA.TA` — not the security number.")

# Resolve the query to candidate tickers. If there's more than one match, let
# the user pick the right one (e.g. the Tel-Aviv AVIV.TA, not a US ETF).
symbol = None
if query:
    try:
        matches = load_matches(query)
    except Exception:
        matches = []

    if not matches:
        st.error("Couldn't find a matching stock — please check the symbol or name.")
        st.caption("If it's an Israeli stock, search by its **.TA ticker** (e.g. "
                   "`AVIV.TA`). Our free data source (Yahoo Finance) can't look up "
                   "Tel-Aviv stocks by their security number.")
    elif len(matches) == 1:
        # Only one match -> nothing to ask, just use it.
        symbol = matches[0].symbol
    else:
        # Several matches -> ASK first. index=None means nothing is pre-selected,
        # so we do NOT analyse anything until the user actively picks the stock
        # they meant (e.g. AVIV.TA rather than a US ETF).
        labels = {m.symbol: f"{m.symbol} - {m.name} ({m.exchange or '?'})"
                  for m in matches}
        symbol = st.selectbox(
            "Found several matches — which stock did you mean?",
            options=[m.symbol for m in matches],
            index=None,
            placeholder="Select the stock you meant...",
            format_func=lambda s: labels.get(s, s),
        )
        if symbol is None:
            st.caption("Pick a match above to see its analysis.")

# Once we have a resolved symbol, run the full analysis on it.
if symbol:
    # Wrap the data fetch in try/except so a network glitch or unexpected
    # error shows a friendly message instead of a red crash screen.
    try:
        quote = load_quote(symbol)
    except Exception:
        quote = None

    if quote is None or not quote.found:
        st.error("Couldn't load that ticker — please try another.")
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

        # Follow / unfollow this stock (saved in the browser).
        following = engine.in_watchlist(st.session_state.get("watchlist", []), symbol)
        st.button(
            "★ Remove from watchlist" if following else "⭐ Add to watchlist",
            key="wl_toggle", on_click=_toggle_watchlist, args=(symbol,),
        )

        # --- Verdict (rule-based, three time horizons) ------------------
        st.divider()
        st.subheader("Verdict")
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
                # Score as a metric so it carries a "?" tooltip.
                column.metric("Score", f"{hv.score:.0f}/100",
                              help=engine.HELP_TEXTS.get("verdict_score"),
                              label_visibility="collapsed")
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

        # --- Our verdict vs analyst consensus (Step 5) ------------------
        st.divider()
        st.subheader("Our verdict vs Wall Street")
        try:
            analyst = load_analyst(symbol)
        except Exception:
            analyst = None

        if analyst is None or not analyst.found or not analyst.has_coverage:
            st.info("No analyst coverage for this stock (common for smaller and "
                    "Tel-Aviv listings) — nothing to compare against here.")
        else:
            col_ours, col_street = st.columns(2)
            with col_ours:
                st.markdown("**Our verdict (1-year)**")
                hv = (verdict.horizons.get("1Y")
                      if verdict is not None and verdict.found else None)
                if hv is not None and hv.enough_data:
                    c = VERDICT_COLORS.get(hv.label, "gray")
                    st.markdown(f":{c}[**{hv.label}**]")
                    st.metric("Score", f"{hv.score:.0f}/100",
                              help=engine.HELP_TEXTS.get("verdict_score"),
                              label_visibility="collapsed")
                else:
                    st.markdown("n/a")
                st.caption("rule-based, from price + fundamentals")
            with col_street:
                st.markdown("**Analyst consensus**")
                c = ANALYST_COLORS.get(analyst.label, "gray")
                st.markdown(f":{c}[**{analyst.label}**]")
                if analyst.mean is not None:
                    st.metric("Mean rating", f"{analyst.mean:.2f}/5",
                              help=engine.HELP_TEXTS.get("analyst_mean"),
                              label_visibility="collapsed")
                    st.caption(f"from {analyst.num_analysts or '?'} analysts")

            # Mean price target vs current price (implied upside / downside).
            if analyst.target_mean and analyst.current_price:
                cur = analyst.currency or ""
                up = analyst.upside_pct or 0.0
                arrow = "🔺" if up >= 0 else "🔻"
                sign = "+" if up >= 0 else ""
                st.write(
                    f"**Mean price target:** {analyst.target_mean:,.2f} {cur} "
                    f"({arrow} {sign}{up:.1f}% vs current {analyst.current_price:,.2f})"
                )
                if analyst.target_low and analyst.target_high:
                    st.caption(f"analyst range {analyst.target_low:,.2f} - "
                               f"{analyst.target_high:,.2f} {cur}")

            # Recent upgrades / downgrades.
            if analyst.actions:
                with st.expander("Recent analyst actions (upgrades / downgrades)"):
                    for a in analyst.actions:
                        verb = engine.humanize_action(a["action"])
                        if a["from_grade"] and a["from_grade"] != a["to_grade"]:
                            grade = f"{a['from_grade']} -> {a['to_grade']}"
                        else:
                            grade = a["to_grade"] or "-"
                        target = (f" · target {a['price_target']:,.0f}"
                                  if a.get("price_target") else "")
                        st.write(f"- **{a['date']}** {a['firm']}: {verb} "
                                 f"({grade}){target}")

            # ALWAYS say something about how we compare to the analysts.
            divergence = engine.explain_divergence(verdict, analyst, "1Y")
            if not divergence.diverges:
                # Agreement -> a short positive note (never an empty section).
                st.success("✅ " + (divergence.note or
                           "Our verdict is in line with the analyst consensus."))
            else:
                if divergence.direction == "analysts_more_bullish":
                    title = (f"⚖️ Why the gap? Analysts ({divergence.analyst_label}) "
                             f"are more bullish than our 1-year view "
                             f"({divergence.our_label})")
                    drivers_word = "Holding our score back"
                else:
                    title = (f"⚖️ Why the gap? Our 1-year view ({divergence.our_label}) "
                             f"is more bullish than analysts "
                             f"({divergence.analyst_label})")
                    drivers_word = "Lifting our score"
                with st.expander(title, expanded=True):
                    st.write(divergence.note)
                    if divergence.drivers:
                        st.write(f"**{drivers_word}:**")
                        for name, measured, weighted in divergence.drivers:
                            st.write(f"- **{name}** — {measured} ({weighted:+.1f})")

        # Link out — per-site analyst scores are often paywalled.
        st.caption(
            f"More detail on [Yahoo Finance]"
            f"(https://finance.yahoo.com/quote/{symbol}/analysis). "
            "Per-site analyst scores (e.g. TipRanks) are often paywalled."
        )

        # --- Price history chart ----------------------------------------
        st.divider()
        st.subheader("Price history")

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
            if as_percent:
                # % view: a normalised line (start of range = 0%). This is the
                # percent representation for BOTH chart types, so the toggle
                # always does something.
                closes = history.data.set_index("Date")["Close"]
                pct = (closes / closes.iloc[0] - 1) * 100
                pct.name = "% change"
                st.line_chart(pct)
                if chart_type == "Candlestick":
                    st.caption("ℹ️ % view uses the line representation (percent "
                               "change from the start of the range).")
            elif chart_type == "Line":
                # Built-in line chart: closing price over time.
                st.line_chart(history.data.set_index("Date")["Close"])
            else:
                # Candlestick (open/high/low/close) in price terms.
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
        st.subheader("Company analysis")
        st.caption("The business behind the stock. Hover the **?** on any tile "
                   "for a plain-language explanation.")
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
        st.subheader("Stock analysis")
        st.caption("How the share price itself has been behaving.")
        try:
            technicals = load_technicals(symbol)
        except Exception:
            technicals = None
        if technicals is not None and technicals.found and technicals.metrics:
            # Group 1: price levels, trend & momentum.
            st.markdown("**Price levels, trend & momentum**")
            render_metrics(technicals, keys=PRICE_TREND_KEYS)
            # Group 2: volume & buy/sell pressure (kept together and labelled so
            # the OBV / A-D / volume-confirmation tiles are easy to find).
            st.markdown("**Volume & buy/sell pressure (estimates)**")
            render_metrics(technicals, keys=PRESSURE_KEYS)
            st.caption(
                "Note: free data doesn't separate buy-volume from sell-volume, "
                "so **OBV** and **Accumulation/Distribution** are *estimates* of "
                "buying/selling pressure derived from price + volume — not true "
                "order-flow data."
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


# At the very end of the run, save the watchlist to the browser if it changed.
# (Callbacks set "_wl_dirty"; we persist once here so the write happens last.)
if st.session_state.pop("_wl_dirty", False):
    persist_watchlist(ls)
