"""
universe.py — the stock UNIVERSE for the opportunity screener (Stage 1).

The single source of truth for WHICH tickers the screener considers, tagged by
theme and region. Pure Python (no Streamlit), so the later scan job imports it
directly. NOTHING here scans or scores — this is just the curated, verified list.

Two parts:
  1) CORE — US large-caps: the current S&P 500 constituents fetched LIVE from
     Wikipedia, Yahoo-normalized (class shares: BRK.B -> BRK-B). A hardcoded
     mega-cap FALLBACK is used ONLY if the live fetch fails, so a build never
     breaks. Every core ticker is theme="core", region="US".
  2) THEMES — hand-curated ticker lists per theme (no clean free theme lists
     exist, so this is expected). The `israel` theme REUSES the verified .TA
     tickers from engine.HEBREW_ALIASES (region="IL"); all others region="US".

A ticker may appear in core AND themes (e.g. NVDA in core + ai + semiconductors);
get_universe() merges duplicates so each symbol is listed once with all its tags.

================ HOW TO ADD A TICKER TO A THEME ================
Every theme below is a plain Python list. To add a ticker:
  * find the theme's list in THEMES and add one line, e.g.  "NVDA",
    (US tickers in Yahoo format; class shares use a dash, e.g. "BRK-B").
  * for the `israel` theme, add the Hebrew alias to engine.HEBREW_ALIASES instead
    (this theme is derived from there, so it never needs editing here).
Then run  verify_universe()  to confirm the new ticker resolves live on Yahoo.
To add a whole NEW theme: add a  "theme_name": [ ... ]  entry to THEMES (and a
REGION_BY_THEME entry if it isn't US).
===============================================================
"""
import engine  # reuse HEBREW_ALIASES (.TA tickers) + the live price check; pure Python

WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Used ONLY if the live S&P 500 fetch fails — a build must never break.
CORE_FALLBACK = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "BRK-B", "TSLA",
    "AVGO", "JPM", "LLY", "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "COST",
    "ABBV", "WMT", "MRK", "ORCL", "CVX", "KO", "PEP", "BAC", "ADBE", "CRM",
    "AMD", "NFLX", "TMO", "ACN", "MCD",
]

# --- Hand-curated theme lists (one ticker per line; add/remove freely) --------
THEMES = {
    "quantum": [
        "IONQ", "RGTI", "QBTS", "QUBT", "ARQQ", "LAES", "IBM", "GOOGL",
        "MSFT", "HON", "NVDA", "QCOM", "FORM", "AMAT",
    ],
    "semiconductors": [
        "NVDA", "AMD", "INTC", "AVGO", "QCOM", "TXN", "MU", "AMAT", "LRCX",
        "KLAC", "ADI", "MRVL", "NXPI", "MCHP", "ON", "ASML", "TSM", "STM",
        "SWKS", "QRVO", "MPWR", "ENTG", "TER", "ARM", "SMCI", "GFS", "UMC", "COHR",
    ],
    "ai": [
        "NVDA", "MSFT", "GOOGL", "META", "AMZN", "AMD", "PLTR", "SNOW", "CRM",
        "NOW", "AI", "PATH", "AVGO", "ARM", "SMCI", "DELL", "ANET", "MRVL",
        "ADBE", "IBM", "ORCL", "BBAI", "SOUN", "TSM",
    ],
    "storage_cloud": [
        "AMZN", "MSFT", "GOOGL", "SNOW", "NET", "DDOG", "MDB", "NTAP",
        "WDC", "STX", "ORCL", "IBM", "AKAM", "FSLY", "BOX", "DBX", "ZS", "CRM",
        "NOW",
    ],
    "healthcare_biotech": [
        "LLY", "JNJ", "UNH", "MRK", "ABBV", "PFE", "TMO", "ABT", "AMGN", "GILD",
        "REGN", "VRTX", "BIIB", "MRNA", "ISRG", "DHR", "BMY", "MDT", "SYK",
        "BSX", "ZTS", "HCA", "CI", "ELV", "CVS",
    ],
    "real_estate_reits": [
        "AMT", "PLD", "EQIX", "CCI", "PSA", "O", "SPG", "WELL", "DLR", "VICI",
        "SBAC", "EXR", "AVB", "EQR", "INVH", "ARE", "VTR", "MAA", "ESS", "KIM",
    ],
    "defense": [
        "LMT", "RTX", "NOC", "GD", "BA", "LHX", "HII", "TXT", "LDOS", "HWM",
        "AXON", "KTOS", "AVAV", "TDG", "CW",
    ],
    "energy": [
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB",
        "KMI", "OKE", "DVN", "HAL", "BKR", "FANG", "TRGP", "LNG", "ET",
        "EPD", "MPLX", "CTRA", "APA", "TPL",
    ],
    "cybersecurity": [
        # Removed (delisted): CYBR (CyberArk -> Palo Alto/PANW),
        # JNPR (Juniper -> HPE). PANW covers the gap.
        "PANW", "CRWD", "FTNT", "ZS", "S", "OKTA", "NET", "AKAM",
        "QLYS", "RPD", "TENB", "VRNS", "CHKP", "GEN", "FFIV", "NTCT",
    ],
    "fintech_payments": [
        # FISV (Fiserv): Yahoo serves it as FISV here, not its newer "FI" ticker.
        "V", "MA", "PYPL", "XYZ", "FISV", "GPN", "AXP", "COF", "SOFI", "AFRM",
        "UPST", "COIN", "HOOD", "NU", "TOST", "BILL", "FOUR", "WU", "GLOB", "MELI",
    ],
    "ev_auto": [
        "TSLA", "RIVN", "LCID", "GM", "F", "NIO", "LI", "XPEV", "BYDDY", "STLA",
        "TM", "HMC", "PSNY", "CHPT", "QS", "LAC",
    ],
    # `israel` is filled below from engine.HEBREW_ALIASES (verified .TA tickers).
    "israel": [],
}

