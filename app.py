"""
Stock Analysis App — Streamlit UI (display layer only).

All the data logic lives in engine.py (pure Python). This file just:
  1. draws the page,
  2. asks the engine for a quote,
  3. shows the result (or a friendly "couldn't find" message).

Run locally with:
    streamlit run app.py
"""

import hmac
import html
import json
import os

import altair as alt  # charting library that ships with Streamlit
import pandas as pd
import streamlit as st

import engine  # our pure-Python data engine (no Streamlit inside it)
import gemini_helper  # OPTIONAL Gemini explanation layer (no-op without a key)
import famous_investors  # famous-investor 13F holdings from SEC EDGAR (no key)

# Basic page configuration (title shown in the browser tab, etc.).
st.set_page_config(page_title="המניות שלי", page_icon="📈")


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

# --- Look & feel (redesign Stage 2: mockup "המניות שלי – הדמיה", items 5-8) ---
# PURE STYLING. The colors come from .streamlit/config.toml (dark theme); this
# CSS adds the Hebrew right-to-left layout, the mockup's fonts and rounded
# "card" surfaces, and keeps numbers / tickers / charts left-to-right.
# Streamlit's internal markup can change between versions, which is why
# requirements.txt pins the Streamlit version.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;700&family=Assistant:wght@400;600;700&display=swap');
    html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp input,
    .stApp button, .stApp textarea {
        font-family: "Assistant", "Rubik", system-ui, sans-serif;
    }
    .stApp h1, .stApp h2, .stApp h3, .stApp h4 {
        font-family: "Rubik", "Assistant", system-ui, sans-serif;
        letter-spacing: -0.01em;
    }
    /* Right-to-left for the CONTENT only (main column + sidebar contents).
       Setting it on the outer frame flips Streamlit's own layout and squeezes
       the sidebar on phones, so the frame itself stays left-to-right. */
    [data-testid="stMainBlockContainer"], [data-testid="stSidebarContent"],
    [data-testid="stSidebarUserContent"] { direction: rtl; }
    .stApp [data-testid="stMarkdownContainer"],
    .stApp [data-testid="stCaptionContainer"],
    .stApp [data-testid="stWidgetLabel"] { text-align: right; }
    .stApp input, .stApp textarea { text-align: right; }
    /* ...but charts, tables, code and numbers stay left-to-right. */
    .stApp [data-testid="stVegaLiteChart"], .stApp [data-testid="stArrowVegaLiteChart"],
    .stApp [data-testid="stDataFrame"], .stApp code,
    .stApp [data-testid="stMetricValue"] { direction: ltr; unicode-bidi: isolate; }
    .stApp [data-testid="stMetricValue"] { text-align: right; }

    /* Rounded "card" sections, like the mockup. */
    .stApp [data-testid="stExpander"] details {
        border-radius: 16px;
        background: var(--secondary-background-color, #151D2B);
    }
    .stApp [data-testid="stExpander"] summary { font-weight: 700; }
    .stApp [data-testid="stTabs"] button p { font-weight: 600; }
    .stApp [data-testid="stTextInput"] input { border-radius: 14px; }

    /* Compact metric tiles on a soft surface. */
    [data-testid="stMetric"] {
        background: rgba(255, 255, 255, 0.04);
        border-radius: 14px;
        padding: 0.45rem 0.75rem;
    }
    [data-testid="stMetricValue"] { font-size: 1.25rem; line-height: 1.3; font-weight: 700; }
    [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p { font-size: 0.85rem; }

    /* Big price line in the stock header. */
    .price-line { font-family: "Rubik", sans-serif; font-size: 2rem; font-weight: 600;
                  direction: ltr; unicode-bidi: isolate; display: inline-block; }
    .chip { display: inline-block; font-weight: 700; font-size: 0.9rem;
            padding: 2px 10px; border-radius: 999px; direction: ltr;
            unicode-bidi: isolate; margin-inline-start: 8px; vertical-align: middle; }
    .chip.up { color: #3CC98A; background: rgba(60, 201, 138, .14); }
    .chip.down { color: #FF6B6B; background: rgba(255, 107, 107, .14); }
    .ltr { direction: ltr; unicode-bidi: isolate; display: inline-block; }
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
    "6M": "חצי שנה (טווח קצר)",
    "1Y": "שנה (טווח בינוני)",
    "5Y": "5 שנים (טווח ארוך)",
}

# Hebrew display text for verdict and analyst labels. The engine keeps the
# English labels as its internal values; only what we SHOW is translated.
LABEL_HE = {
    "Sell": "מכירה", "Hold": "החזקה", "Buy": "קנייה", "Strong Buy": "קנייה חזקה",
    "Underperform": "תשואת חסר", "Strong Sell": "מכירה חזקה",
}


def he(label):
    """Hebrew display text for a verdict/analyst label (unknown -> as-is)."""
    return LABEL_HE.get(label, label) if label else label

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


MISSING = "—"  # shown wherever a value is missing (was "n/a")


def format_metric(metric, currency):
    """
    Turn a Metric into a display string, using its `fmt` hint. Missing values
    show as a dash ("—"). `currency` (e.g. "USD") is appended to money values.
    """
    if not metric.available:
        return MISSING

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
COLOR_LEGEND = ("🟢 חוזקה · 🟡 לשים לב · 🔴 חולשה. כללי אצבע לפי המספרים "
                "שמוצגים, **לא ייעוץ השקעות**.")


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


# The 6 key metrics shown up front (mockup item 6). The rest live under the
# "כל הנתונים" / "ניתוח טכני" tabs. (group, engine key, Hebrew label, Hebrew help)
KEY_METRICS = [
    ("company", "pe", "מכפיל רווח",
     "מחיר המניה חלקי הרווח השנתי למניה. גבוה = יקר, או שמצפים לצמיחה."),
    ("company", "revenue_growth", "צמיחת הכנסות",
     "בכמה גדלו המכירות לעומת שנה קודם."),
    ("company", "profit_margin", "שולי רווח",
     "כמה מכל שקל של מכירות נשאר כרווח נקי."),
    ("company", "debt_to_equity", "חוב להון",
     "כמה חוב יש לחברה ביחס להון שלה. נמוך = בטוח יותר."),
    ("company", "dividend_yield", "תשואת דיבידנד",
     "כמה דיבידנד משולם בשנה ביחס למחיר המניה."),
    ("technicals", "beta", "בטא",
     "כמה המניה זזה ביחס לשוק. 1 = כמו השוק, מעל 1 = תנודתית יותר."),
]


def key_metric_tiles(company, technicals, context=None):
    """(label, value, help) for the 6 key tiles. Pure: no Streamlit calls.
    A colored dot is prefixed when the engine has a good/neutral/bad rule;
    missing data shows MISSING ("—") with no dot."""
    groups = {"company": company, "technicals": technicals}
    tiles = []
    for group_name, key, label, help_text in KEY_METRICS:
        group = groups[group_name]
        metric = (group.metrics.get(key)
                  if (group is not None and group.found and group.metrics) else None)
        if metric is None or not metric.available:
            tiles.append((label, MISSING, help_text))
            continue
        status = engine.classify_metric(key, metric.value, context)
        if status in _STATUS_DOT:
            label = f"{_STATUS_DOT[status]} {label}"
        tiles.append((label, format_metric(metric, group.currency), help_text))
    return tiles


def render_key_metrics(company, technicals, context=None, columns_per_row=3):
    tiles = key_metric_tiles(company, technicals, context)
    for start in range(0, len(tiles), columns_per_row):
        columns = st.columns(columns_per_row)
        for column, (label, value, help_text) in zip(columns, tiles[start:start + columns_per_row]):
            column.metric(label, value, help=help_text)


# Colors for "up" / "down" from the mockup's dark palette.
UP_COLOR = "#3CC98A"
DOWN_COLOR = "#FF6B6B"


def make_line_chart(df, value_col="Close", value_title="מחיר"):
    """
    A clean line chart with a soft gradient fill underneath (mockup item 7).
    Green when the value ended the range at or above where it started, red
    when it fell. `df` needs a Date column and `value_col`.
    """
    values = df[value_col].dropna()
    rising = len(values) < 2 or float(values.iloc[-1]) >= float(values.iloc[0])
    color = UP_COLOR if rising else DOWN_COLOR
    # Fit the axis to the data (an area otherwise fills down to a far-away
    # "nice" round number and squashes the line against the top).
    if len(values):
        low, high = float(values.min()), float(values.max())
        pad = (high - low) * 0.08 or abs(high) * 0.02 or 1.0
        y_scale = alt.Scale(zero=False, nice=False, domain=[low - pad, high + pad])
    else:
        y_scale = alt.Scale(zero=False)
    area = (
        alt.Chart(df)
        .mark_area(
            clip=True,
            line={"color": color, "strokeWidth": 2},
            color=alt.Gradient(
                gradient="linear",
                stops=[alt.GradientStop(color=color + "00", offset=0),
                       alt.GradientStop(color=color + "55", offset=1)],
                x1=1, x2=1, y1=1, y2=0,
            ),
        )
        .encode(
            x=alt.X("Date:T", title=None),
            y=alt.Y(f"{value_col}:Q", title=value_title, scale=y_scale),
            tooltip=[alt.Tooltip("Date:T", title="תאריך"),
                     alt.Tooltip(f"{value_col}:Q", title=value_title, format=",.2f")],
        )
        .properties(height=300)
    )
    return area


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


def breakdown_html(breakdown):
    """The per-signal verdict breakdown as one left-to-right HTML list."""
    items = []
    for ws in breakdown:
        base_sign = "+" if ws.points > 0 else ""  # minus self-prints
        w_sign = "+" if ws.weighted > 0 else ""
        items.append(
            f"<li><b>{html.escape(ws.name)}</b> — {html.escape(str(ws.measured))}"
            f" | base {base_sign}{ws.points} × weight {ws.weight:.1f}"
            f" = <b>{w_sign}{ws.weighted:.1f}</b></li>")
    return ('<div dir="ltr" style="text-align:left"><ul style="margin:0">'
            + "".join(items) + "</ul></div>")


def debug_enabled():
    """True when the page URL has ?debug=1 (shows the Debug panel)."""
    try:
        return st.query_params.get("debug") == "1"
    except Exception:
        return False


def _goto(symbol):
    """A 'View' click pre-fills the search box on the next rerun."""
    st.session_state["pending_query"] = symbol


def render_watchlist_sidebar():
    """Draw the followed stocks in the sidebar with a compact quote, plus
    'View' and 'Remove' buttons."""
    with st.sidebar:
        st.markdown("### ⭐ במעקב")
        watchlist = st.session_state.get("watchlist", [])
        if not watchlist:
            st.caption("עוד אין מניות. חפש מניה ולחץ **הוסף למעקב**.")
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
            cols[0].button("הצג", key=f"wl_view_{sym}", on_click=_goto, args=(sym,))
            cols[1].button("הסר", key=f"wl_rm_{sym}",
                           on_click=_remove_watchlist, args=(sym,))
        st.caption("נשמר בדפדפן שלך.")


# --- Page content ---------------------------------------------------------

st.title("📈 המניות שלי")
st.write("הקלד סימול או שם של מניה ולחץ Enter.")

# Load the watchlist and draw it in the sidebar. A "View" click stores a
# pending query that we move into the search box BEFORE the widget is created.
ls = init_watchlist()
if "pending_query" in st.session_state:
    st.session_state["search_box"] = st.session_state.pop("pending_query")
render_watchlist_sidebar()

# A search box: accepts a ticker, a company name, or a Tel-Aviv (.TA) symbol.
# .strip() removes accidental spaces; pressing Enter reruns the script.
query = st.text_input(
    "חיפוש מניה: סימול, שם חברה או מניה בתל אביב (.TA)",
    placeholder="למשל: אפל, טבע, NVDA, TEVA.TA",
    key="search_box",
).strip()

# Israeli stocks use a .TA ticker on Yahoo (NOT their TASE security number).
st.caption("טיפ: מניות בתל אביב מחפשים לפי סימול עם `.TA`, למשל `TEVA.TA`, "
           "ולא לפי מספר נייר הערך.")

# Resolve the query to candidate tickers. If there's more than one match, let
# the user pick the right one (e.g. the Tel-Aviv AVIV.TA, not a US ETF).
symbol = None
if query:
    try:
        matches = load_matches(query)
    except Exception:
        matches = []

    if not matches:
        st.error("לא נמצאה מניה כזו. כדאי לבדוק את הסימול או השם.")
        st.caption("אם זו מניה ישראלית, חפש לפי הסימול עם **.TA** (למשל `AVIV.TA`). "
                   "מקור הנתונים החינמי (Yahoo Finance) לא מוצא מניות בתל אביב "
                   "לפי מספר נייר הערך.")
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
            "נמצאו כמה התאמות. לאיזו מניה התכוונת?",
            options=[m.symbol for m in matches],
            index=None,
            placeholder="בחר את המניה...",
            format_func=lambda s: labels.get(s, s),
        )
        if symbol is None:
            st.caption("בחר מניה מהרשימה כדי לראות את הניתוח.")

# Once we have a resolved symbol, run the full analysis on it.
if symbol:
    # Wrap the data fetch in try/except so a network glitch or unexpected
    # error shows a friendly message instead of a red crash screen.
    try:
        quote = load_quote(symbol)
    except Exception:
        quote = None

    if quote is None or not quote.found:
        st.error("לא הצלחנו לטעון את המניה. נסה מניה אחרת.")
    else:
        # ---- Pinned header (stays visible above the tabs) --------------
        # Company name, then the price large with today's change as a colored
        # chip (mockup item 5), then the exact symbol and exchange.
        st.subheader(quote.name)
        currency = quote.currency or ""
        if quote.change_abs is not None:
            cls = "up" if quote.change_abs >= 0 else "down"
            sign = "+" if quote.change_abs >= 0 else ""
            chip = (f'<span class="chip {cls}">{sign}{quote.change_abs:,.2f} '
                    f'({sign}{quote.change_pct:.2f}%)</span> '
                    '<span style="opacity:.7">היום</span>')
        else:
            chip = '<span style="opacity:.7">השינוי היומי לא זמין</span>'
        st.markdown(f'<span class="price-line">{quote.price:,.2f} {currency}</span>'
                    f'{chip}', unsafe_allow_html=True)
        exchange = quote.exchange or "בורסה לא ידועה"
        st.caption(f"{quote.symbol} · {exchange}")

        # Follow / unfollow this stock (saved in the browser).
        following = engine.in_watchlist(st.session_state.get("watchlist", []), symbol)
        st.button(
            "★ הסר ממעקב" if following else "⭐ הוסף למעקב",
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
            ["סקירה", "ניתוח AI", "גרפים", "כל הנתונים", "ניתוח טכני",
             "מי מחזיק"])

        # ======================= Overview =======================
        with tab_overview:
            with st.expander("השורה התחתונה", expanded=True):
                if verdict is None or not verdict.found:
                    st.info("אין חוות דעת זמינה למניה הזו.")
                else:
                    # Three colour-coded tiles, short -> long horizon.
                    columns = st.columns(len(engine.HORIZONS))
                    for column, horizon in zip(columns, engine.HORIZONS):
                        hv = verdict.horizons.get(horizon)
                        column.markdown(f"**{HORIZON_NAMES.get(horizon, horizon)}**")
                        if hv is None or not hv.enough_data:
                            column.info("אין מספיק נתונים")
                            continue
                        color = VERDICT_COLORS.get(hv.label, "gray")
                        column.markdown(f":{color}[**{he(hv.label)}**]")
                        # Score as a metric so it carries a "?" tooltip.
                        column.metric("Score", f"{hv.score:.0f}/100",
                                      help=engine.HELP_TEXTS.get("verdict_score"),
                                      label_visibility="collapsed")
                        column.progress(int(round(hv.score)))

                    st.caption(
                        "⚠️ חוות דעת אוטומטית לפי כללים ונתונים ציבוריים, **לא ייעוץ "
                        "השקעות.** שלושת הטווחים שוקלים את **אותם נתונים של היום** "
                        "בצורה שונה, והם **לא** תחזית."
                    )

                    # Transparent per-horizon breakdown. Rendered inline (Streamlit
                    # forbids an expander inside an expander), one tab per horizon.
                    st.markdown("**למה? (איך חושבה חוות הדעת)**")
                    horizon_tabs = st.tabs([HORIZON_NAMES.get(h, h)
                                            for h in engine.HORIZONS])
                    for htab, horizon in zip(horizon_tabs, engine.HORIZONS):
                        hv = verdict.horizons.get(horizon)
                        with htab:
                            if hv is None or not hv.enough_data:
                                st.info(hv.reason if hv else "אין מספיק נתונים.")
                                continue
                            st.caption(
                                f"ציון משוקלל {hv.score:.1f}/100 (50 = ניטרלי). "
                                "כל סימן: base (נקודות בסיס) × weight (משקל הטווח) = התרומה."
                            )
                            # The signal names/readings come from the engine in
                            # English, so this list is shown left-to-right as a
                            # block (mixing it into RTL Hebrew garbles the order).
                            st.markdown(breakdown_html(hv.breakdown),
                                        unsafe_allow_html=True)

            with st.expander("אנחנו מול וול סטריט", expanded=True):
                if analyst is None or not analyst.found or not analyst.has_coverage:
                    st.info("אין אנליסטים שמכסים את המניה (נפוץ במניות קטנות ובתל "
                            "אביב), אז אין למה להשוות.")
                else:
                    col_ours, col_street = st.columns(2)
                    with col_ours:
                        st.markdown("**חוות הדעת שלנו (שנה)**")
                        hv = (verdict.horizons.get("1Y")
                              if verdict is not None and verdict.found else None)
                        if hv is not None and hv.enough_data:
                            c = VERDICT_COLORS.get(hv.label, "gray")
                            st.markdown(f":{c}[**{he(hv.label)}**]")
                            st.metric("Score", f"{hv.score:.0f}/100",
                                      help=engine.HELP_TEXTS.get("verdict_score"),
                                      label_visibility="collapsed")
                        else:
                            st.markdown(MISSING)
                        st.caption("לפי כללים, ממחיר ונתוני החברה")
                    with col_street:
                        st.markdown("**קונצנזוס האנליסטים**")
                        c = ANALYST_COLORS.get(analyst.label, "gray")
                        st.markdown(f":{c}[**{he(analyst.label)}**]")
                        if analyst.mean is not None:
                            st.metric("Mean rating", f"{analyst.mean:.2f}/5",
                                      help=engine.HELP_TEXTS.get("analyst_mean"),
                                      label_visibility="collapsed")
                            st.caption(f"לפי {analyst.num_analysts or '?'} אנליסטים")

                    # Mean price target vs current price (implied upside / downside).
                    if analyst.target_mean and analyst.current_price:
                        cur = analyst.currency or ""
                        up = analyst.upside_pct or 0.0
                        arrow = "🔺" if up >= 0 else "🔻"
                        sign = "+" if up >= 0 else ""
                        st.write(
                            f"**יעד מחיר ממוצע:** {analyst.target_mean:,.2f} {cur} "
                            f"({arrow} {sign}{up:.1f}% מהמחיר הנוכחי "
                            f"{analyst.current_price:,.2f})"
                        )
                        if analyst.target_low and analyst.target_high:
                            st.caption(f"טווח היעדים של האנליסטים: "
                                       f"{analyst.target_low:,.2f} עד "
                                       f"{analyst.target_high:,.2f} {cur}")

                    # (Recent analyst actions now live in the Ownership tab.)

                    # ALWAYS say something about how we compare to the analysts.
                    divergence = engine.explain_divergence(verdict, analyst, "1Y")
                    if not divergence.diverges:
                        # Agreement -> a short positive note (never an empty section).
                        st.success("✅ " + (divergence.note or
                                   "חוות הדעת שלנו תואמת לקונצנזוס האנליסטים."))
                    else:
                        if divergence.direction == "analysts_more_bullish":
                            title = (f"⚖️ למה יש פער? האנליסטים ({he(divergence.analyst_label)}) "
                                     f"אופטימיים יותר מהתחזית שלנו לשנה "
                                     f"({he(divergence.our_label)})")
                            drivers_word = "מה מוריד את הציון שלנו"
                        else:
                            title = (f"⚖️ למה יש פער? התחזית שלנו לשנה ({he(divergence.our_label)}) "
                                     f"אופטימית יותר מהאנליסטים "
                                     f"({he(divergence.analyst_label)})")
                            drivers_word = "מה מעלה את הציון שלנו"
                        # Inline (an expander can't nest inside this one).
                        st.markdown(f"**{title}**")
                        st.write(divergence.note)
                        if divergence.drivers:
                            st.write(f"**{drivers_word}:**")
                            for name, measured, weighted in divergence.drivers:
                                st.write(f"- **{name}** — {measured} ({weighted:+.1f})")

                # Link out — per-site analyst scores are often paywalled.
                st.caption(
                    f"פירוט נוסף ב-[Yahoo Finance]"
                    f"(https://finance.yahoo.com/quote/{symbol}/analysis). "
                    "ציוני אנליסטים באתרים אחרים (כמו TipRanks) בדרך כלל בתשלום."
                )

            with st.expander("נתונים עיקריים", expanded=True):
                st.caption("6 הנתונים החשובים. לחץ על **?** להסבר. כל השאר בלשונית "
                           "**כל הנתונים**.")
                render_key_metrics(company, technicals, context=metric_context)
                st.caption(COLOR_LEGEND)

        # ======================= AI analysis =======================
        with tab_ai:
            # OPTIONAL; Gemini, button-triggered. We NEVER auto-call Gemini — the
            # user clicks a button. Each (symbol, depth) result is cached in
            # session_state so re-clicks / reruns don't re-call (protecting
            # free-tier quota). With no key the buttons are disabled (tooltip) and
            # nothing else appears; any failure renders a muted note, never raises.
            st.markdown("**🤖 ניתוח AI**")
            gemini_key = get_gemini_key()
            no_key = not gemini_key
            ai_cache = st.session_state.setdefault("_gemini_cache", {})
            ai_active = st.session_state.setdefault("_gemini_active_depth", {})

            disabled_help = "צריך מפתח Gemini כדי להפעיל" if no_key else None
            col_quick, col_deep = st.columns(2)
            if col_quick.button("⚡ הסבר קצר", key="ai_quick_btn", disabled=no_key,
                                help=disabled_help, width="stretch"):
                ai_active[symbol] = "quick"
            if col_deep.button("🔍 ניתוח מעמיק", key="ai_deep_btn", disabled=no_key,
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
                    st.caption("סיכום שנכתב על ידי AI מהנתונים שבלשוניות האחרות. "
                               "לא ייעוץ השקעות.")
                else:
                    # Only AFTER a click: a small muted note. Never auto, never crash.
                    st.caption("_ניתוח ה-AI לא זמין כרגע._")

        # ======================= Charts =======================
        with tab_charts:
            with st.expander("גרף מחיר", expanded=True):
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
                    "סוג גרף",
                    options=["Line", "Candlestick"],
                    format_func={"Line": "קו", "Candlestick": "נרות"}.get,
                    horizontal=True,
                )

                # Y-axis toggle: actual Price (default) vs % change from the start
                # of the selected range (normalised so the first point = 0%).
                as_percent = st.toggle(
                    "הצג כשינוי באחוזים מתחילת הטווח",
                    value=False,
                    help="מנרמל את הגרף כך שתחילת הטווח = 0%, ומראה את הרווח או "
                         "ההפסד באחוזים לאורך הטווח.",
                )

                # Fetch the candles for the chosen range (cached).
                history = load_history(symbol, range_key)

                if not history.found:
                    # Empty range (common for 1D/1W intraday on the free tier).
                    st.info(f"אין נתוני גרף לטווח {range_key}: {history.reason}")
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
                            st.caption("ℹ️ הנרות מראים מחיר (ציר שמאלי). הציר הימני "
                                       "מראה את השינוי המצטבר באחוזים מתחילת הטווח.")
                    elif as_percent:
                        # Line + % view: a normalised line (start of range = 0%).
                        pct_df = history.data[["Date"]].copy()
                        pct_df["Pct"] = engine.price_to_pct_change(
                            history.data["Close"], engine.first_close(history.data))
                        st.altair_chart(make_line_chart(pct_df, "Pct", "% שינוי"),
                                        width="stretch")
                    else:
                        # Clean line of closing prices, green/red by the range.
                        st.altair_chart(make_line_chart(history.data), width="stretch")

            with st.expander("נפח מסחר", expanded=False):
                # Same logic as before: only when there's real volume for this range.
                has_volume = False
                if history.found and "Volume" in history.data.columns:
                    volumes = history.data["Volume"].dropna()
                    has_volume = len(volumes) > 0 and float(volumes.sum()) > 0
                if has_volume:
                    st.altair_chart(make_volume_chart(history.data), width="stretch")
                else:
                    st.caption("_אין נתוני נפח מסחר לטווח הזה._")

        # ======================= Fundamentals =======================
        with tab_fund:
            with st.expander("נתוני החברה", expanded=True):
                st.caption("העסק שמאחורי המניה. לחץ על **?** בכל נתון להסבר פשוט.")
                if company is not None and company.found and company.metrics:
                    st.caption(COLOR_LEGEND)
                    render_metrics(company, context=metric_context)
                else:
                    st.info("אין נתוני חברה זמינים למניה הזו.")

        # ======================= Technicals =======================
        with tab_tech:
            st.caption("איך מחיר המניה עצמו התנהג.")
            if technicals is not None and technicals.found and technicals.metrics:
                with st.expander("רמות מחיר, מגמה ומומנטום", expanded=True):
                    st.caption(COLOR_LEGEND)
                    render_metrics(technicals, keys=PRICE_TREND_KEYS,
                                   context=metric_context)
                with st.expander("נפח מסחר ולחץ קנייה/מכירה (הערכות)",
                                 expanded=False):
                    render_metrics(technicals, keys=PRESSURE_KEYS,
                                   context=metric_context)
                    st.caption(
                        "הערה: הנתונים החינמיים לא מפרידים בין נפח קנייה לנפח מכירה, "
                        "אז **OBV** ו-**Accumulation/Distribution** הם *הערכות* של לחץ "
                        "קנייה/מכירה לפי מחיר ונפח, ולא נתוני פקודות אמיתיים."
                    )
            else:
                st.info("אין נתונים טכניים זמינים למניה הזו.")

        # ======================= Ownership =======================
        with tab_own:
            with st.expander("גופים מוסדיים בולטים", expanded=True):
                if holders is None or not holders.found:
                    st.caption("_אין נתונים על מחזיקים מוסדיים במניה הזו._")
                else:
                    if holders.institutions_pct_held is not None:
                        pct = holders.institutions_pct_held * 100
                        across = (f" ({holders.institutions_count:,} גופים)"
                                  if holders.institutions_count else "")
                        st.markdown(f"**{pct:.1f}%** מהמניות מוחזקות על ידי גופים "
                                    f"מוסדיים{across}.")

                    if holders.top_holders:
                        table = pd.DataFrame([{
                            "מחזיק": h.name,
                            "מניות": f"{h.shares:,}" if h.shares is not None else MISSING,
                            "% מוחזק": (f"{h.pct_held * 100:.2f}%"
                                        if h.pct_held is not None else MISSING),
                            "תאריך דיווח": h.date_reported or MISSING,
                        } for h in holders.top_holders])
                        st.dataframe(table, hide_index=True, width="stretch")
                        st.caption(
                            "המחזיקים המוסדיים הגדולים לפי דוחות **13F** רבעוניים "
                            "(באיחור של עד כ-45 יום), בעיקר מנהלי השקעות גדולים "
                            "(כמו Vanguard ו-BlackRock). זה **לא** בזמן אמת, ו**לא "
                            "ייעוץ השקעות**."
                        )
                    else:
                        # Summary % available but no named-holder breakdown (common
                        # for smaller / foreign listings).
                        st.caption(
                            "סיכום האחזקה המוסדית לפי דוחות **13F** רבעוניים (באיחור "
                            "של עד כ-45 יום). פירוט לפי שמות לא זמין למניה הזו. **לא "
                            "ייעוץ השקעות**."
                        )

            # Famous investors (real SEC 13F data; button-triggered so we never
            # run ~10 EDGAR calls on page load). Cached per symbol for the session.
            with st.expander("משקיעים מפורסמים שמחזיקים", expanded=False):
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
                            "משקיע": h["name"],
                            "קרן": h["fund"],
                            "מניות": (f"{h['shares']:,.0f}"
                                      if h.get("shares") is not None else MISSING),
                            "שווי": (f"{h['value']:,.0f}"
                                     if h.get("value") is not None else MISSING),
                            "% מהתיק": (
                                f"{h['pct_of_portfolio']:.1f}%"
                                if h.get("pct_of_portfolio") is not None else MISSING),
                            "מול הדוח הקודם": _famous_change_str(h),
                            "נכון ל": h.get("period") or MISSING,
                        } for h in famous])
                        st.dataframe(ftable, hide_index=True, width="stretch")
                        st.caption(
                            "לפי דוחות **13F** רבעוניים ב-SEC (באיחור של עד כ-45 יום), "
                            "**מניות אמריקאיות בלבד**, מתוך רשימה של כ-10 משקיעים. לא "
                            "רשימה מלאה, לא בזמן אמת, ו**לא ייעוץ השקעות**. **% מהתיק** "
                            "ו**מול הדוח הקודם** מחושבים משני הדוחות האחרונים של כל קרן."
                        )
                    else:
                        st.caption("_אף אחד מהמשקיעים שבמעקב לא מדווח על אחזקה "
                                   "(או שזו לא מניה אמריקאית)._")
                else:
                    st.caption("בדיקה של כ-10 משקיעים מוכרים (באפט, אקמן, ברי…) "
                               "מול דוח ה-13F האחרון שלהם. מניות אמריקאיות בלבד.")

            with st.expander("שינויי המלצות אחרונים של אנליסטים",
                             expanded=False):
                if (analyst is not None and analyst.found
                        and getattr(analyst, "actions", None)):
                    for a in analyst.actions:
                        verb = engine.humanize_action(a["action"])
                        if a["from_grade"] and a["from_grade"] != a["to_grade"]:
                            grade = f"{a['from_grade']} -> {a['to_grade']}"
                        else:
                            grade = a["to_grade"] or "-"
                        target = (f" · יעד {a['price_target']:,.0f}"
                                  if a.get("price_target") else "")
                        st.write(f"- **{a['date']}** {a['firm']}: {verb} "
                                 f"({grade}){target}")
                else:
                    st.caption("_אין שינויי המלצות אחרונים._")

        # Debug panel (mockup item 8): hidden unless the URL has ?debug=1.
        # Open it to see WHICH source gave us the price and every raw value.
        if debug_enabled():
            with st.expander("🔧 Debug", expanded=False):
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
