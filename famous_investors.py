"""
famous_investors.py — which well-known investors hold a given stock, from REAL
SEC 13F filings (via edgartools). No LLM, no guessing — only what funds actually
report to the SEC each quarter.

For each holder we also derive, from the fund's own 13F totals:
- pct_of_portfolio: this position's value as a % of the fund's TOTAL 13F value
  (current filing),
- prev_pct: the same stock's % in the fund's PREVIOUS 13F filing (or None),
- direction: "up" | "down" | "flat" | "new" vs that previous filing.

No Streamlit here, so this stays importable/testable on its own. The SEC fair-use
identity string (name + email — NOT a secret) is passed in by the caller.

Design:
- FAMOUS_INVESTORS: a curated list of {name, fund, cik}. Every CIK was verified
  against LIVE EDGAR (entity name + a recent 13F-HR filing) before shipping.
- Per fund we fetch its TWO most recent 13F-HR filings (current + previous) and
  cache the parsed bundle in-module keyed by CIK (13F is quarterly, so refetching
  is wasteful and risks SEC rate limits). Any fund that errors or has no recent
  filing is skipped quietly; a total failure returns an empty list, never raises.
- Non-US stocks (e.g. .TA tickers) won't appear in any 13F -> empty list, which
  is EXPECTED, not an error.
"""
import logging
import time

logger = logging.getLogger(__name__)

# Curated, SEC-verified 13F filers. CIK + latest 13F-HR confirmed live against
# EDGAR. Entity names in comments are the EDGAR filer names.
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

# A position is "flat" if its portfolio weight moved by <= this many percentage
# points vs the previous filing.
_FLAT_PP = 0.1

# In-module cache: cik -> parsed two-filing bundle. Persists across Streamlit
# reruns (the module stays imported), so a fund is fetched at most once per
# process — the key performance / rate-limit lever.
_FUND_CACHE = {}


def _to_number(value):
    """Best-effort numeric coercion for a holdings cell, else None."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # reject NaN


def _sum_col(df, col):
    """Sum a numeric column across the given rows, or None if nothing usable."""
    if df is None or col not in getattr(df, "columns", []):
        return None
    total, seen = 0.0, False
    for v in df[col]:
        n = _to_number(v)
        if n is not None:
            total += n
            seen = True
    return total if seen else None


def _matched_rows(table, sym):
    """Rows of a 13F infotable whose Ticker == sym (upper/stripped), or None."""
    if table is None or getattr(table, "empty", True):
        return None
    if "Ticker" not in getattr(table, "columns", []):
        return None
    tickers = table["Ticker"].astype(str).str.upper().str.strip()
    matched = table[tickers == sym]
    return matched if not matched.empty else None


def _parse_filing(filing):
    """Return (period, total_value, infotable) for one 13F-HR filing."""
    period = (str(getattr(filing, "period_of_report", "") or "")
              or str(getattr(filing, "filing_date", "") or ""))
    obj = filing.obj()
    infotable = obj.infotable
    total = None
    tv = getattr(obj, "total_value", None)  # provided fund total (a Decimal)
    if tv is not None:
        try:
            total = float(tv)
        except (TypeError, ValueError):
            total = None
    if not total:  # fall back to summing the holdings' values
        total = _sum_col(infotable, "Value")
    return period, total, infotable


def _fund_data(cik, identity):
    """The fund's two most recent 13F-HR filings, parsed and cached by CIK.

    Returns a dict with cur_/prev_ (period, total, table). Missing previous filing
    -> prev_* are None. Raises on hard EDGAR errors so the caller can skip the fund.
    """
    if cik in _FUND_CACHE:
        return _FUND_CACHE[cik]

    import edgar  # lazy: keeps module import cheap and offline-safe
    edgar.set_identity(identity)

    data = {"cur_period": None, "cur_total": None, "cur_table": None,
            "prev_period": None, "prev_total": None, "prev_table": None}

    filings = edgar.Company(cik).get_filings(form="13F-HR")
    if filings is None or len(filings) == 0:
        _FUND_CACHE[cik] = data
        return data

    two = list(filings.head(2))  # newest first: [0] current, [1] previous
    if len(two) >= 1:
        data["cur_period"], data["cur_total"], data["cur_table"] = _parse_filing(two[0])
    if len(two) >= 2:
        try:
            data["prev_period"], data["prev_total"], data["prev_table"] = _parse_filing(two[1])
        except Exception as error:
            # Previous filing is optional — a parse failure just means no delta.
            logger.warning("famous_investors: prev filing for cik %s failed: %s",
                           cik, error)

    _FUND_CACHE[cik] = data
    time.sleep(_SEC_DELAY_S)  # polite spacing — only on an actual SEC fetch
    return data


def _direction(pct, prev_pct):
    """Classify the move in portfolio weight vs the previous filing."""
    if prev_pct is None:
        return "new"
    delta = pct - prev_pct
    if abs(delta) <= _FLAT_PP:
        return "flat"
    return "up" if delta > 0 else "down"


def get_famous_holders(symbol, identity):
    """
    The curated famous investors who report holding `symbol`, from their latest
    SEC 13F-HR. Each record: {name, fund, shares, value, period,
    pct_of_portfolio, prev_pct, direction}.

    Empty list when no tracked investor reports it (incl. non-US tickers) or on
    any failure. Never raises.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []

    results = []
    for investor in FAMOUS_INVESTORS:
        try:
            data = _fund_data(investor["cik"], identity)
            matched = _matched_rows(data["cur_table"], sym)
            if matched is None:
                continue  # not currently held by this fund

            value = _sum_col(matched, "Value")
            total = data["cur_total"]
            pct = (100.0 * value / total) if (value is not None and total) else None

            # Same stock's weight in the PREVIOUS filing (if held then).
            prev_pct = None
            prev_matched = _matched_rows(data["prev_table"], sym)
            if prev_matched is not None and data["prev_total"]:
                prev_value = _sum_col(prev_matched, "Value")
                if prev_value is not None:
                    prev_pct = 100.0 * prev_value / data["prev_total"]

            results.append({
                "name": investor["name"],
                "fund": investor["fund"],
                "shares": _sum_col(matched, "SharesPrnAmount"),
                "value": value,
                "period": data["cur_period"] or "",
                "pct_of_portfolio": pct,
                "prev_pct": prev_pct,
                "direction": _direction(pct, prev_pct) if pct is not None else None,
            })
        except Exception as error:
            # One bad fund must never sink the whole result.
            logger.warning("famous_investors: %s (cik %s) failed: %s",
                           investor.get("fund"), investor.get("cik"), error)
            continue
    return results
