"""
Stock Analysis App — Streamlit UI (display layer only).

All the data logic lives in engine.py (pure Python). This file just:
  1. draws the page,
  2. asks the engine for a quote,
  3. shows the result (or a friendly "couldn't find" message).

Run locally with:
    streamlit run app.py
"""

import html
import hmac
import json
import os
from datetime import date, datetime

import altair as alt  # charting library that ships with Streamlit
import pandas as pd
import streamlit as st

import engine  # our pure-Python data engine (no Streamlit inside it)
import gemini_helper  # OPTIONAL Gemini explanation layer (no-op without a key)
import famous_investors  # famous-investor 13F holdings from SEC EDGAR (no key)
import data_store  # where our own data lives (local folder now, private repo later)
import portfolio  # the user's trades -> holdings, P&L, tax, totals in ₪ (pure Python)
import buy_score  # 50% engine + 30% analysts + 20% AI news -> -100..+100
import news  # latest headlines from free sources (Yahoo, Google News RSS)
import ai_insights  # numbers behind the AI overview / picks (pure Python)
import benchmark  # portfolio vs TA-125 / S&P 500 with the same money on the same days

# Basic page configuration (title shown in the browser tab, etc.).
st.set_page_config(page_title="המניות שלי", page_icon="📈",
                   initial_sidebar_state="collapsed")

# --- Look & feel (PURE STYLING) -------------------------------------------
# The colors live in .streamlit/config.toml (dark theme). This CSS adds what
# the theme can't: Hebrew right-to-left text, the Rubik/Assistant fonts,
# compact metric tiles, and a few small "cards" (header, verdict) drawn as HTML.
# Charts and tables stay left-to-right so their axes read normally.
APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;700&family=Assistant:wght@400;600;700&display=swap');
:root {
  --sa-surface: #151D2B; --sa-surface2: #1D2737; --sa-line: #283347;
  --sa-fg: #E7ECF4; --sa-muted: #8B97AA; --sa-accent: #8EA2FF;
  --sa-up: #3CC98A; --sa-down: #FF6B6B; --sa-hold: #F2B04C;
}
.stApp, .stApp p, .stApp label, .stApp input, .stApp button, .stApp li { font-family: "Assistant", "Rubik", system-ui, sans-serif; }
/* Keep Streamlit's icon font (expander arrows etc.) untouched. */
[data-testid="stIconMaterial"], .material-symbols-rounded { font-family: "Material Symbols Rounded" !important; }
h1, h2, h3, h4 { font-family: "Rubik", "Assistant", system-ui, sans-serif !important; letter-spacing: -0.01em; }
.block-container { padding-top: 2.2rem; padding-bottom: 4rem; max-width: 760px; }

