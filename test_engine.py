"""
test_engine.py — simple checks for engine.py.

Run it with a single command (from the project folder, with the venv active
or using the venv's python):

    python test_engine.py

It calls the real engine against a few well-known tickers and a deliberately
fake one, then prints a clear PASS/FAIL for each. The script exits with code 0
if everything passed, or 1 if anything failed (handy for automation).

Note: this hits the live Yahoo Finance service, so you need an internet
connection and an English (non-Hebrew) folder path for it to work locally.
"""

import sys

from engine import (
    get_stock_quote,
    get_price_history,
    get_company_metrics,
    get_stock_technicals,
    RANGES,
)

# Well-known tickers we expect to resolve to a real company + positive price.
VALID_TICKERS = ["AAPL", "MSFT", "TEVA"]

# A symbol that clearly does not exist — the engine should signal "not found".
INVALID_TICKER = "NOTAREAL123"


def check(description, test_function):
    """
    Run one test. Print PASS if it doesn't raise, FAIL (with the reason) if it
    does. Returns True/False so we can count how many passed.
    """
    try:
        test_function()
        print(f"PASS: {description}")
        return True
    except AssertionError as error:
        print(f"FAIL: {description} -> {error}")
        return False
    except Exception as error:  # e.g. a network problem
        print(f"FAIL: {description} -> unexpected error: {error}")
        return False


def expect_valid(symbol):
    """A real ticker should be found, with a sensible name and positive price."""
    quote = get_stock_quote(symbol)

    assert quote.found, f"expected found=True for {symbol}"
    assert isinstance(quote.name, str) and quote.name.strip(), \
        f"expected a company name for {symbol}, got {quote.name!r}"
    assert isinstance(quote.price, float) and quote.price > 0, \
        f"expected a positive price for {symbol}, got {quote.price!r}"

    # Show what we actually got, so the output is informative.
    print(f"      {symbol}: {quote.name} @ {quote.price:,.2f} {quote.currency} "
          f"(price via {quote.price_source})")


def expect_not_found(symbol):
    """A fake ticker should come back with the clear 'not found' signal."""
    quote = get_stock_quote(symbol)
    assert not quote.found, \
        f"expected found=False for {symbol}, but got {quote.name!r} @ {quote.price}"


# Expected columns for any history table.
HISTORY_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]


def expect_history(symbol, range_key):
    """A real ticker + range should return a non-empty, well-formed table."""
    history = get_price_history(symbol, range_key)

    assert history.found, \
        f"expected history for {symbol} {range_key}, reason: {history.reason!r}"
    assert history.data is not None and not history.data.empty, \
        f"expected non-empty data for {symbol} {range_key}"
    for column in HISTORY_COLUMNS:
        assert column in history.data.columns, \
            f"missing column {column!r} for {symbol} {range_key}"

    rows = len(history.data)
    print(f"      {symbol} {range_key}: {rows} rows "
          f"(period={history.period}, interval={history.interval})")


def expect_history_not_found(symbol, range_key):
    """A fake ticker (or bad range) should report no data, not crash."""
    history = get_price_history(symbol, range_key)
    assert not history.found, \
        f"expected no history for {symbol} {range_key}, but got data"


# Keys every company / technicals result must expose (values may be n/a).
COMPANY_KEYS = ["market_cap", "pe", "forward_pe", "eps", "revenue",
                "profit_margin", "dividend_yield", "debt_to_equity",
                "free_cash_flow", "next_earnings", "sector", "industry"]
TECH_KEYS = ["week52_high", "week52_low", "ma50", "ma200", "rsi",
             "beta", "avg_volume"]


def expect_company_metrics(symbol):
    """Company metrics should be found, expose all keys, and have a sane mkt cap."""
    group = get_company_metrics(symbol)
    assert group.found, f"expected company metrics for {symbol}"
    for key in COMPANY_KEYS:
        assert key in group.metrics, f"missing company key '{key}' for {symbol}"

    mc = group.metrics["market_cap"]
    if mc.available:
        assert isinstance(mc.value, (int, float)) and mc.value > 0, \
            f"market cap should be positive, got {mc.value!r}"

    available = sum(1 for k in COMPANY_KEYS if group.metrics[k].available)
    print(f"      {symbol} company: {available}/{len(COMPANY_KEYS)} fields available")


def expect_stock_technicals(symbol):
    """Technicals should be found, expose all keys, and have sane MA/RSI."""
    group = get_stock_technicals(symbol)
    assert group.found, f"expected technicals for {symbol}"
    for key in TECH_KEYS:
        assert key in group.metrics, f"missing tech key '{key}' for {symbol}"

    ma50 = group.metrics["ma50"]
    if ma50.available:
        assert ma50.value > 0, f"50-day MA should be positive, got {ma50.value!r}"

    rsi = group.metrics["rsi"]
    if rsi.available:
        assert 0 <= rsi.value <= 100, f"RSI must be 0..100, got {rsi.value!r}"

    available = sum(1 for k in TECH_KEYS if group.metrics[k].available)
    print(f"      {symbol} technicals: {available}/{len(TECH_KEYS)} fields available")


def expect_metrics_not_found(symbol):
    """A fake ticker must return not-found for both metric groups, no crash."""
    company = get_company_metrics(symbol)
    technicals = get_stock_technicals(symbol)
    assert not company.found, f"expected company not-found for {symbol}"
    assert not technicals.found, f"expected technicals not-found for {symbol}"


def main():
    print("Running engine tests against live Yahoo Finance data...\n")

    results = []
    for symbol in VALID_TICKERS:
        results.append(check(f"{symbol} returns a valid quote",
                             lambda s=symbol: expect_valid(s)))
    results.append(check(f"{INVALID_TICKER} is reported as not found",
                         lambda: expect_not_found(INVALID_TICKER)))

    # Price history: test the reliable daily/weekly ranges. We skip 1D/1W here
    # because intraday data is often empty on the free tier (the engine handles
    # that gracefully, but it would make this test flaky).
    for range_key in ["1M", "6M", "1Y", "5Y"]:
        results.append(check(f"AAPL {range_key} history returns data",
                             lambda r=range_key: expect_history("AAPL", r)))

    # A fake ticker should report "no data", not crash.
    results.append(check(f"{INVALID_TICKER} 1M history is reported as no-data",
                         lambda: expect_history_not_found(INVALID_TICKER, "1M")))
    # An unknown range key should also be handled gracefully.
    results.append(check("an unknown range key is reported as no-data",
                         lambda: expect_history_not_found("AAPL", "99X")))

    # Step 3: company metrics + stock technicals for a couple of real tickers.
    for symbol in ["AAPL", "TEVA"]:
        results.append(check(f"{symbol} company metrics present",
                             lambda s=symbol: expect_company_metrics(s)))
        results.append(check(f"{symbol} stock technicals present",
                             lambda s=symbol: expect_stock_technicals(s)))

    # A fake ticker must return not-found for the metric groups too.
    results.append(check(f"{INVALID_TICKER} metrics are reported as not found",
                         lambda: expect_metrics_not_found(INVALID_TICKER)))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} tests passed.")

    # Plain ASCII on purpose: emoji can crash on Windows consoles whose
    # encoding (cp1252 / cp1255) can't represent them.
    if passed == total:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
