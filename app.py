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
import pandas as pd
import streamlit as st

import engine  # our pure-Python data engine (no Streamlit inside it)
import gemini_helper  # OPTIONAL Gemini explanation layer (no-op without a key)

# Basic page configuration (title shown in the browser tab, etc.).
st.set_page_config(page_title="Stock Analysis App", page_icon="📈")

# --- Compact metric tiles (PURE STYLING) ---------------------------------
# st.metric has no built-in "small" size, so we shrink the value font (~2rem ->
# 1.25rem, roughly 62%) and the label, and tighten vertical spacing, so the
# Fundamentals/Technicals tile grids read more densely. This only restyles
# st.metric — values, color dots, "?" tooltips and n/a handling are untouched.
st.markdown(
    """
    <style>
    [data-testid="stMetricValue"] { font-size: 1.25rem; line-height: 1.3; }
    [data-testid="stMetricLabel"] { font-size: 0.85rem; }
    [data-testid="stMetricLabel"] p { font-size: 0.85rem; }
    [data-testid="stMetric"] { padding: 0.10rem 0rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


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


@st.cache_data(ttl=600)
def load_holders(symbol):
    """Cached wrapper for the institutional holders (real 13F data)."""
    return engine.get_institutional_holders(symbol)


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


def get_gemini_key():
    """Read the optional Gemini API key from Streamlit secrets. Returns None if
    there's no secrets file or no key set — the AI summary is then skipped
    silently. Wrapped so a totally-missing secrets file can't crash the app.

    STOCKAPP_DISABLE_GEMINI is a test seam (like STOCKAPP_DISABLE_BROWSER_STORAGE):
    when set, we force the no-key path so tests never touch Gemini, even if a real
    secrets.toml exists locally."""
    if os.environ.get("STOCKAPP_DISABLE_GEMINI"):
        return None
    try:
        return st.secrets.get("GEMINI_API_KEY", None)
    except Exception:
        return None


def _gemini_payload(symbol, verdict, company, technicals, analyst, divergence):
    """Shape the already-computed objects into a plain dict for the explainer.
    Every value is a display string already shown on the page; missing values
    are marked 'MISSING' (never guessed). No new numbers are computed here."""
    def disp(group, key):
        metric = group.metrics.get(key) if (group and group.found) else None
        if metric is None or not metric.available:
            return "MISSING"
        return format_metric(metric, group.currency if group else "")

    horizons = {}
    if verdict is not None and verdict.found:
        for horizon in engine.HORIZONS:
            hv = verdict.horizons.get(horizon)
            horizons[horizon] = (f"{hv.label} ({hv.score:.0f}/100)"
                                 if (hv and hv.enough_data) else "MISSING")

    if analyst is not None and analyst.found and analyst.has_coverage:
        analyst_data = {
            "consensus": analyst.label or "MISSING",
            "mean_rating_1to5": (analyst.mean if analyst.mean is not None
                                 else "MISSING"),
            "num_analysts": analyst.num_analysts or "MISSING",
            "mean_price_target": (analyst.target_mean if analyst.target_mean
                                  else "MISSING"),
            "implied_upside_pct": (round(analyst.upside_pct, 1)
                                   if analyst.upside_pct is not None else "MISSING"),
        }
    else:
        analyst_data = "MISSING (no analyst coverage)"

    return {
        "symbol": symbol,
        "verdict_by_horizon": horizons or "MISSING",
        "fundamentals": {
            "sector": disp(company, "sector"),
            "industry": disp(company, "industry"),
            "market_cap": disp(company, "market_cap"),
            "pe_trailing": disp(company, "pe"),
            "forward_pe": disp(company, "forward_pe"),
            "peg": disp(company, "peg"),
            "revenue_ttm": disp(company, "revenue"),
            "earnings_growth_yoy": disp(company, "earnings_growth"),
            "revenue_growth_yoy": disp(company, "revenue_growth"),
            "profit_margin": disp(company, "profit_margin"),
            "dividend_yield": disp(company, "dividend_yield"),
            "debt_to_equity": disp(company, "debt_to_equity"),
            "free_cash_flow": disp(company, "free_cash_flow"),
        },
        "technicals": {
            "rsi": disp(technicals, "rsi"),
            "ma50": disp(technicals, "ma50"),
            "ma200": disp(technicals, "ma200"),
            "macd_trend": disp(technicals, "macd_state"),
            "bollinger_position": disp(technicals, "bb_state"),
            "week52_high": disp(technicals, "week52_high"),
            "week52_low": disp(technicals, "week52_low"),
            "beta": disp(technicals, "beta"),
        },
        "analyst": analyst_data,
        "divergence_note": (divergence.note if (divergence
                            and getattr(divergence, "note", None)) else "MISSING"),
    }


# Stock-analysis tiles are split into two readable groups.
PRICE_TREND_KEYS = ["week52_high", "week52_low", "ma50", "ma200", "rsi", "beta",
                    "macd", "macd_signal", "macd_hist", "macd_state",
                    "bb_upper", "bb_middle", "bb_lower", "bb_state"]
PRESSURE_KEYS = ["avg_volume", "vol_recent", "vol_avg", "vol_move", "vol_confirm",
                 "obv_value", "obv_trend", "ad_value", "ad_trend"]


# Subtle status cue: a small colored dot prefixed to the tile label. Green =
# strength, amber = watch/mixed, red = weakness. Uncolored tiles render as before.
_STATUS_DOT = {"good": "🟢", "neutral": "🟡", "bad": "🔴"}

# Legend + disclaimer shown once per section that has colored tiles.
COLOR_LEGEND = ("🟢 strength · 🟡 watch / mixed · 🔴 weakness — quick rules of "
                "thumb from the numbers already shown, **not financial advice**.")


def render_metrics(group, keys=None, columns_per_row=3, context=None):
    """
    Show a MetricGroup as a tidy grid of st.metric tiles (n/a when missing),
    each with a plain-language "?" help tooltip. Pass `keys` to render only a
    subset (and in that order), e.g. to split a section into sub-groups.

    `context` (sector, current price) drives the color cue: each tile with a
    meaningful good/bad direction gets a small colored dot on its label. Tiles
    without a direction (or missing data) render exactly as before.
    """
    if keys is None:
        items = list(group.metrics.items())
    else:
        items = [(k, group.metrics[k]) for k in keys if k in group.metrics]
    for start in range(0, len(items), columns_per_row):
        row = items[start:start + columns_per_row]
        columns = st.columns(columns_per_row)
        for column, (key, metric) in zip(columns, row):
            status = engine.classify_metric(key, metric.value, context)
            label = metric.label
            help_text = engine.HELP_TEXTS.get(key) or ""
            if status in _STATUS_DOT:
                label = f"{_STATUS_DOT[status]} {metric.label}"
                note = engine.threshold_note(key, context)
                if note:
                    help_text = f"{help_text}  {note}".strip()
            column.metric(label, format_metric(metric, group.currency),
                          help=help_text or None)


def make_candlestick(df, pct_first_close=None):
    """
    Build a simple candlestick chart from a price-history table using Altair.

    Each candle has a thin "wick" (the day's low-to-high range) and a thick
    "body" (open-to-close). Green means the price closed up, red means down.

    When `pct_first_close` is given (the range-start close), a SECONDARY right-
    hand y-axis labelled "% change" is added, linked to the left price axis so
    the range-start close reads 0%. No second line is drawn: because % change is
    a linear function of price, an explicit % domain computed from the same
    price domain maps pixel-for-pixel onto the candles (see engine.first_close /
    engine.price_to_pct_change).
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

    # Left ($) axis. When we add the linked % axis we pin the price domain to the
    # data's Low..High (with a little padding) so the right axis can line up with
    # it exactly; otherwise we let Altair auto-fit as before.
    if pct_first_close:
        low = float(df["Low"].min())
        high = float(df["High"].max())
        pad = (high - low) * 0.05 or 1.0  # small breathing room, like the default
        low, high = low - pad, high + pad
        price_scale = alt.Scale(zero=False, nice=False, domain=[low, high])
    else:
        price_scale = alt.Scale(zero=False)

    # The wick: a vertical line from Low to High.
    wicks = base.mark_rule().encode(
        y=alt.Y("Low:Q", title="Price", scale=price_scale),
        y2="High:Q",
    )
    # The body: a bar from Open to Close.
    bodies = base.mark_bar().encode(
        y="Open:Q",
        y2="Close:Q",
    )
    candles = wicks + bodies

    if not pct_first_close:
        return candles

    # A faint dashed baseline at the 0% level. In price terms 0% is exactly the
    # range-start close (pct_first_close), so we draw it on the SAME left price
    # scale as the candles — that keeps it aligned with them pixel-for-pixel and
    # marks where the right "% change" axis reads 0. Kept subtle so it never
    # competes with the candles.
    baseline = (
        alt.Chart(pd.DataFrame({"y": [pct_first_close]}))
        .mark_rule(strokeDash=[4, 4], color="#9e9e9e", opacity=0.7, size=1)
        .encode(y=alt.Y("y:Q", scale=price_scale, axis=None))
    )

    # Secondary right-hand "% change" axis, linked to the left price axis. We map
    # the SAME price domain through engine.price_to_pct_change so the two scales
    # share endpoints; an invisible mark just carries the axis (no extra line).
    pct_low = engine.price_to_pct_change(low, pct_first_close)
    pct_high = engine.price_to_pct_change(high, pct_first_close)
    pct_axis = (
        alt.Chart(df)
        .mark_point(opacity=0)  # invisible: we only want its right-hand axis
        .transform_calculate(pct=f"(datum.Close / {pct_first_close} - 1) * 100")
        .encode(
            x=alt.X("Date:T", title=None),
            y=alt.Y(
                "pct:Q",
                title="% change",
                scale=alt.Scale(zero=False, nice=False, domain=[pct_low, pct_high]),
                axis=alt.Axis(orient="right", format="+.1f"),
            ),
        )
    )
    # Independent y scales let the price layer and the % layer keep their own
    # domains while sharing the exact same plotting area, so they stay aligned.
    return alt.layer(candles, baseline, pct_axis).resolve_scale(y="independent")


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
            # "~s" gives compact SI tick labels (50,000,000 -> "50M").
            y=alt.Y("Volume:Q", title="Volume", axis=alt.Axis(format="~s")),
        )
        .properties(height=200)  # taller than before so bars aren't squished,
        # but still clearly secondary to the price chart (Altair default ~300px)
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
        # ---- Pinned header (stays visible above the tabs) --------------
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

        # ---- Load everything once (cached); each tab renders its slice. -
        # PURE LAYOUT: these are the same cached calls as before, just hoisted
        # so every tab (and the debug panel) can read the data it needs. No
        # computation changes — Streamlit runs all tab bodies each rerun anyway.
        try:
            verdict = load_verdict(symbol)
        except Exception:
            verdict = None
        try:
            analyst = load_analyst(symbol)
        except Exception:
            analyst = None
        try:
            holders = load_holders(symbol)
        except Exception:
            holders = None
        try:
            company = load_company(symbol)
        except Exception:
            company = None
        try:
            technicals = load_technicals(symbol)
        except Exception:
            technicals = None
        # Context for the color cues: the stock's sector (for sector-aware
        # valuation thresholds) and its current price (for price-vs-MA).
        sector = None
        if company is not None and company.found:
            sm = company.metrics.get("sector")
            sector = sm.value if (sm and sm.available) else None
        metric_context = {"sector": sector, "price": quote.price}

        tab_overview, tab_ai, tab_charts, tab_fund, tab_tech, tab_own = st.tabs(
            ["Overview", "AI analysis", "Charts", "Fundamentals", "Technicals",
             "Ownership"])

        # ======================= Overview =======================
        with tab_overview:
            with st.expander("Verdict", expanded=True):
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

                    # Transparent per-horizon breakdown. Rendered inline (Streamlit
                    # forbids an expander inside an expander), one tab per horizon.
                    st.markdown("**Why this verdict? (how it was calculated)**")
                    horizon_tabs = st.tabs([HORIZON_NAMES.get(h, h)
                                            for h in engine.HORIZONS])
                    for htab, horizon in zip(horizon_tabs, engine.HORIZONS):
                        hv = verdict.horizons.get(horizon)
                        with htab:
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

            with st.expander("Our verdict vs Wall Street", expanded=True):
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

                    # (Recent analyst actions now live in the Ownership tab.)

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
                        # Inline (an expander can't nest inside this one).
                        st.markdown(f"**{title}**")
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

        # ======================= AI analysis =======================
        with tab_ai:
            # OPTIONAL; Gemini, button-triggered. We NEVER auto-call Gemini — the
            # user clicks a button. Each (symbol, depth) result is cached in
            # session_state so re-clicks / reruns don't re-call (protecting
            # free-tier quota). With no key the buttons are disabled (tooltip) and
            # nothing else appears; any failure renders a muted note, never raises.
            st.markdown("**🤖 AI analysis**")
            gemini_key = get_gemini_key()
            no_key = not gemini_key
            ai_cache = st.session_state.setdefault("_gemini_cache", {})
            ai_active = st.session_state.setdefault("_gemini_active_depth", {})

            disabled_help = "Add a Gemini API key to enable" if no_key else None
            col_quick, col_deep = st.columns(2)
            if col_quick.button("⚡ Quick take", key="ai_quick_btn", disabled=no_key,
                                help=disabled_help, width="stretch"):
                ai_active[symbol] = "quick"
            if col_deep.button("🔍 Deep dive", key="ai_deep_btn", disabled=no_key,
                               help=disabled_help, width="stretch"):
                ai_active[symbol] = "deep"

            # Show the most-recently-requested depth for this symbol (if any). The
            # result is generated once, then served from the per-(symbol, depth) cache.
            depth = ai_active.get(symbol)
            if depth:
                cache_key = (symbol, depth)
                if cache_key not in ai_cache:
                    # Build the payload from data already on the page, then call once.
                    divergence_for_ai = None
                    try:
                        if (verdict is not None and analyst is not None
                                and analyst.found and analyst.has_coverage):
                            divergence_for_ai = engine.explain_divergence(
                                verdict, analyst, "1Y")
                    except Exception:
                        divergence_for_ai = None
                    try:
                        company_ai = load_company(symbol)
                    except Exception:
                        company_ai = None
                    try:
                        technicals_ai = load_technicals(symbol)
                    except Exception:
                        technicals_ai = None
                    payload = _gemini_payload(symbol, verdict, company_ai,
                                              technicals_ai, analyst, divergence_for_ai)
                    ai_cache[cache_key] = gemini_helper.explain_verdict(
                        payload, gemini_key, depth=depth)

                explanation = ai_cache[cache_key]
                if explanation:
                    st.info(explanation)
                    st.caption("AI-generated summary of this stock's data "
                               "(shown across the other tabs) — not financial advice.")
                else:
                    # Only AFTER a click: a small muted note. Never auto, never crash.
                    st.caption("_AI analysis unavailable._")

        # ======================= Charts =======================
        with tab_charts:
            with st.expander("Price history", expanded=True):
                # Range selector as a row of buttons (segmented control). The keys
                # come straight from the engine so the two never drift apart.
                range_key = st.segmented_control(
                    "Range",
                    options=list(engine.RANGES.keys()),
                    default="1M",
                    label_visibility="collapsed",
                )
                # If the user clicks the active button it deselects (returns None);
                # fall back to the default so a chart is always shown.
                range_key = range_key or "1M"

                # Toggle between a clean line of closing prices and candlesticks.
                chart_type = st.radio(
                    "Chart type",
                    options=["Line", "Candlestick"],
                    horizontal=True,
                )

                # Y-axis toggle: actual Price (default) vs % change from the start
                # of the selected range (normalised so the first point = 0%).
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
                    if chart_type == "Candlestick":
                        # Candles always plot price ($) on the LEFT axis. With % ON
                        # we keep the candles and add a linked RIGHT "% change" axis
                        # instead of swapping in a line (for one stock the % line
                        # would just trace the candles — see price_to_pct_change).
                        base_close = engine.first_close(history.data) if as_percent else None
                        st.altair_chart(
                            make_candlestick(history.data, pct_first_close=base_close),
                            width="stretch",
                        )
                        if base_close:
                            st.caption("ℹ️ Candles show price ($, left axis); the right "
                                       "axis reads cumulative % change from the start of "
                                       "the selected range.")
                    elif as_percent:
                        # Line + % view: a normalised line (start of range = 0%).
                        closes = history.data.set_index("Date")["Close"]
                        pct = engine.price_to_pct_change(closes, engine.first_close(history.data))
                        pct.name = "% change"
                        st.line_chart(pct)
                    else:
                        # Built-in line chart: closing price over time.
                        st.line_chart(history.data.set_index("Date")["Close"])

            with st.expander("Volume", expanded=False):
                # Same logic as before: only when there's real volume for this range.
                has_volume = False
                if history.found and "Volume" in history.data.columns:
                    volumes = history.data["Volume"].dropna()
                    has_volume = len(volumes) > 0 and float(volumes.sum()) > 0
                if has_volume:
                    st.altair_chart(make_volume_chart(history.data), width="stretch")
                else:
                    st.caption("_No volume data for this range._")

        # ======================= Fundamentals =======================
        with tab_fund:
            with st.expander("Company analysis", expanded=True):
                st.caption("The business behind the stock. Hover the **?** on any "
                           "tile for a plain-language explanation.")
                if company is not None and company.found and company.metrics:
                    st.caption(COLOR_LEGEND)
                    render_metrics(company, context=metric_context)
                else:
                    st.info("No company metrics available for this ticker.")

        # ======================= Technicals =======================
        with tab_tech:
            st.caption("How the share price itself has been behaving.")
            if technicals is not None and technicals.found and technicals.metrics:
                with st.expander("Price levels, trend & momentum", expanded=True):
                    st.caption(COLOR_LEGEND)
                    render_metrics(technicals, keys=PRICE_TREND_KEYS,
                                   context=metric_context)
                with st.expander("Volume & buy/sell pressure (estimates)",
                                 expanded=False):
                    render_metrics(technicals, keys=PRESSURE_KEYS,
                                   context=metric_context)
                    st.caption(
                        "Note: free data doesn't separate buy-volume from sell-volume, "
                        "so **OBV** and **Accumulation/Distribution** are *estimates* of "
                        "buying/selling pressure derived from price + volume — not true "
                        "order-flow data."
                    )
            else:
                st.info("No stock metrics available for this ticker.")

        # ======================= Ownership =======================
        with tab_own:
            with st.expander("Notable institutional holders", expanded=True):
                if holders is None or not holders.found:
                    st.caption("_No institutional holder data available for this stock._")
                else:
                    if holders.institutions_pct_held is not None:
                        pct = holders.institutions_pct_held * 100
                        across = (f" across {holders.institutions_count:,} institutions"
                                  if holders.institutions_count else "")
                        st.markdown(f"**{pct:.1f}%** of shares held by institutions{across}.")

                    if holders.top_holders:
                        table = pd.DataFrame([{
                            "Holder": h.name,
                            "Shares": f"{h.shares:,}" if h.shares is not None else "n/a",
                            "% held": (f"{h.pct_held * 100:.2f}%"
                                       if h.pct_held is not None else "n/a"),
                            "Date reported": h.date_reported or "n/a",
                        } for h in holders.top_holders])
                        st.dataframe(table, hide_index=True, width="stretch")
                        st.caption(
                            "Top institutional holders from quarterly **13F filings** "
                            "(lagged up to ~45 days), mostly large asset managers (e.g. "
                            "Vanguard, BlackRock). This is **not** a real-time or "
                            "famous-investor view, and **not financial advice**."
                        )
                    else:
                        # Summary % available but no named-holder breakdown (common
                        # for smaller / foreign listings).
                        st.caption(
                            "Institutional ownership summary from quarterly **13F "
                            "filings** (lagged up to ~45 days); a named holder breakdown "
                            "isn't available for this stock. **Not financial advice**."
                        )

            with st.expander("Recent analyst actions (upgrades / downgrades)",
                             expanded=False):
                if (analyst is not None and analyst.found
                        and getattr(analyst, "actions", None)):
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
                else:
                    st.caption("_No recent analyst actions available._")

        # Debug panel stays below the tabs (collapsed). Open it to see WHICH
        # source gave us the price and every raw value we tried.
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