/* Hebrew: right-to-left for the page text and widgets... */
[data-testid="stMainBlockContainer"] { direction: rtl; text-align: right; }
[data-testid="stMarkdownContainer"] { text-align: right; }
[data-testid="stExpander"] summary { flex-direction: row; }
/* ...but charts, tables and numbers inside inputs stay left-to-right. */
[data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"],
[data-testid="stDataFrame"], [data-testid="stTable"], .vega-embed { direction: ltr; text-align: left; }
/* Range buttons (1D ... 5Y) read left-to-right like on every chart app. */
[data-testid="stButtonGroup"] { direction: ltr; }
/* ...but the Hebrew choices (home tabs, index picker, trade side) read right-to-left. */
.st-key-home_tab [data-testid="stButtonGroup"], .st-key-bench_index [data-testid="stButtonGroup"],
[class*="st-key-trade_side_"] [data-testid="stButtonGroup"] { direction: rtl; }
.sa-brand { font-family: Rubik, sans-serif; font-weight: 700; font-size: 1.6rem; margin: 0 0 0.6rem; }
[data-testid="stCode"], [data-testid="stCode"] pre { direction: ltr; text-align: left; }
.sa-num { direction: ltr; unicode-bidi: isolate; display: inline-block; font-variant-numeric: tabular-nums; }

/* No hover link icons next to headings. */
[data-testid="stHeaderActionElements"] { display: none; }
/* We don't use the sidebar any more: hide its open/close arrow. */
[data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] { display: none; }

/* Compact metric tiles (smaller value text, tighter spacing). */
[data-testid="stMetricValue"] { font-size: 1.2rem; line-height: 1.3; font-variant-numeric: tabular-nums; }
/* Numbers read left-to-right so "-1.90%" keeps its minus in front. */
[data-testid="stMetricValue"] > div { direction: ltr; unicode-bidi: isolate; text-align: right; }
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p { font-size: 0.85rem; }
[data-testid="stMetric"] { padding: 0.15rem 0; }

/* Key-metric grid: keep 2 tiles per row even on a phone (Streamlit would
   otherwise stack every column on its own line). */
.st-key-kpis [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem; }
.st-key-kpis [data-testid="stColumn"] { min-width: 0 !important; width: 50% !important; flex: 1 1 0 !important;
  background: var(--sa-surface2); border-radius: 14px; padding: 0.45rem 0.7rem; }

/* Watchlist rows on the home screen: full-width buttons that read as list rows. */
.st-key-watchlist button, .st-key-portfolio_rows button, [class*="st-key-picks_"] button { justify-content: flex-start; text-align: right; border-radius: 14px;
  background: var(--sa-surface); border: 1px solid var(--sa-line); padding: 0.7rem 0.9rem; }
.st-key-watchlist button p, .st-key-portfolio_rows button p, [class*="st-key-picks_"] button p { font-size: 1rem; white-space: pre-line; line-height: 1.45; }
.sa-warn { color: var(--sa-hold); font-size: 0.8rem; margin-top: 0.5rem; }

/* Small HTML cards. */
.sa-card { background: var(--sa-surface); border: 1px solid var(--sa-line); border-radius: 20px;
  padding: 1rem 1.1rem; margin: 0.4rem 0 0.8rem; }
.sa-eyebrow { color: var(--sa-muted); font-size: 0.82rem; }
.sa-name { font-family: Rubik, sans-serif; font-weight: 700; font-size: 1.55rem; line-height: 1.2; }
.sa-sub { color: var(--sa-muted); font-size: 0.85rem; }
.sa-price { font-family: Rubik, sans-serif; font-weight: 600; font-size: 2.1rem; line-height: 1.15; }
.sa-row { display: flex; gap: 0.6rem; align-items: baseline; flex-wrap: wrap; }
.sa-chip { display: inline-block; font-weight: 700; font-size: 0.85rem; padding: 0.12rem 0.6rem;
  border-radius: 999px; font-variant-numeric: tabular-nums; direction: ltr; unicode-bidi: isolate; }
.sa-up { color: var(--sa-up); background: rgba(60,201,138,.14); }
.sa-down { color: var(--sa-down); background: rgba(255,107,107,.14); }
.sa-hold { color: var(--sa-hold); background: rgba(242,176,76,.15); }
.sa-flat { color: var(--sa-muted); background: var(--sa-surface2); }
.sa-verdict { display: flex; justify-content: space-between; align-items: flex-start; gap: 0.8rem; }
.sa-word { font-family: Rubik, sans-serif; font-weight: 700; font-size: 1.9rem; line-height: 1.1; }
.sa-score { text-align: center; min-width: 4.5rem; }
.sa-score b { font-size: 1.3rem; font-variant-numeric: tabular-nums; }
.sa-bar { height: 6px; border-radius: 99px; background: var(--sa-surface2); margin-top: 0.3rem; overflow: hidden; }
.sa-bar i { display: block; height: 100%; border-radius: 99px; }
.sa-why { margin: 0.7rem 0 0.6rem; font-size: 0.95rem; }
.sa-horiz { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.4rem; }
.sa-horiz div { background: var(--sa-surface2); border-radius: 12px; padding: 0.45rem; text-align: center; }
.sa-horiz b { display: block; font-size: 0.95rem; } .sa-horiz span { font-size: 0.75rem; color: var(--sa-muted); }
.sa-fine { color: var(--sa-muted); font-size: 0.76rem; margin-top: 0.6rem; }
</style>
"""
st.markdown(APP_CSS, unsafe_allow_html=True)



def _secret(name):
    """Read an optional secret; None if missing (or no secrets file at all)."""
    try:
        return st.secrets.get(name, None)
    except Exception:
        return None


def get_app_password():
    """The app password, or None when none is configured (then there's no lock).

    STOCKAPP_DISABLE_PASSWORD is a test seam (like STOCKAPP_DISABLE_GEMINI):
    when set, the lock is skipped even if a password exists locally."""
    if os.environ.get("STOCKAPP_DISABLE_PASSWORD"):
        return None
    return _secret("APP_PASSWORD")


def get_data_store_config():
    """(repo, token) of the PRIVATE data repo, or (None, None) when not set up
    yet — data_store then keeps everything in the local_data folder."""
    return _secret("DATA_REPO"), _secret("DATA_REPO_TOKEN")


def password_ok(entered, expected):
    """Constant-time comparison, so the check doesn't leak timing hints."""
    if not expected or entered is None:
        return False
    return hmac.compare_digest(str(entered).encode("utf-8"), str(expected).encode("utf-8"))


def require_password():
    """Lock the whole app behind APP_PASSWORD (only if one is configured).
    Success is remembered for this browser session."""
    expected = get_app_password()
    if not expected or st.session_state.get("auth_ok"):
        return
    st.markdown("### 🔒 כניסה")
    entered = st.text_input("סיסמה", type="password", key="app_password_input")
    if entered:
        if password_ok(entered, expected):
            st.session_state["auth_ok"] = True
            st.session_state.pop("app_password_input", None)
            st.rerun()
        else:
            st.error("הסיסמה לא נכונה. נסה שוב.")
    st.stop()


require_password()



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
    "6M": "חצי שנה",
    "1Y": "שנה",
    "5Y": "5 שנים",
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
    show as a dash ("—"). `currency` (e.g. "USD") is appended to money values.
    """
    if not metric.available:
        return "—"

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


# SEC requires a fair-use identity (name + email) on every request — this is NOT
# a secret or login, so a sensible default in code is fine; secrets can override.
_DEFAULT_EDGAR_IDENTITY = "Stock App contact@example.com"


def get_edgar_identity():
    """The SEC EDGAR fair-use identity string. Reads st.secrets["EDGAR_IDENTITY"]
    if set, else a sensible default — works even with no secrets file."""
    try:
        return st.secrets.get("EDGAR_IDENTITY", _DEFAULT_EDGAR_IDENTITY)
    except Exception:
        return _DEFAULT_EDGAR_IDENTITY


def _famous_change_str(holder):
    """Compact 'vs last filing' cell for a famous-holder row: an arrow + the
    change in portfolio weight (percentage points), or NEW for a fresh position."""
    direction = holder.get("direction")
    pct = holder.get("pct_of_portfolio")
    prev = holder.get("prev_pct")
    if direction == "new" or prev is None:
        return "NEW"
    if pct is None:
        return "—"
    arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(direction, "→")
    return f"{arrow} {pct - prev:+.1f}pp"


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


def render_metrics(group, keys=None, columns_per_row=3, context=None, labels=None):
    """
    Show a MetricGroup as a tidy grid of st.metric tiles ("—" when missing),
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
            label = (labels or {}).get(key, metric.label)
            help_text = engine.HELP_TEXTS.get(key) or ""
            if status in _STATUS_DOT:
                label = f"{_STATUS_DOT[status]} {label}"
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
    """A row tap pre-fills the search box on the next rerun -- with an EXACT
    symbol, so the page opens that stock instead of asking 'which one?'."""
    st.session_state["pending_query"] = symbol
    st.session_state["exact_symbol"] = symbol


# --- Portfolio: the trades, kept with data_store --------------------------
#
# What's saved is the list of buys and sells, in data/portfolio.json via
# data_store: the local_data folder today, the PRIVATE GitHub repo once
# DATA_REPO + DATA_REPO_TOKEN are set. The holdings, profit and tax are
# worked out from the trades in portfolio.py; this part only draws them.
#
# The first version kept "qty + average price" in the browser (Safari wipes
# that after 7 days). migrate_browser_portfolio() moves it into trades once.

PORTFOLIO_KEY = "portfolio"  # the old browser key (read once, then cleared)
SIDE_HE = {"buy": "קנייה", "sell": "מכירה"}


def html_safe(text):
    """Two '$' on a line turn into a math formula in Streamlit markdown:
    use the HTML entity in our HTML snippets."""
    return str(text).replace("$", "&#36;")


def md_safe(text):
    """Same for plain markdown (button labels, captions)."""
    return str(text).replace("$", "\\$")


def _store_kwargs():
    repo, token = get_data_store_config()
    # STOCKAPP_DATA_DIR is a test seam: tests keep their data in a temp folder.
    return {"repo": repo, "token": token,
            "local_dir": os.environ.get("STOCKAPP_DATA_DIR") or None}


def storage_is_local():
    repo, token = get_data_store_config()
    return data_store.backend_name(repo, token) == "local"


# Where the trades live:
#   "repo"    -> the PRIVATE GitHub data repo (once DATA_REPO + token are set)
#   "dir"     -> a folder on the server (STOCKAPP_DATA_DIR; used by the tests)
#   "browser" -> this device's browser (the default until the repo is set up).
# Why not a server folder by default: on Streamlit Cloud that folder is SHARED
# by everyone who opens the public link, and it is wiped on every redeploy.
TRADES_KEY = "portfolio_trades"   # browser key for the trades document
_LS = None                        # the browser storage handle (set below)


def portfolio_backend():
    if not storage_is_local():
        return "repo"
    if os.environ.get("STOCKAPP_DATA_DIR"):
        return "dir"
    return "browser"


def load_portfolio():
    """The trades, read once per visit. On a read error we show it and keep
    the saved copy untouched (nothing is written until a read works)."""
    if "portfolio" not in st.session_state:
        if portfolio_backend() == "browser":
            raw = _LS.getItem(TRADES_KEY) if _LS is not None else None
            if raw is None:
                # Nothing saved yet -- or the browser component hasn't answered
                # yet (it does on the next rerun), so don't cache the empty one.
                if _LS is None:
                    st.session_state["portfolio"] = portfolio.empty()
                return st.session_state.get("portfolio", portfolio.empty())
            try:
                obj = json.loads(raw) if isinstance(raw, str) else raw
                st.session_state["portfolio"] = portfolio.clean_data(obj)
                st.session_state.pop("portfolio_error", None)
            except (ValueError, portfolio.PortfolioError) as exc:
                st.session_state["portfolio_error"] = str(exc)
                return portfolio.empty()
            return st.session_state["portfolio"]
        try:
            st.session_state["portfolio"] = data_store.read_json(
                portfolio.STORE_PATH, portfolio.empty(), **_store_kwargs())
            st.session_state.pop("portfolio_error", None)
        except data_store.DataStoreError as exc:
            st.session_state["portfolio_error"] = str(exc)
            return portfolio.empty()
    return st.session_state["portfolio"]


def update_portfolio(change, message="Update portfolio"):
    """Read the LATEST saved copy, apply change(data) -> new data, save it.
    Reading first means trades saved from another device are never
    overwritten. Raises PortfolioError / DataStoreError for the caller."""
    if portfolio_backend() == "browser":
        if st.session_state.get("portfolio_error"):
            raise data_store.DataStoreError("the saved portfolio couldn't be read")
        new = change(load_portfolio())
        st.session_state["portfolio"] = new
        st.session_state["_trades_dirty"] = True   # written at the end of the run
        return new
    kw = _store_kwargs()
    current = data_store.read_json(portfolio.STORE_PATH, portfolio.empty(), **kw)
    new = change(current)
    data_store.write_json(portfolio.STORE_PATH, new, message, **kw)
    st.session_state["portfolio"] = new
    st.session_state.pop("portfolio_error", None)
    return new


def persist_trades(ls):
    """Browser backend: write the trades document (end of the run)."""
    if ls is None or "portfolio" not in st.session_state:
        return
    try:
        ls.setItem(TRADES_KEY, json.dumps(st.session_state["portfolio"], ensure_ascii=False),
                   key="trades_persist")
    except Exception:
        pass


def migrate_browser_portfolio(ls):
    """Once: the old browser portfolio (qty + average price) -> opening
    trades dated today. The browser copy is cleared only after the save works.
    `_test_browser_portfolio` in session_state stands in for the browser in tests."""
    if st.session_state.get("_pf_migrated"):
        return
    raw = (ls.getItem(PORTFOLIO_KEY) if ls is not None
           else st.session_state.pop("_test_browser_portfolio", None))
    if raw is None:
        return  # nothing stored, or the browser component hasn't loaded yet
    st.session_state["_pf_migrated"] = True
    old = portfolio.parse(raw)
    if not old:
        return
    currencies = {}
    for h in old:
        try:
            q = load_quote(h["symbol"])
            currencies[h["symbol"]] = portfolio.to_major(q.price, q.currency)[1] if q.found else ""
        except Exception:
            currencies[h["symbol"]] = ""
    try:
        update_portfolio(lambda d: portfolio.from_holdings(d, old, currencies),
                         "Move portfolio from the browser")
    except (portfolio.PortfolioError, data_store.DataStoreError):
        st.session_state["_pf_migrated"] = False  # keep the browser copy; retry next visit
        return
    st.session_state["_pf_moved"] = len(old)
    if ls is not None:
        if portfolio_backend() == "browser":
            persist_trades(ls)  # save the trades BEFORE clearing the old key
        try:
            ls.setItem(PORTFOLIO_KEY, "[]", key="portfolio_clear")
        except Exception:
            pass


@st.cache_data(ttl=600)
def load_fx(currency):
    """Today's rate from `currency` to ₪ (1.0 for ₪), or None if unavailable."""
    sym = portfolio.fx_symbol(currency)
    if sym is None:
        return 1.0
    try:
        q = engine.get_stock_quote(sym)
        return q.price if (q.found and q.price) else None
    except Exception:
        return None


def portfolio_rows(holdings):
    """Value every holding with live quotes + FX. Returns (rows, names)."""
    rows, names = [], {}
    for h in holdings:
        price = currency = change = None
        try:
            q = load_quote(h["symbol"])
            if q and q.found:
                price, currency = portfolio.to_major(q.price, q.currency)
                change = q.change_pct
                names[h["symbol"]] = short_name(q.name, h["symbol"])
        except Exception:
            pass
        fx = load_fx(currency) if currency else None
        rows.append(portfolio.position(h, price, currency or "", change, fx))
    return rows, names


def portfolio_card_html(totals):
    """The 'My portfolio' summary card (total in ₪, today, total P&L)."""
    warn = ('<div class="sa-warn">⚠️ התיק נשמר רק בדפדפן במכשיר הזה. עד שנחבר '
            'את המאגר הפרטי, כדאי לשמור גיבוי (למטה, ב״גיבוי ושחזור״).</div>'
            if storage_is_local() else "")
    if totals["value"] is None:
        return ('<div class="sa-card"><div class="sa-eyebrow">שווי התיק</div>'
                f'<div class="sa-sub">אין מחירים זמינים כרגע.</div>{warn}</div>')
    day = (chip_html(totals["day_pct"], "%") + ' <span class="sa-sub">היום</span>'
           if totals["day_pct"] is not None else "")
    pnl = totals["pnl"]
    pnl_txt = (f'<span class="sa-sub">סה״כ רווח/הפסד: <span class="sa-num">'
               f'{_esc(portfolio.money(pnl, "ILS", 0, signed=True))}</span></span> '
               + chip_html(totals["pnl_pct"], "%")) if pnl is not None else ""
    missing = (f'<div class="sa-fine">בלי מחיר כרגע: {_esc(", ".join(totals["missing"]))}</div>'
               if totals["missing"] else "")
    return html_safe(
        '<div class="sa-card"><div class="sa-eyebrow">שווי התיק</div>'
        f'<div class="sa-row"><span class="sa-price sa-num">'
        f'{_esc(portfolio.money(totals["value"], "ILS", 0))}</span>{day}</div>'
        f'<div>{pnl_txt}</div>{missing}'
        '<div class="sa-fine">מניות במטבע זר מחושבות בשקלים לפי השער של היום. '
        f'עלות הקנייה מחושבת גם היא לפי השער של היום.</div>{warn}</div>')


def render_trade_box(symbol, quote):
    """Stock page: what I hold in this stock + a form to log a buy or sell."""
    data = load_portfolio()
    price, currency = portfolio.to_major(quote.price, quote.currency)
    try:
        held = portfolio.compute(data).holdings.get(symbol)
    except portfolio.PortfolioError:
        held = None
    if held is not None:
        row = portfolio.position({"symbol": symbol, "qty": held.qty, "cost": held.avg_cost},
                                 price, currency, quote.change_pct)
        st.markdown(html_safe(
            '<div class="sa-card"><div class="sa-row" style="justify-content:space-between">'
            f'<span class="sa-eyebrow">בתיק שלי</span>{chip_html(row["pnl_pct"], "%")}</div>'
            '<div class="sa-horiz" style="margin-top:0.5rem">'
            f'<div><b class="sa-num">{held.qty:,.4g}</b><span>מניות</span></div>'
            f'<div><b class="sa-num">{_esc(portfolio.money(held.avg_cost, held.currency or currency))}</b>'
            '<span>מחיר קנייה ממוצע</span></div>'
            f'<div><b class="sa-num">{_esc(portfolio.money(row["value"], currency, 0))}</b>'
            '<span>שווי היום</span></div></div></div>'), unsafe_allow_html=True)

    done = st.session_state.pop("_trade_saved", None)
    if done and done[0] == symbol:
        st.success(done[1])
    title = "📝 רישום קנייה / מכירה" if held is not None else "+ הוספה לתיק (רישום קנייה)"
    with st.expander(title, expanded=False):
        if st.session_state.get("portfolio_error"):
            st.error("התיק השמור לא נקרא, אז אי אפשר לשמור עסקה כרגע. נסה לרענן.")
            return
        # A stock that's already in the portfolio keeps the currency its
        # trades were typed in.
        currency = portfolio.currency_of(data, symbol) or currency
        unit = portfolio.CURRENCY_SYMBOLS.get(currency, currency)
        with st.form(f"trade_form_{symbol}", clear_on_submit=True, border=False):
            side_he = st.segmented_control("סוג העסקה", ["קנייה", "מכירה"], default="קנייה",
                                           key=f"trade_side_{symbol}")
            col_qty, col_price = st.columns(2)
            qty = col_qty.number_input("כמות מניות", min_value=0.0, value=None, step=1.0,
                                       format="%g", key=f"trade_qty_{symbol}")
            px = col_price.number_input(f"מחיר למניה ({unit})", min_value=0.0,
                                        value=round(price, 2) if price else None,
                                        step=0.01, format="%.2f", key=f"trade_price_{symbol}")
            when = st.date_input("תאריך העסקה", value=date.today(), max_value=date.today(),
                                 format="DD/MM/YYYY", key=f"trade_date_{symbol}")
            submitted = st.form_submit_button("שמור עסקה", type="primary", width="stretch")
        st.caption((f"יש לך עכשיו {held.qty:,.4g} מניות. " if held is not None else "")
                   + "המחיר מולא לפי המחיר הנוכחי, אפשר לשנות.")
        if submitted:
            side = "sell" if side_he == "מכירה" else "buy"
            try:
                update_portfolio(lambda d: portfolio.add_transaction(
                    d, symbol, side, qty, px, currency, when), f"{side} {symbol}")
            except portfolio.PortfolioError as exc:
                st.error(str(exc))
            except data_store.DataStoreError:
                st.error("השמירה נכשלה (בעיה באחסון). העסקה לא נשמרה, נסה שוב.")
            else:
                st.session_state["_trade_saved"] = (
                    symbol, f"נשמר: {SIDE_HE[side]} של {qty:g} מניות {symbol}.")
                st.rerun()


def _tx_label(t):
    return (f"{t['date']} · {SIDE_HE.get(t['side'], t['side'])} · {t['symbol']} · "
            f"{t['qty']:g} × {portfolio.money(t['price'], t.get('currency'))}")


LAST_BACKUP_KEY = "last_backup"


def init_last_backup(ls):
    """When a backup was last saved on this device (ISO text) or None."""
    if ls is not None and not st.session_state.get("_bk_loaded"):
        raw = ls.getItem(LAST_BACKUP_KEY)
        if raw is not None:
            st.session_state["last_backup"] = str(raw).strip('"') or None
            st.session_state["_bk_loaded"] = True
    st.session_state.setdefault("last_backup", None)


def _mark_backup():
    """Download / 'I copied it' / restore: remember the backup time."""
    st.session_state["last_backup"] = datetime.now().isoformat(timespec="seconds")
    st.session_state["_bk_dirty"] = True


def render_trades_and_backup():
    """Home, bottom: the last year's trades + tax estimate, and backup/restore."""
    data = load_portfolio()
    txs = data.get("transactions", [])
    with st.expander("🧾 עסקאות ומס (שנה אחרונה)", expanded=False):
        recent = portfolio.last_year(txs)
        if not recent:
            st.caption("עוד אין עסקאות מהשנה האחרונה.")
        else:
            table = pd.DataFrame([{
                "תאריך": date.fromisoformat(t["date"]).strftime("%d/%m/%y"),
                "מניה": t["symbol"],
                "פעולה": ("יתרת פתיחה" if t.get("note") == "opening"
                          else SIDE_HE.get(t["side"], t["side"])),
                "כמות": f"{t['qty']:g}", "מחיר": portfolio.money(t["price"], t.get("currency")),
            } for t in recent])
            st.dataframe(table, hide_index=True, width="stretch")
            if any(t.get("note") == "opening" for t in txs):
                st.caption("״יתרת פתיחה״ = מניות שהוזנו קודם רק עם כמות ומחיר ממוצע, "
                           "בלי תאריך. אפשר למחוק אותן ולהזין את הקניות האמיתיות.")
        try:
            result = portfolio.compute(data)
        except portfolio.PortfolioError as exc:
            st.error(f"יש בעיה ברשימת העסקאות: {exc}")
            result = None
        year = date.today().year
        if result is not None:
            realized = portfolio.realized_in_year(result, year)
            if realized:
                lines = "".join(
                    '<div class="sa-row" style="justify-content:space-between">'
                    f'<span>רווח <b class="sa-num">{portfolio.money(profit, cur, signed=True)}</b></span>'
                    '<span class="sa-sub">מס משוער (25%): '
                    f'<span class="sa-num">{portfolio.money(portfolio.tax_estimate(profit), cur)}</span>'
                    '</span></div>'
                    for cur, profit in sorted(realized.items()))
                st.markdown(html_safe(
                    f'<div class="sa-card"><div class="sa-eyebrow">רווח ממומש ב-{year} '
                    f'(ממכירות)</div>{lines}</div>'), unsafe_allow_html=True)
                st.caption("הערכה גסה בלבד: 25% מהרווח נטו בכל מטבע, בלי עמלות, בלי "
                           "התאמה לאינפלציה ובלי המרה לשקל לפי תאריכי העסקאות. "
                           "לא ייעוץ מס.")
            elif txs:
                st.caption(f"עוד אין מכירות ב-{year}, אז אין רווח ממומש.")
        if txs:
            by_id = {t["id"]: t for t in txs}
            pick = st.selectbox("מחיקת עסקה שהוזנה בטעות", options=list(by_id),
                                index=None, placeholder="בחר עסקה…",
                                format_func=lambda i: _tx_label(by_id[i]), key="tx_delete_pick")
            if pick and st.button("🗑️ מחק את העסקה", key="tx_delete_btn"):
                try:
                    update_portfolio(lambda d: portfolio.remove_transaction(d, pick),
                                     "Remove trade")
                    st.session_state.pop("tx_delete_pick", None)
                    st.rerun()
                except portfolio.PortfolioError as exc:
                    st.error(str(exc))
                except data_store.DataStoreError:
                    st.error("המחיקה נכשלה (בעיה באחסון). נסה שוב.")

    watchlist = st.session_state.get("watchlist", [])
    last = st.session_state.get("last_backup")
    if storage_is_local() and portfolio.needs_backup_reminder(
            last, datetime.now(), bool(txs or watchlist)):
        st.warning("💾 כדאי לשמור גיבוי: התיק ורשימת המעקב נשמרים רק בדפדפן, "
                   "וספארי באייפון מוחק אותם אחרי כשבוע בלי כניסה. פתח את "
                   "**גיבוי ושחזור** למטה.")
    with st.expander("💾 גיבוי ושחזור", expanded=False):
        backup_text = portfolio.make_backup(data, watchlist)
        if storage_is_local():
            st.caption("כרגע התיק ורשימת המעקב נשמרים רק בדפדפן במכשיר הזה. גיבוי "
                       "שומר אותם בקובץ או כטקסט, ואפשר לשחזר אותם בכל מכשיר.")
        st.download_button(
            "⬇️ הורד קובץ גיבוי", data=backup_text,
            file_name=f"stock-app-backup-{date.today().isoformat()}.json",
            mime="application/json", key="backup_download", width="stretch",
            on_click=_mark_backup)
        st.caption("הקובץ כולל את כל העסקאות ואת רשימת המעקב. באייפון הוא נשמר "
                   "באפליקציית ״קבצים״. אפשר גם להעתיק את הטקסט (כפתור ההעתקה "
                   "בפינה) ולשמור אותו, למשל בפתקים:")
        st.code(backup_text, language="json")
        st.button("✓ העתקתי ושמרתי", key="backup_copied", on_click=_mark_backup)
        if last:
            try:
                when = datetime.fromisoformat(last).strftime("%d.%m.%Y %H:%M")
            except ValueError:
                when = last
            st.caption(f"גיבוי אחרון: \u2066{when}\u2069")
        upload = st.file_uploader("שחזור מקובץ גיבוי", type=["json"], key="backup_upload")
        pasted = st.text_area("או הדבק כאן את טקסט הגיבוי", key="restore_text", height=90)
        source = (upload.getvalue().decode("utf-8", "replace") if upload is not None
                  else (pasted or "").strip())
        if source:
            try:
                restored, wl = portfolio.parse_backup(source)
            except portfolio.PortfolioError as exc:
                st.error(str(exc))
            else:
                st.info(f"בקובץ: {len(restored['transactions'])} עסקאות ו-{len(wl)} מניות "
                        "במעקב. השחזור מחליף את מה שיש עכשיו.")
                if st.button("שחזר", key="backup_restore_btn", type="primary"):
                    try:
                        update_portfolio(lambda _d: restored, "Restore from backup")
                        st.session_state["watchlist"] = wl
                        st.session_state["_wl_dirty"] = True
                        st.success("שוחזר.")
                        _mark_backup()
                    except data_store.DataStoreError:
                        st.error("השחזור נכשל (בעיה באחסון). נסה שוב.")


# --- Home screen + small HTML building blocks ----------------------------

# Hebrew names for verdict / analyst labels (the engine keeps English keys).
HE_LABELS = {
    "Strong Buy": "קנייה חזקה", "Buy": "קנייה", "Hold": "החזקה",
    "Sell": "מכירה", "Underperform": "תת-ביצוע", "Strong Sell": "מכירה חזקה",
}
# CSS class per verdict, for the colored word on the verdict card.
VERDICT_CLASS = {"Strong Buy": "up", "Buy": "up", "Hold": "hold", "Sell": "down"}

# The six numbers shown up front on a stock page (the rest sit under
# "כל נתוני החברה"). Beta comes from the technicals group.
KEY_METRICS = ["pe", "revenue_growth", "profit_margin", "debt_to_equity",
               "dividend_yield", "beta"]
KEY_METRIC_LABELS = {
    "pe": "מכפיל רווח (P/E)", "revenue_growth": "צמיחת הכנסות",
    "profit_margin": "שולי רווח", "debt_to_equity": "חוב להון",
    "dividend_yield": "תשואת דיבידנד", "beta": "בטא (תנודתיות)",
}


def he_label(label):
    """Hebrew for a verdict/analyst label; unknown labels pass through."""
    return HE_LABELS.get(label, label or "—")


def _esc(text):
    """Escape text we put inside our own HTML snippets."""
    return html.escape(str(text or ""))


def chip_html(value, suffix=""):
    """A small green/red pill like '+2.31%'. None -> a muted dash."""
    if value is None:
        return '<span class="sa-chip sa-flat">—</span>'
    cls = "sa-up" if value > 0 else ("sa-down" if value < 0 else "sa-flat")
    return f'<span class="sa-chip {cls}">{value:+.2f}{suffix}</span>'


_NAME_SUFFIXES = (", Inc.", " Inc.", " Inc", " Corporation", " Corp.", " Corp", " Limited",
                  " Ltd.", " Ltd", " plc", " PLC", " N.V.", " S.A.", " AG", " SE",
                  " Holdings", " Industries", " Group", " Co.", " Company", " B.M.")


def short_name(name, symbol=""):
    """'Teva Pharmaceutical Industries Limited' -> 'Teva Pharmaceutical'."""
    name = (name or "").strip() or symbol
    for _ in range(3):
        for suf in _NAME_SUFFIXES:
            if name.endswith(suf) and len(name) > len(suf) + 2:
                name = name[: -len(suf)].rstrip(" ,&")
    return name


def display_price(price, currency):
    """A price for display in the MAJOR unit: 13580 ILA -> '₪135.80'."""
    value, major = portfolio.to_major(price, currency)
    return portfolio.money(value, major)


def major_history(df, currency):
    """Price history with agorot/pence turned into ₪/£ (other units as-is)."""
    _, major = portfolio.to_major(1.0, currency)
    if major == (currency or "") or df is None:
        return df
    factor = portfolio.to_major(1.0, currency)[0]
    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in out.columns:
            out[col] = out[col] * factor
    return out


def header_card_html(quote):
    """Name, symbol · exchange, big price and today's change."""
    price = display_price(quote.price, quote.currency)
    today = (chip_html(quote.change_pct, "%") + ' <span class="sa-sub">היום</span>'
             if quote.change_pct is not None else '<span class="sa-sub">אין נתון יומי</span>')
    return (
        f'<div class="sa-name">{_esc(short_name(quote.name, quote.symbol))}</div>'
        f'<div class="sa-sub"><span class="sa-num">{_esc(quote.symbol)}</span> · '
        f'{_esc(quote.exchange or "")}'
        + (f' · {_esc(quote.name)}' if quote.name and short_name(quote.name) != quote.name else "")
        + '</div>'
        f'<div class="sa-row"><span class="sa-price sa-num">{html_safe(_esc(price))}</span>{today}</div>'
    )


# Short Hebrew names for the engine's signal keys (the engine's own names are
# English). Used in the one-line "for / against" summary of the verdict card.
SIGNAL_HE = {
    "pe": "תמחור", "growth": "צמיחה", "fwd_pe": "תחזית רווחים",
    "margin": "רווחיות", "d2e": "חוב", "fcf": "תזרים מזומנים",
    "ma50": "מגמה קצרה (50 יום)", "ma200": "מגמה ארוכה (200 יום)",
    "ma_cross": "חציית ממוצעים", "rsi": "מומנטום (RSI)",
    "range52": "מיקום בטווח השנתי", "macd": "מומנטום (MACD)",
    "bollinger": "רצועות בולינגר", "vol_confirm": "אישור נפח מסחר",
    "obv": "לחץ קנייה/מכירה (OBV)", "ad": "צבירה/הפצה",
}


def _signal_he(ws):
    return SIGNAL_HE.get(getattr(ws, "key", None), ws.name)


def _why_sentence(hv):
    """One plain line from the strongest signals: what helps, what hurts."""
    if hv is None or not hv.breakdown:
        return ""
    ranked = sorted(hv.breakdown, key=lambda ws: ws.weighted, reverse=True)
    pros = [_signal_he(ws) for ws in ranked if ws.weighted > 0][:2]
    cons = [_signal_he(ws) for ws in reversed(ranked) if ws.weighted < 0][:2]
    parts = []
    if pros:
        parts.append("בעד: " + ", ".join(pros))
    if cons:
        parts.append("נגד: " + ", ".join(cons))
    return " · ".join(parts)


def breakdown_html(breakdown):
    """The per-signal verdict breakdown as one left-to-right HTML list."""
    items = []
    for ws in breakdown:
        base_sign = "+" if ws.points > 0 else ""  # minus self-prints
        w_sign = "+" if ws.weighted > 0 else ""
        items.append(
            f"<li><b>{_esc(ws.name)}</b> ({_esc(_signal_he(ws))}): "
            f"{_esc(str(ws.measured))} | base {base_sign}{ws.points} × weight "
            f"{ws.weight:.1f} = <b>{w_sign}{ws.weighted:.1f}</b></li>")
    return ('<div dir="ltr" style="text-align:left"><ul style="margin:0">'
            + "".join(items) + "</ul></div>")


def verdict_card_html(verdict):
    """The 'bottom line' card: the 1-year verdict big, the 3 horizons small."""
    main = verdict.horizons.get("1Y")
    if main is None or not main.enough_data:
        main = next((verdict.horizons.get(h) for h in engine.HORIZONS
                     if verdict.horizons.get(h) and verdict.horizons.get(h).enough_data), None)
    if main is None:
        return '<div class="sa-card">אין מספיק נתונים לפסיקה.</div>'
    cls = VERDICT_CLASS.get(main.label, "hold")
    span = {"6M": "חצי שנה", "1Y": "שנה", "5Y": "5 שנים"}
    pills = []
    for h in engine.HORIZONS:
        hv = verdict.horizons.get(h)
        if hv is None or not hv.enough_data:
            pills.append(f'<div><b>—</b><span>{span[h]}</span></div>')
        else:
            c = VERDICT_CLASS.get(hv.label, "hold")
            pills.append(f'<div><b style="color:var(--sa-{c})">{_esc(he_label(hv.label))}</b>'
                         f'<span>{span[h]} · <span class="sa-num">{hv.score:.0f}</span></span></div>')
    why = _why_sentence(main)
    return (
        '<div class="sa-card"><div class="sa-verdict"><div>'
        f'<div class="sa-eyebrow">השורה התחתונה · {span.get(main.horizon, "")}</div>'
        f'<div class="sa-word" style="color:var(--sa-{cls})">{_esc(he_label(main.label))}</div></div>'
        f'<div class="sa-score"><span class="sa-num"><b>{main.score:.0f}</b><span class="sa-sub">/100</span></span>'
        f'<div class="sa-bar"><i style="width:{main.score:.0f}%;background:var(--sa-{cls})"></i></div></div></div>'
        + (f'<div class="sa-why">{_esc(why)}</div>' if why else "")
        + f'<div class="sa-horiz">{"".join(pills)}</div>'
        '<div class="sa-fine">חוות דעת אוטומטית לפי כללים ונתונים ציבוריים של היום. '
        'שלושת הטווחים שוקלים את אותם נתונים אחרת. לא ייעוץ השקעות ולא תחזית.</div></div>'
    )


def combined_key_metrics(company, technicals):
    """One MetricGroup holding the KEY_METRICS from company + technicals."""
    merged = dict(company.metrics)
    if technicals is not None and technicals.found and "beta" in technicals.metrics:
        merged["beta"] = technicals.metrics["beta"]
    return engine.MetricGroup(True, company.symbol, company.currency, merged,
                              company.source_note)


def range_change_pct(df):
    """% change from the first to the last close of the chart's range."""
    try:
        first = engine.first_close(df)
        last = float(df["Close"].dropna().iloc[-1])
        return (last / first - 1) * 100 if first else None
    except Exception:
        return None


def make_price_area(df, as_percent=False):
    """Clean line + soft area fill; green if the range ended up, red if down."""
    data = df[["Date", "Close"]].dropna().copy()
    field_name, title, fmt = "Close", None, ",.2f"
    if as_percent:
        base = engine.first_close(df)
        data["Pct"] = engine.price_to_pct_change(data["Close"], base)
        field_name, fmt = "Pct", "+.1f"
    change = range_change_pct(df)
    color = "#3CC98A" if (change is None or change >= 0) else "#FF6B6B"
    lo, hi = float(data[field_name].min()), float(data[field_name].max())
    pad = (hi - lo) * 0.08 or 1.0
    y = alt.Y(f"{field_name}:Q", title=title, scale=alt.Scale(domain=[lo - pad, hi + pad], nice=False),
              axis=alt.Axis(format=fmt, grid=True, gridOpacity=0.12, domain=False, ticks=False,
                            labelColor="#8B97AA", tickCount=4))
    x = alt.X("Date:T", title=None, axis=alt.Axis(grid=False, labelColor="#8B97AA",
                                                   domain=False, ticks=False, tickCount=4))
    base = alt.Chart(data).encode(x=x, y=y, tooltip=[alt.Tooltip("Date:T", title="תאריך"),
                                                    alt.Tooltip(f"{field_name}:Q", format=fmt, title="")])
    area = base.mark_area(
        line={"color": color, "strokeWidth": 2},
        color=alt.Gradient(gradient="linear", x1=1, x2=1, y1=1, y2=0,
                           stops=[alt.GradientStop(color="rgba(0,0,0,0)", offset=0),
                                  alt.GradientStop(color=color, offset=1)]),
        opacity=0.35, clip=True,
    )
    return (area.properties(height=230)
            .configure_view(strokeWidth=0)
            .configure(background="transparent"))


def _go_home():
    """Back button: clear the search so the home screen shows."""
    st.session_state["search_box"] = ""


def render_portfolio_home():
    """'My portfolio' on the home screen: the summary card + one row per stock."""
    st.markdown("#### התיק שלי")
    data = load_portfolio()
    if st.session_state.get("portfolio_error"):
        st.error("לא הצלחתי לקרוא את התיק השמור, אז לא מציג ולא שומר שינויים כרגע. "
                 "נסה לרענן בעוד רגע.")
        return
    moved = st.session_state.pop("_pf_moved", None)
    if moved:
        st.success(f"העברתי {moved} מניות מהתיק שנשמר בדפדפן לשמירה החדשה, "
                   "כ״יתרת פתיחה״ עם התאריך של היום.")
    try:
        holdings = portfolio.as_holdings(portfolio.compute(data))
    except portfolio.PortfolioError as exc:
        st.error(f"יש בעיה ברשימת העסקאות: {exc}")
        return
    if not holdings:
        st.caption("עוד אין מניות בתיק. פתח מניה ולחץ **הוספה לתיק** כדי לרשום "
                   "קנייה: כמה מניות, באיזה מחיר ומתי.")
        return
    rows, names = portfolio_rows(holdings)
    st.markdown(portfolio_card_html(portfolio.summarize(rows)), unsafe_allow_html=True)
    with st.container(key="portfolio_rows"):
        for r in rows:
            sym = r["symbol"]
            qty = f"{r['qty']:,.4g}"
            label = f"**{md_safe(names.get(sym, sym))}**{score_tag(score_for(sym))}\n\u2066{qty}\u2069 מניות"
            if r["value"] is not None:
                label += f" · \u2066{md_safe(portfolio.money(r['value'], r['currency'], 0))}\u2069"
            if r["pnl_pct"] is not None:
                color = "green" if r["pnl_pct"] >= 0 else "red"
                label += f" · :{color}[\u2066{r['pnl_pct']:+.1f}%\u2069]"
            st.button(label, key=f"pf_view_{sym}", on_click=_goto, args=(sym,),
                      width="stretch")
    render_portfolio_overview(rows, names, data)


# --- Portfolio overview (mockup item 14) ----------------------------------

AI_OVERVIEW_TTL_H = 24


@st.cache_resource
def _ai_overview_store():
    """Server-wide: portfolio signature -> {"data": {...}, "at": datetime}."""
    return {}


def _run_ai_overview(sig, facts_payload):
    data = gemini_helper.portfolio_overview(facts_payload, get_gemini_key())
    if data:
        _ai_overview_store()[sig] = {"data": data, "at": datetime.now()}
    else:
        st.session_state["_ai_overview_failed"] = True


def render_portfolio_overview(rows, names, data=None):
    """Facts computed in code + (on request) AI strengths / weaknesses / ideas."""
    scores = {r["symbol"]: score_for(r["symbol"])["score"] for r in rows}
    facts = ai_insights.portfolio_facts(rows, scores, names)
    if not facts["weights"]:
        return
    with st.container(border=True):
        st.markdown("#### מבט על התיק")
        avg = facts["avg_score"]
        top_sym, top_name, top_pct = facts["top"]
        cells = [
            (f"{avg:+d}" if avg is not None else "—", "ציון ממוצע"),
            (f"{top_pct:.0f}%", "במניה הגדולה"),
            (f"{facts['il_pct']:.0f}%", "בישראל (₪)"),
        ]
        st.markdown(
            '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px">'
            + "".join(f'<div class="sa-card" style="margin:0;padding:8px;text-align:center">'
                      f'<b class="sa-num" style="font-size:1.15rem">{_esc(v)}</b><br>'
                      f'<span class="sa-sub">{_esc(k)}</span></div>' for v, k in cells)
            + "</div>"
            + '<div style="display:flex;gap:3px;height:10px;border-radius:99px;overflow:hidden">'
            + "".join(f'<span style="flex:{p:.2f};background:var(--sa-accent);opacity:{max(.3, 1 - i * .2):.2f}"></span>'
                      for i, (_, _, p) in enumerate(facts["weights"]))
            + '</div><div class="sa-sub" style="display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:4px">'
            + "".join(f'<span>{_esc(n)} <span class="sa-num">{p:.0f}%</span></span>'
                      for _, n, p in facts["weights"])
            + "</div>", unsafe_allow_html=True)
        if data:
            render_vs_index(data)

        sig = ai_insights.signature(facts)
        item = _ai_overview_store().get(sig)
        fresh = item and (datetime.now() - item["at"]).total_seconds() < AI_OVERVIEW_TTL_H * 3600
        if fresh:
            for key, title, icon in (("strengths", "חוזקות", "✓"), ("weaknesses", "חולשות", "!"),
                                     ("ideas", "רעיונות לבדיקה", "→")):
                if item["data"].get(key):
                    st.markdown(f"**{title}**")
                    for line in item["data"][key]:
                        st.markdown(f"{icon} {md_safe(line)}")
            st.caption(f"ניתוח AI מ-\u2066{item['at']:%d.%m %H:%M}\u2069 (מתרענן עד פעם ביום).")
        no_key = not get_gemini_key()
        st.button("🤖 רענן ניתוח AI" if fresh else "🤖 נתח את התיק עם AI",
                  key="ai_overview_btn", on_click=_run_ai_overview,
                  args=(sig, ai_insights.facts_for_ai(facts)), disabled=no_key or bool(fresh),
                  help="צריך מפתח Gemini" if no_key else
                  ("כבר נותח היום" if fresh else "קריאה אחת ל-Gemini"), width="stretch")
        if st.session_state.pop("_ai_overview_failed", False):
            st.caption("_ה-AI לא הצליח לנתח כרגע. נסה שוב מאוחר יותר._")
        st.caption("המספרים (משקלים, ציונים) מחושבים בקוד. ה-AI כותב רק את החוזקות, "
                   "החולשות והרעיונות. רעיונות לבדיקה, לא ייעוץ השקעות.")


# --- Portfolio vs the index (mockup item 17) -------------------------------

BENCH_LABELS = {v["name"]: k for k, v in benchmark.INDEXES.items()}


@st.cache_data(ttl=3600)
def load_closes(symbol):
    """One year of daily closes in the MAJOR unit: [(iso_date, close)], or []."""
    try:
        h = load_history(symbol, "1Y")
        if not h.found:
            return []
        q = load_quote(symbol)
        df = major_history(h.data, q.currency if (q and q.found) else "")
        df = df[["Date", "Close"]].dropna()
        return [(pd.Timestamp(t).date().isoformat(), float(c))
                for t, c in zip(df["Date"], df["Close"])]
    except Exception:
        return []


def _symbol_fx(symbol):
    try:
        q = load_quote(symbol)
        if q and q.found:
            return load_fx(portfolio.to_major(q.price, q.currency)[1])
    except Exception:
        pass
    return None


def make_vs_index_chart(points, index_name):
    rows = []
    for d, mine, ix in points:
        rows.append({"Date": d, "line": "התיק שלך", "pct": mine})
        rows.append({"Date": d, "line": index_name, "pct": ix})
    df = pd.DataFrame(rows)
    color = alt.Color("line:N", legend=None, scale=alt.Scale(
        domain=["התיק שלך", index_name], range=["#8EA2FF", "#8B97AA"]))
    dash = alt.StrokeDash("line:N", legend=None, scale=alt.Scale(
        domain=["התיק שלך", index_name], range=[[1, 0], [4, 3]]))
    return (alt.Chart(df).mark_line(strokeWidth=2.2).encode(
                x=alt.X("Date:T", title=None, axis=alt.Axis(grid=False, labelColor="#8B97AA",
                                                            domain=False, ticks=False, tickCount=4)),
                y=alt.Y("pct:Q", title=None, axis=alt.Axis(format="+.0f", grid=True,
                        gridOpacity=0.12, domain=False, ticks=False, labelColor="#8B97AA",
                        tickCount=4, labelExpr="datum.label + '%'")),
                color=color, strokeDash=dash,
                tooltip=[alt.Tooltip("Date:T", title="תאריך"), alt.Tooltip("line:N", title=""),
                         alt.Tooltip("pct:Q", format="+.1f", title="%")])
            .properties(height=170).configure_view(strokeWidth=0)
            .configure(background="transparent"))


def render_vs_index(data):
    """'Vs the index': the same money, on the same days, into TA-125 / S&P 500."""
    txs = data.get("transactions", [])
    if not txs:
        return
    st.markdown('<h4 style="margin:0.9rem 0 0.2rem">מול המדד '
                '<span class="sa-sub" style="font-weight:400">שנה אחרונה</span></h4>',
                unsafe_allow_html=True)
    names = list(BENCH_LABELS)
    choice = st.segmented_control("מדד", names, default=names[0], key="bench_index",
                                  label_visibility="collapsed") or names[0]
    index = benchmark.INDEXES[BENCH_LABELS[choice]]
    symbols = sorted({t["symbol"] for t in txs})
    result = benchmark.compare(
        txs, {s: load_closes(s) for s in symbols}, {s: _symbol_fx(s) for s in symbols},
        load_closes(index["symbol"]))
    if result is None:
        st.caption("אין כרגע מספיק נתוני מחירים כדי להשוות למדד.")
        return
    if len(result["points"]) < benchmark.MIN_POINTS:
        start = datetime.fromisoformat(result["start"]).strftime("%d.%m.%Y")
        st.caption(f"עוד מוקדם להשוות: העסקאות בתיק הן מ-\u2066{start}\u2069. ההשוואה תופיע "
                   "אחרי שבוע מסחר. אם קנית לפני כן, אפשר לתקן את תאריך הקנייה "
                   "ב״עסקאות ומס״ (מחיקה ורישום מחדש).")
        return
    st.altair_chart(make_vs_index_chart(result["points"], choice), width="stretch")
    win = result["diff"] >= 0
    st.markdown(html_safe(
        '<div class="sa-sub" style="display:flex;gap:14px;flex-wrap:wrap">'
        '<span><i style="display:inline-block;width:12px;height:3px;background:var(--sa-accent);'
        'vertical-align:middle;margin-inline-end:5px"></i>התיק שלך '
        f'<b class="sa-num" style="color:var(--sa-fg)">{result["my_pct"]:+.1f}%</b></span>'
        '<span><i style="display:inline-block;width:12px;height:0;border-top:3px dashed var(--sa-muted);'
        f'vertical-align:middle;margin-inline-end:5px"></i>{_esc(choice)} '
        f'<b class="sa-num" style="color:var(--sa-fg)">{result["idx_pct"]:+.1f}%</b></span></div>'
        f'<p style="margin:6px 0 0;font-weight:700;color:var(--sa-{"up" if win else "down"})">'
        f'{_esc(benchmark.verdict_he(result, choice))}</p>'), unsafe_allow_html=True)
    start = datetime.fromisoformat(result["start"]).strftime("%d.%m.%Y")
    extra = (f" בלי {', '.join(result['missing'])} (אין היסטוריית מחירים)."
             if result["missing"] else "")
    st.caption(f"כאילו כל קנייה מאז ⁦{start}⁩ הייתה נכנסת למדד באותו יום ובאותו סכום, "
               f"וכל מכירה הייתה מוכרת את אותו חלק. ההשוואה לא כוללת דיבידנדים ועמלות.{extra}")


# --- "AI recommends" (mockup item 15) ------------------------------------

def load_ai_picks():
    try:
        return data_store.read_json("data/ai_picks.json", None, **_store_kwargs())
    except Exception:
        return None


def _pick_label(sym):
    """'**Salesforce** · CRM' + price and today's change on a second line."""
    try:
        q = load_quote(sym)
    except Exception:
        q = None
    if not (q and q.found):
        return f"**\u2066{sym}\u2069**"
    label = (f"**{md_safe(short_name(q.name, sym))}** · \u2066{sym}\u2069\n"
             f"\u2066{md_safe(display_price(q.price, q.currency))}\u2069")
    if q.change_pct is not None:
        color = "green" if q.change_pct >= 0 else "red"
        label += f" · :{color}[\u2066{q.change_pct:+.2f}%\u2069] היום"
    return label


def render_ai_picks_home():
    st.caption("מניות שכדאי לבדוק, מתוך כ-611 המניות שנסרקו בלילה. רעיונות לבדיקה, "
               "לא המלצה לקנות.")
    picks = load_ai_picks()
    if not picks or not (picks.get("short") or picks.get("long")):
        st.info("ההמלצות נוצרות בסריקה היומית (08:00) ונשלחות לטלגרם. באתר הן "
                "יופיעו כאן אחרי שנחבר את המאגר הפרטי, כי הסריקה רצה על המחשב "
                "ולא בשרת של האתר.")
        return
    by_ai = picks.get("source") == "ai"
    for key, title, hint in (("short", "לטווח קצר", "שבועות עד חודשים"),
                             ("long", "לטווח ארוך", "שנה ומעלה")):
        if not picks.get(key):
            continue
        with st.container(border=True):
            st.markdown(f"#### {title} <span class='sa-sub'>{hint}</span>",
                        unsafe_allow_html=True)
            with st.container(key=f"picks_{key}"):
                for p in picks[key]:
                    st.button(_pick_label(p["symbol"]), key=f"pick_{key}_{p['symbol']}",
                              on_click=_goto, args=(p["symbol"],), width="stretch")
                    st.caption(md_safe(p["why"]))
    try:
        when = date.fromisoformat(picks["day"]).strftime("%d.%m.%Y")
    except (KeyError, ValueError):
        when = "?"
    st.caption(f"מהסריקה של \u2066{when}\u2069. "
               + ("הקוד סינן קודם את המניות החזקות, ורק אחר כך ה-AI בחר מתוכן והסביר."
                  if by_ai else "נבחר לפי הקוד בלבד (ה-AI לא היה זמין)."))


# --- Buy score (mockup items 12-13) -------------------------------------

AI_NEWS_TTL_H = 12   # one Gemini news call per stock per 12 hours, at most


@st.cache_data(ttl=1800)
def load_news(symbol, name):
    return news.fetch_headlines(symbol, name, limit=5)


@st.cache_resource
def _ai_news_store():
    """Server-wide memory of AI news ratings: symbol -> {score, reason, at}.
    Shared by every page and visitor so a rating is reused, not re-bought."""
    return {}


def ai_news_rating(symbol):
    """The cached AI news rating for `symbol` if it is fresh, else None."""
    item = _ai_news_store().get(symbol)
    if not item:
        return None
    age_h = (datetime.now() - item["at"]).total_seconds() / 3600
    return item if age_h < AI_NEWS_TTL_H else None


def _run_ai_news(symbol, name):
    """Button callback: read the headlines with Gemini (one call), store it."""
    key = get_gemini_key()
    heads = load_news(symbol, name)
    result = gemini_helper.rate_news(symbol, name, heads, key) if key else None
    if result:
        _ai_news_store()[symbol] = {"score": result[0], "reason": result[1],
                                    "at": datetime.now()}
    else:
        st.session_state["_ai_news_failed"] = symbol


def score_for(symbol, verdict=None, analyst=None):
    """The buy score dict for a symbol (loads verdict/analyst if not given)."""
    try:
        verdict = verdict if verdict is not None else load_verdict(symbol)
    except Exception:
        verdict = None
    try:
        analyst = analyst if analyst is not None else load_analyst(symbol)
    except Exception:
        analyst = None
    ai = ai_news_rating(symbol)
    return buy_score.from_engine_objects(verdict, analyst, ai["score"] if ai else None)


def score_tag(score):
    """' · ציון +34' (colored) for list rows, or '' when there is no score."""
    if not score or score["score"] is None:
        return ""
    color = {"up": "green", "down": "red"}.get(score["cls"], "orange")
    return f" · :{color}[ציון \u2066{score['score']:+d}\u2069]"


PART_NAMES = {"engine": ("המנוע שלנו", "16 סימנים מהנתונים, תחזית לשנה"),
              "analysts": ("אנליסטים", "דירוג ויעד מחיר ממוצע (Yahoo)"),
              "ai": ("AI על החדשות", "Gemini קורא את הכותרות האחרונות")}


def score_card_html(score):
    """The buy-score card: big number, lean label, a -100..+100 bar."""
    if score["score"] is None:
        return '<div class="sa-card"><div class="sa-eyebrow">ציון קנייה</div>אין מספיק נתונים.</div>'
    n, cls = score["score"], score["cls"]
    pos = (n + 100) / 2
    return (
        '<div class="sa-card"><div class="sa-verdict"><div>'
        '<div class="sa-eyebrow">ציון קנייה</div>'
        f'<div class="sa-word sa-num" style="color:var(--sa-{cls})">{n:+d}</div></div>'
        f'<span class="sa-chip sa-{cls}">{_esc(score["label"])}</span></div>'
        '<div style="position:relative;height:10px;border-radius:99px;margin:14px 4px 6px;'
        'direction:ltr;background:linear-gradient(90deg,var(--sa-down),var(--sa-hold),var(--sa-up));opacity:.85">'
        f'<span style="position:absolute;top:50%;left:{pos:.1f}%;width:16px;height:16px;'
        'border-radius:50%;background:var(--sa-fg);border:3px solid var(--sa-surface);'
        'transform:translate(-50%,-50%)"></span></div>'
        '<div class="sa-sub" style="display:flex;justify-content:space-between;direction:ltr">'
        '<span>-100 מכירה</span><span>0</span><span>קנייה +100</span></div></div>'
    )


def render_score_section(symbol, name, verdict, analyst):
    """Stock page: the buy-score card + 'how was it calculated' + AI news part."""
    score = score_for(symbol, verdict, analyst)
    st.markdown(score_card_html(score), unsafe_allow_html=True)
    ai = ai_news_rating(symbol)
    with st.expander("איך חושב הציון?", expanded=False):
        for p in score["parts"]:
            title, desc = PART_NAMES[p["key"]]
            if p["key"] == "ai" and ai and ai.get("reason"):
                desc = ai["reason"]
            if p["value"] is None:
                val = "—"
                desc += " · לא נכלל" if p["key"] != "ai" else " · עוד לא הופעל"
            else:
                val = f"{p['value']:+d}"
            st.markdown(f"<b>{_esc(title)}</b> · {p['weight']*100:.0f}% · "
                        f"<b class='sa-num'>{_esc(val)}</b><br>"
                        f"<span class='sa-sub'>{_esc(desc)}</span>",
                        unsafe_allow_html=True)
        if score["formula"]:
            st.code(score["formula"], language=None, wrap_lines=True)
        if score["missing"]:
            st.caption("חלק שחסר לא נספר, והמשקלים של השאר מתחלקים מחדש ל-100%.")
        if ai is None:
            no_key = not get_gemini_key()
            st.button("🤖 קרא את החדשות עם AI", key=f"ai_news_{symbol}",
                      on_click=_run_ai_news, args=(symbol, name), disabled=no_key,
                      help="צריך מפתח Gemini" if no_key else
                      "קריאה אחת ל-Gemini, נשמרת ל-12 שעות", width="stretch")
            if st.session_state.pop("_ai_news_failed", None) == symbol:
                st.caption("_ה-AI לא הצליח לקרוא את החדשות כרגע. נסה שוב מאוחר יותר._")
        st.caption("הציון מחושב בקוד. ה-AI רק מוסיף את חלק החדשות ומסביר אותו. "
                   "לא ייעוץ השקעות.")


def render_news_section(symbol, name):
    """3 latest headlines with source and age (mockup item 18), AI line on top."""
    heads = load_news(symbol, name)
    with st.container(border=True):
        st.markdown("#### חדשות")
        ai = ai_news_rating(symbol)
        if ai and ai.get("reason"):
            st.markdown(f"**AI ·** {_esc(ai['reason'])}", unsafe_allow_html=True)
        if not heads:
            st.caption("לא נמצאו כותרות מהשבוע האחרון.")
            return
        for h in heads[:3]:
            meta = " · ".join(x for x in (h.get("source"), news.age_he(h.get("published"))) if x)
            title = md_safe(h["title"])
            line = f"[{title}]({h['link']})" if h.get("link") else title
            st.markdown(f"{line}  \n<span class='sa-sub'>{_esc(meta)}</span>",
                        unsafe_allow_html=True)
        st.caption("כותרות וקישורים בלבד, ממקורות חינמיים: \u2066Yahoo / Google News\u2069.")


def render_watchlist_home():
    """The followed stocks as tappable rows on the home screen."""
    st.markdown("#### במעקב")
    watchlist = st.session_state.get("watchlist", [])
    if not watchlist:
        st.caption("עוד אין מניות במעקב. חפש מניה ולחץ **☆ הוסף למעקב**.")
        return
    with st.container(key="watchlist"):
        for sym in watchlist:
            # \u2066...\u2069 keep each number/symbol left-to-right inside
            # the Hebrew (right-to-left) row, so "+1.12%" never comes out jumbled.
            label = f"**\u2066{sym}\u2069**"
            try:
                q = load_quote(sym)
                if q and q.found:
                    label = (f"**{md_safe(short_name(q.name, sym))}**{score_tag(score_for(sym))}\n"
                             f"\u2066{sym}\u2069 · \u2066{md_safe(display_price(q.price, q.currency))}\u2069")
                    if q.change_pct is not None:
                        color = "green" if q.change_pct >= 0 else "red"
                        label += f" · :{color}[\u2066{q.change_pct:+.2f}%\u2069]"
                else:
                    label += score_tag(score_for(sym))
            except Exception:
                pass
            st.button(label, key=f"wl_view_{sym}", on_click=_goto, args=(sym,),
                      width="stretch")
    st.caption("נשמר בדפדפן במכשיר הזה.")


# --- Page content ---------------------------------------------------------
#
# Two screens in one script, chosen by the search box:
#   * empty search  -> HOME: "My stocks" (search + the followed stocks)
#   * a stock found -> STOCK PAGE: one scroll, the bottom line first
#
# Load the watchlist first. A row click stores a pending query that we move
# into the search box BEFORE the widget is created (Streamlit rule).
ls = init_watchlist()
_LS = ls
init_last_backup(ls)
migrate_browser_portfolio(ls)
if "pending_query" in st.session_state:
    st.session_state["search_box"] = st.session_state.pop("pending_query")

home = not (st.session_state.get("search_box") or "").strip()

if home:
    st.markdown('<div class="sa-brand">המניות שלי</div>', unsafe_allow_html=True)
else:
    st.button("→ המניות שלי", key="back_home", on_click=_go_home)

# A search box: accepts a ticker, a company name (English or Hebrew), or a
# Tel-Aviv (.TA) symbol. Pressing Enter reruns the script.
query = st.text_input(
    "חיפוש מניה",
    placeholder="חיפוש: אפל, טבע, NVDA, TEVA.TA…",
    key="search_box",
    label_visibility="collapsed",
).strip()

if home:
    home_tab = st.segmented_control(
        "תצוגה", options=["התיק והמעקב", "ה-AI ממליץ"], default="התיק והמעקב",
        key="home_tab", label_visibility="collapsed") or "התיק והמעקב"
    if home_tab == "ה-AI ממליץ":
        render_ai_picks_home()
    else:
        render_portfolio_home()
        render_watchlist_home()
        render_trades_and_backup()
    st.caption("טיפ: מניות בבורסה בתל אביב מסתיימות ב-`.TA`, למשל `TEVA.TA`. "
               "החיפוש לא עובד לפי מספר נייר.")

# Resolve the query to candidate tickers. If there's more than one match, let
# the user pick the right one (e.g. the Tel-Aviv AVIV.TA, not a US ETF).
symbol = None
if query and query == st.session_state.get("exact_symbol"):
    symbol = query          # opened from a portfolio / watchlist row
elif query:
    st.session_state.pop("exact_symbol", None)   # a typed search asks as before
    try:
        matches = load_matches(query)
    except Exception:
        matches = []

    if not matches:
        st.error("לא מצאתי מניה כזו. כדאי לבדוק את השם או הסימול.")
        st.caption("מניה ישראלית? חפש לפי הסימול שמסתיים ב-**.TA** (למשל `AVIV.TA`). "
                   "מקור הנתונים החינמי (Yahoo Finance) לא מכיר מספרי נייר של הבורסה בתל אביב.")
    elif len(matches) == 1:
        symbol = matches[0].symbol
    else:
        # Several matches -> ASK first; nothing is analysed until a pick.
        labels = {m.symbol: f"{m.symbol} - {m.name} ({m.exchange or '?'})"
                  for m in matches}
        symbol = st.selectbox(
            "נמצאו כמה מניות. לאיזו התכוונת?",
            options=[m.symbol for m in matches],
            index=None,
            placeholder="בחר מניה…",
            format_func=lambda s: labels.get(s, s),
        )
        if symbol is None:
            st.caption("בחר מניה מהרשימה כדי לראות את הניתוח.")

# Once we have a resolved symbol, run the full analysis on it.
if symbol:
    try:
        quote = load_quote(symbol)
    except Exception:
        quote = None

    if quote is None or not quote.found:
        st.error("לא הצלחתי לטעון את המניה הזו. נסה מניה אחרת.")
    else:
        # ---- Load everything once (cached); each section renders its slice.
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

        # ======================= Header =======================
        st.markdown(header_card_html(quote), unsafe_allow_html=True)
        following = engine.in_watchlist(st.session_state.get("watchlist", []), symbol)
        st.button(
            "★ במעקב (לחץ להסרה)" if following else "☆ הוסף למעקב",
            key="wl_toggle", on_click=_toggle_watchlist, args=(symbol,),
        )

        # ======================= My holding (portfolio) =======================
        render_trade_box(symbol, quote)

        # ======================= Price chart =======================
        range_key = st.segmented_control(
            "טווח",
            options=list(engine.RANGES.keys()),
            default="6M",
            key="range_key",
            label_visibility="collapsed",
        ) or "6M"  # clicking the active button deselects it -> keep a chart
        history = load_history(symbol, range_key)
        if history.found:  # agorot -> ₪ so the chart matches the header
            history.data = major_history(history.data, quote.currency)
        chart_type = st.session_state.get("chart_type", "קו")
        as_percent = st.session_state.get("as_percent", False)

        if not history.found:
            st.info(f"אין נתוני גרף לטווח {range_key} ({history.reason}).")
        elif chart_type == "נרות":
            base_close = engine.first_close(history.data) if as_percent else None
            st.altair_chart(make_candlestick(history.data, pct_first_close=base_close),
                            width="stretch")
            if base_close:
                st.caption("הנרות מראים מחיר (ציר שמאל). הציר הימני מראה שינוי באחוזים "
                           "מתחילת הטווח.")
        else:
            st.altair_chart(make_price_area(history.data, as_percent=as_percent),
                            width="stretch")
        if history.found:
            change = range_change_pct(history.data)
            if change is not None:
                st.markdown(
                    f'<span class="sa-sub">בטווח {range_key}:</span> '
                    f'{chip_html(change, "%")}', unsafe_allow_html=True)

        with st.expander("אפשרויות גרף", expanded=False):
            st.radio("סוג גרף", options=["קו", "נרות"], horizontal=True, key="chart_type")
            st.toggle("הצג כשינוי באחוזים מתחילת הטווח", key="as_percent")

        # ======================= Buy score (items 12-13) =======================
        render_score_section(symbol, quote.name or symbol, verdict, analyst)

        # ======================= Bottom line (verdict) =======================
        if verdict is None or not verdict.found:
            st.info("אין פסיקה זמינה למניה הזו.")
        else:
            st.markdown(verdict_card_html(verdict), unsafe_allow_html=True)
            with st.expander("למה? איך חושב הציון", expanded=False):
                horizon_tabs = st.tabs([HORIZON_NAMES.get(h, h) for h in engine.HORIZONS])
                for htab, horizon in zip(horizon_tabs, engine.HORIZONS):
                    hv = verdict.horizons.get(horizon)
                    with htab:
                        if hv is None or not hv.enough_data:
                            st.info(hv.reason if hv else "אין מספיק נתונים.")
                            continue
                        st.caption(
                            f"ציון משוקלל {hv.score:.1f}/100 (50 = ניטרלי). "
                            "כל סימן: base (נקודות בסיס) × weight (משקל לטווח) = "
                            "התרומה לציון."
                        )
                        # Engine text is English, so show the list as one
                        # left-to-right block (mixed into RTL it garbles).
                        st.markdown(breakdown_html(hv.breakdown),
                                    unsafe_allow_html=True)

        # ======================= Wall Street =======================
        with st.container(border=True):
            st.markdown("#### מול וול סטריט")
            if analyst is None or not analyst.found or not analyst.has_coverage:
                st.caption("אין סיקור של אנליסטים למניה הזו (נפוץ במניות קטנות ובתל אביב), "
                           "אז אין למה להשוות.")
            else:
                col_ours, col_street = st.columns(2)
                with col_ours:
                    st.markdown("**הפסיקה שלנו (שנה)**")
                    hv = (verdict.horizons.get("1Y")
                          if verdict is not None and verdict.found else None)
                    if hv is not None and hv.enough_data:
                        c = VERDICT_COLORS.get(hv.label, "gray")
                        st.markdown(f":{c}[**{he_label(hv.label)}**] · {hv.score:.0f}/100",
                                    help=engine.HELP_TEXTS.get("verdict_score"))
                    else:
                        st.markdown("—")
                with col_street:
                    st.markdown("**האנליסטים**")
                    c = ANALYST_COLORS.get(analyst.label, "gray")
                    mean_txt = (f" · {analyst.mean:.2f}/5" if analyst.mean is not None else "")
                    st.markdown(f":{c}[**{he_label(analyst.label)}**]{mean_txt}",
                                help=engine.HELP_TEXTS.get("analyst_mean"))
                    if analyst.num_analysts:
                        st.caption(f"{analyst.num_analysts} אנליסטים")

                if analyst.target_mean and analyst.current_price:
                    cur = analyst.currency or quote.currency or ""
                    up = analyst.upside_pct or 0.0
                    st.markdown(
                        f"**יעד מחיר ממוצע:** <span class='sa-num'>"
                        f"{html_safe(_esc(display_price(analyst.target_mean, cur)))}</span> "
                        f"{chip_html(up, '%')}", unsafe_allow_html=True)
                    if analyst.target_low and analyst.target_high:
                        st.caption(md_safe(
                            f"טווח היעדים: \u2066{display_price(analyst.target_low, cur)}"
                            f"\u2069 עד \u2066{display_price(analyst.target_high, cur)}\u2069"))
                st.caption("זה מה שוול סטריט מצפה לו, לא התחזית שלנו.")

                # ALWAYS say something about how we compare to the analysts.
                divergence = engine.explain_divergence(verdict, analyst, "1Y")
                if not divergence.diverges:
                    st.success("✅ " + (divergence.note or
                               "הפסיקה שלנו תואמת את דעת האנליסטים."))
                else:
                    if divergence.direction == "analysts_more_bullish":
                        title = (f"⚖️ למה יש פער? האנליסטים ({he_label(divergence.analyst_label)}) "
                                 f"אופטימיים יותר מאיתנו ({he_label(divergence.our_label)})")
                        drivers_word = "מה מוריד את הציון שלנו"
                    else:
                        title = (f"⚖️ למה יש פער? אנחנו ({he_label(divergence.our_label)}) "
                                 f"אופטימיים יותר מהאנליסטים ({he_label(divergence.analyst_label)})")
                        drivers_word = "מה מעלה את הציון שלנו"
                    st.markdown(f"**{title}**")
                    st.write(divergence.note)
                    if divergence.drivers:
                        st.write(f"**{drivers_word}:**")
                        for name, measured, weighted in divergence.drivers:
                            st.write(f"- **{name}**: {measured} ({weighted:+.1f})")
            st.caption(
                f"פירוט נוסף ב-[Yahoo Finance](https://finance.yahoo.com/quote/{symbol}/analysis). "
                "ציונים של אתרים כמו TipRanks בדרך כלל בתשלום."
            )

        # ======================= News (item 18) =======================
        render_news_section(symbol, quote.name or symbol)

        # ======================= Key numbers =======================
        st.markdown("#### נתונים עיקריים")
        if company is not None and company.found and company.metrics:
            key_group = combined_key_metrics(company, technicals)
            with st.container(key="kpis"):
                render_metrics(key_group, keys=KEY_METRICS, columns_per_row=2,
                               context=metric_context, labels=KEY_METRIC_LABELS)
            st.caption("🟢 חוזקה · 🟡 לשים לב · 🔴 חולשה. כללי אצבע מהמספרים, "
                       "לא ייעוץ השקעות. לחץ על **?** להסבר.")
        else:
            st.info("אין נתוני חברה זמינים למניה הזו.")

        # ======================= More (collapsed) =======================
        with st.expander("🤖 ניתוח AI", expanded=False):
            # OPTIONAL; Gemini, button-triggered. We NEVER auto-call Gemini — the
            # user clicks a button. Each (symbol, depth) result is cached in
            # session_state so re-clicks / reruns don't re-call (protecting
            # free-tier quota). With no key the buttons are disabled.
            gemini_key = get_gemini_key()
            no_key = not gemini_key
            ai_cache = st.session_state.setdefault("_gemini_cache", {})
            ai_active = st.session_state.setdefault("_gemini_active_depth", {})

            disabled_help = "צריך להגדיר מפתח Gemini כדי להפעיל" if no_key else None
            col_quick, col_deep = st.columns(2)
            if col_quick.button("⚡ הסבר קצר", key="ai_quick_btn", disabled=no_key,
                                help=disabled_help, width="stretch"):
                ai_active[symbol] = "quick"
            if col_deep.button("🔍 ניתוח מעמיק", key="ai_deep_btn", disabled=no_key,
                               help=disabled_help, width="stretch"):
                ai_active[symbol] = "deep"

            depth = ai_active.get(symbol)
            if depth:
                cache_key = (symbol, depth)
                if cache_key not in ai_cache:
                    divergence_for_ai = None
                    try:
                        if (verdict is not None and analyst is not None
                                and analyst.found and analyst.has_coverage):
                            divergence_for_ai = engine.explain_divergence(
                                verdict, analyst, "1Y")
                    except Exception:
                        divergence_for_ai = None
                    payload = _gemini_payload(symbol, verdict, company,
                                              technicals, analyst, divergence_for_ai)
                    with st.spinner("Gemini כותב…"):
                        ai_cache[cache_key] = gemini_helper.explain_verdict(
                            payload, gemini_key, depth=depth)

                explanation = ai_cache[cache_key]
                if explanation:
                    st.info(explanation)
                    st.caption("סיכום שנכתב על ידי AI מהנתונים שבדף. לא ייעוץ השקעות.")
                else:
                    st.caption("_ניתוח ה-AI לא זמין כרגע._")
            else:
                st.caption("Gemini מסביר במילים פשוטות את הפסיקה והנתונים. רץ רק בלחיצה, "
                           "כדי לחסוך במכסה החינמית.")

        with st.expander("כל נתוני החברה", expanded=False):
            if company is not None and company.found and company.metrics:
                render_metrics(company, context=metric_context)
            else:
                st.caption("_אין נתוני חברה זמינים._")

        with st.expander("ניתוח טכני", expanded=False):
            if technicals is not None and technicals.found and technicals.metrics:
                st.markdown("**מחיר, מגמה ומומנטום**")
                render_metrics(technicals, keys=PRICE_TREND_KEYS, context=metric_context)
                st.markdown("**נפח מסחר ולחץ קנייה/מכירה (הערכות)**")
                render_metrics(technicals, keys=PRESSURE_KEYS, context=metric_context)
                st.caption(
                    "נתונים חינמיים לא מפרידים בין נפח קנייה לנפח מכירה, ולכן **OBV** "
                    "ו-**Accumulation/Distribution** הם הערכות שמחושבות ממחיר ונפח, "
                    "לא נתוני מסחר אמיתיים."
                )
                has_volume = False
                if history.found and "Volume" in history.data.columns:
                    volumes = history.data["Volume"].dropna()
                    has_volume = len(volumes) > 0 and float(volumes.sum()) > 0
                if has_volume:
                    st.markdown(f"**נפח מסחר ({range_key})**")
                    st.altair_chart(make_volume_chart(history.data), width="stretch")
            else:
                st.caption("_אין נתונים טכניים זמינים._")

        with st.expander("מוסדיים גדולים", expanded=False):
            if holders is None or not holders.found:
                st.caption("_אין נתוני מחזיקים מוסדיים למניה הזו._")
            else:
                if holders.institutions_pct_held is not None:
                    pct = holders.institutions_pct_held * 100
                    across = (f" ({holders.institutions_count:,} גופים)"
                              if holders.institutions_count else "")
                    st.markdown(f"**{pct:.1f}%** מהמניות מוחזקות בידי מוסדיים{across}.")
                if holders.top_holders:
                    table = pd.DataFrame([{
                        "Holder": h.name,
                        "Shares": f"{h.shares:,}" if h.shares is not None else "—",
                        "% held": (f"{h.pct_held * 100:.2f}%"
                                   if h.pct_held is not None else "—"),
                        "Date reported": h.date_reported or "—",
                    } for h in holders.top_holders])
                    st.dataframe(table, hide_index=True, width="stretch")
                st.caption("מדוחות **13F** רבעוניים (באיחור של עד כ-45 יום), בעיקר מנהלי "
                           "השקעות גדולים כמו Vanguard ו-BlackRock. לא ייעוץ השקעות.")

        # Famous investors (real SEC 13F data; button-triggered so we never
        # run ~10 EDGAR calls on page load). Cached per symbol for the session.
        with st.expander("משקיעים מפורסמים", expanded=False):
            famous_cache = st.session_state.setdefault("_famous_cache", {})
            requested = st.session_state.setdefault("_famous_requested", set())
            if st.button("בדוק משקיעים מפורסמים", key="famous_btn"):
                requested.add(symbol)

            if symbol in requested:
                if symbol not in famous_cache:
                    with st.spinner("בודק דוחות 13F ב-SEC…"):
                        try:
                            famous_cache[symbol] = famous_investors.get_famous_holders(
                                symbol, get_edgar_identity())
                        except Exception:
                            famous_cache[symbol] = None  # error sentinel
                famous = famous_cache[symbol]

                if famous is None:
                    st.caption("_הנתונים על משקיעים מפורסמים לא זמינים כרגע._")
                elif famous:
                    ftable = pd.DataFrame([{
                        "Investor": h["name"],
                        "Fund": h["fund"],
                        "Shares": (f"{h['shares']:,.0f}"
                                   if h.get("shares") is not None else "—"),
                        "Value": (f"{h['value']:,.0f}"
                                  if h.get("value") is not None else "—"),
                        "% of portfolio": (
                            f"{h['pct_of_portfolio']:.1f}%"
                            if h.get("pct_of_portfolio") is not None else "—"),
                        "vs last filing": _famous_change_str(h),
                        "As of": h.get("period") or "—",
                    } for h in famous])
                    st.dataframe(ftable, hide_index=True, width="stretch")
                    st.caption("מדוחות **13F** של ה-SEC (באיחור של עד כ-45 יום), מניות "
                               "אמריקאיות בלבד, מתוך רשימה של כ-10 משקיעים. לא ייעוץ השקעות.")
                else:
                    st.caption("_אף אחד מהמשקיעים ברשימה לא מחזיק במניה הזו "
                               "(או שהיא לא נסחרת בארה״ב)._")
            else:
                st.caption("בדיקה מול כ-10 משקיעים ידועים (באפט, אקמן, ברי ועוד) לפי "
                           "דוח ה-13F האחרון שלהם. מניות אמריקאיות בלבד.")

        with st.expander("פעולות אנליסטים אחרונות", expanded=False):
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
                    st.write(f"- **{a['date']}** {a['firm']}: {verb} ({grade}){target}")
            else:
                st.caption("_אין פעולות אנליסטים אחרונות._")

        # Debug panel: hidden unless the address ends with ?debug=1.
        if st.query_params.get("debug") == "1":
            with st.expander("🔧 Debug details", expanded=False):
                st.write(f"**Price came from:** `{quote.price_source}`")
                st.write(f"**Previous close came from:** "
                         f"`{quote.prev_source or 'not available'}`")
                st.write("**Price candidates tried:**")
                st.write(quote.sources.get("price_candidates", {}))
                st.write("**Previous-close candidates tried:**")
                st.write(quote.sources.get("prev_candidates", {}))
                if company is not None and getattr(company, "source_note", ""):
                    st.write(f"**Company fundamentals via:** `{company.source_note}`")


# At the very end of the run, save the watchlist to the browser if it changed.
# (Callbacks set "_wl_dirty"; we persist once here so the write happens last.)
if st.session_state.pop("_wl_dirty", False):
    persist_watchlist(ls)
if st.session_state.pop("_trades_dirty", False):
    persist_trades(ls)
if st.session_state.pop("_bk_dirty", False) and ls is not None:
    try:
        ls.setItem(LAST_BACKUP_KEY, st.session_state.get("last_backup") or "",
                   key="last_backup_persist")
    except Exception:
        pass