# Themes whose tickers are NOT US-listed. Everything else defaults to "US".
REGION_BY_THEME = {"israel": "IL"}

# Reuse the already-verified .TA tickers from the Hebrew-alias map so we don't
# duplicate or re-verify them here.
ISRAEL_TICKERS = sorted({ticker for (ticker, _name) in engine.HEBREW_ALIASES.values()
                         if ticker.upper().endswith(".TA")})
THEMES["israel"] = ISRAEL_TICKERS


def normalize_symbol(symbol: str) -> str:
    """Yahoo-normalize a symbol. Class-share dots become dashes (BRK.B -> BRK-B),
    but a `.TA` suffix (Tel-Aviv) is preserved."""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    if s.endswith(".TA"):
        return s
    return s.replace(".", "-")


def fetch_sp500_symbols():
    """LIVE: current S&P 500 constituent symbols from Wikipedia, Yahoo-normalized.
    Raises on any failure (the caller falls back).

    Fetched via `requests` (certifi CA bundle + a real User-Agent) rather than
    pandas' built-in urllib, which can hit stale-OS-cert / UA-blocking issues."""
    import io
    import requests
    import pandas as pd
    headers = {"User-Agent": "Mozilla/5.0 (compatible; StockApp universe builder)"}
    response = requests.get(WIKIPEDIA_SP500_URL, headers=headers, timeout=20)
    response.raise_for_status()
    tables = pd.read_html(io.StringIO(response.text))
    # The constituents table is the one with a 'Symbol' column.
    constituents = next(t for t in tables if "Symbol" in t.columns)
    syms = [normalize_symbol(s) for s in constituents["Symbol"].astype(str)]
    return [s for s in syms if s]


def get_core_symbols():
    """The S&P 500 (live) or the hardcoded fallback if the fetch fails/looks wrong."""
    try:
        syms = fetch_sp500_symbols()
        if syms and len(syms) > 100:  # sanity: a real S&P pull is ~500
            return syms
    except Exception:
        pass
    return list(CORE_FALLBACK)


def build_universe(core_symbols=None):
    """Build the merged tag map: symbol -> {"regions": set, "themes": set}.

    Pass `core_symbols` to skip the live S&P fetch (used by tests). Otherwise the
    core is fetched live (with fallback)."""
    core = core_symbols if core_symbols is not None else get_core_symbols()
    tags = {}

    def add(symbol, region, theme):
        sym = (symbol or "").strip().upper()
        if not sym:
            return
        entry = tags.setdefault(sym, {"regions": set(), "themes": set()})
        entry["regions"].add(region)
        entry["themes"].add(theme)

    for sym in core:
        add(sym, "US", "core")
    for theme, syms in THEMES.items():
        region = REGION_BY_THEME.get(theme, "US")
        for sym in syms:
            add(sym, region, theme)
    return tags


def get_universe(core_symbols=None):
    """All unique tickers as a sorted list of
    {"symbol", "regions": [...], "themes": [...]} (duplicates merged)."""
    tags = build_universe(core_symbols)
    return [{"symbol": sym,
             "regions": sorted(meta["regions"]),
             "themes": sorted(meta["themes"])}
            for sym, meta in sorted(tags.items())]


def get_themes():
    """theme -> sorted, de-duplicated ticker list."""
    return {theme: sorted({s.strip().upper() for s in syms if s and s.strip()})
            for theme, syms in THEMES.items()}


def list_themes():
    """[(theme, ticker_count), ...] for a quick overview."""
    return [(theme, len(syms)) for theme, syms in get_themes().items()]


def _resolves(symbol):
    """Fast live check: does `symbol` have a usable Yahoo price? (fast_info only,
    so it's quick across hundreds of tickers)."""
    try:
        import yfinance as yf
        price = engine._safe(yf.Ticker(symbol).fast_info, "last_price")
        return engine._is_number(price) and float(price) > 0
    except Exception:
        return False


def verify_universe(check=None, core_symbols=None):
    """LIVE guard: confirm every ticker (core + themes) resolves on Yahoo.

    Returns (total, verified, failures) where failures is a list of
    (symbol, [sources]) — sources being 'core' and/or theme names. `check` is the
    per-symbol resolver (injectable for tests); defaults to a live Yahoo check.
    """
    resolver = check or _resolves
    tags = build_universe(core_symbols)
    failures = []
    verified = 0
    for sym, meta in sorted(tags.items()):
        if resolver(sym):
            verified += 1
        else:
            failures.append((sym, sorted(meta["themes"])))
    return len(tags), verified, failures
