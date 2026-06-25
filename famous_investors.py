"""
famous_investors.py — which well-known investors hold a given stock, from REAL
SEC 13F filings (via edgartools). No LLM, no guessing — only what funds actually
report to the SEC each quarter.

No Streamlit here, so this stays importable/testable on its own. The SEC fair-use
identity string (name + email — NOT a secret) is passed in by the caller.

Design:
- FAMOUS_INVESTORS: a curated list of {name, fund, cik}. Every CIK was verified
  against LIVE EDGAR (entity name + a recent 13F-HR filing) before shipping.
- get_famous_holders(symbol, identity): for each fund, fetch its LATEST 13F
  holdings and check whether the stock appears (matched by the holdings' Ticker
  column, which edgartools derives from CUSIP). Per-fund holdings are cached
  in-module keyed by CIK (13F is quarterly, so refetching is wasteful and risks
  SEC rate limits). Any fund that errors or has no recent filing is skipped
  quietly; a total failure returns an empty list, never an exception.
- Non-US stocks (e.g. .TA tickers) simply won't appear in any 13F -> empty list,
  which is EXPECTED, not an error.
"""
import logging
import time

logger = logging.getLogger(__name__)

# Curated, SEC-verified 13F filers. CIK + latest 13F-HR confirmed live against
# EDGAR (see the STEP 0 verification). Entity names in comments are the EDGAR
# filer names backing each well-known investor.
FAMOUS_INVESTORS = [
    {"name": "Warren Buffett", "fund": "Berkshire Hathaway", "cik": 1067983},
    {"name": "Bill Ackman", "fund": "Pershing Square Capital Management", "cik": 1336528},
    {"name": "Michael Burry", "fund": "Scion Asset Management", "cik": 1649339},
    {"name": "Ray Dalio", "fund": "Bridgewater Associates", "cik": 1350694},
    {"name": "David Tepper", "fund": "Appaloosa Management", "cik": 1656456},  # Appaloosa LP
    {"name": "Carl Icahn", "fund": "Icahn Capital", "cik": 921669},           # Icahn Carl C
    {"name": "Terry Smith", "fund": "Fundsmith", "cik": 1569205},             # Fundsmith LLP
    {"name": "Chase Coleman", "fund": "Tiger Global Management", "cik": 1167483},
    {"name": "Stanley Druckenmiller", "fund": "Duquesne Family Office", "cik": 1536411},
    {"name": "Howard Marks", "fund": "Oaktree Capital Management", "cik": 949509},  # Oaktree Capital Management LP
]

# Polite spacing between SEC calls (edgartools also rate-limits internally).
_SEC_DELAY_S = 0.10

# In-module cache of each fund's latest holdings: cik -> (period, infotable_df).
# Persists across Streamlit reruns (the module stays imported), so a fund is
# fetched at most once per process — the key performance/rate-limit lever.
_HOLDINGS_CACHE = {}


def _to_number(value):
    """Best-effort numeric coercion for a holdings cell, else None."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # reject NaN


def _sum_col(df, col):
    """Sum a numeric column across the matched rows (share classes), or None."""
    if col not in df.columns:
        return None
    total, seen = 0.0, False
    for v in df[col]:
        n = _to_number(v)
        if n is not None:
            total += n
            seen = True
    return total if seen else None


def _fund_holdings(cik, identity):
    """Return (period, infotable_df) for a fund's LATEST 13F-HR, cached in-module.
    (period, None) when there's nothing usable. Raises on hard EDGAR errors so the
    caller can skip this fund — the per-fund loop swallows it."""
    if cik in _HOLDINGS_CACHE:
        return _HOLDINGS_CACHE[cik]

    import edgar  # lazy: keeps module import cheap and offline-safe
    edgar.set_identity(identity)

    filings = edgar.Company(cik).get_filings(form="13F-HR")
    if filings is None or len(filings) == 0:
        _HOLDINGS_CACHE[cik] = (None, None)
        return None, None

    latest = filings.latest()
    # "As of" period the filing reports on (quarter end); fall back to filing date.
    period = (str(getattr(latest, "period_of_report", "") or "")
              or str(getattr(latest, "filing_date", "") or ""))
    infotable = latest.obj().infotable
    _HOLDINGS_CACHE[cik] = (period, infotable)
    time.sleep(_SEC_DELAY_S)  # polite spacing — only on an actual SEC fetch
    return period, infotable


def get_famous_holders(symbol, identity):
    """
    The curated famous investors who report holding `symbol`, from their latest
    SEC 13F-HR. Returns a list of dicts: {name, fund, shares, value, period}.

    Empty list when no tracked investor reports it (incl. non-US tickers, which
    never appear in 13Fs) or on any failure. Never raises.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []

    results = []
    for investor in FAMOUS_INVESTORS:
        try:
            period, infotable = _fund_holdings(investor["cik"], identity)
            if infotable is None or getattr(infotable, "empty", True):
                continue
            if "Ticker" not in infotable.columns:
                continue
            tickers = infotable["Ticker"].astype(str).str.upper().str.strip()
            matched = infotable[tickers == sym]
            if matched.empty:
                continue
            results.append({
                "name": investor["name"],
                "fund": investor["fund"],
                "shares": _sum_col(matched, "SharesPrnAmount"),
                "value": _sum_col(matched, "Value"),
                "period": period or "",
            })
        except Exception as error:
            # One bad fund must never sink the whole result.
            logger.warning("famous_investors: %s (cik %s) failed: %s",
                           investor.get("fund"), investor.get("cik"), error)
            continue
    return results
