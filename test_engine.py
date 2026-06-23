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

import pandas as pd

from engine import (
    get_stock_quote,
    get_price_history,
    get_company_metrics,
    get_stock_technicals,
    compute_verdict,
    find_tickers,
    VERDICT_LABELS,
    HORIZONS,
    RANGES,
    _volume_confirmation,
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
             "beta", "avg_volume",
             "macd", "macd_signal", "macd_hist", "macd_state",
             "bb_upper", "bb_middle", "bb_lower", "bb_state",
             "vol_recent", "vol_avg", "vol_move", "vol_confirm",
             "obv_value", "obv_trend", "ad_value", "ad_trend"]


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

    # Bollinger: upper must exceed lower, middle must sit between them.
    bb_upper = group.metrics["bb_upper"]
    bb_lower = group.metrics["bb_lower"]
    bb_middle = group.metrics["bb_middle"]
    if bb_upper.available and bb_lower.available:
        assert bb_upper.value > bb_lower.value, "Bollinger upper must exceed lower"
        if bb_middle.available:
            assert bb_lower.value <= bb_middle.value <= bb_upper.value, \
                "Bollinger middle must sit between the bands"

    # MACD histogram must equal macd line minus signal line.
    macd = group.metrics["macd"]
    macd_signal = group.metrics["macd_signal"]
    macd_hist = group.metrics["macd_hist"]
    if macd.available and macd_signal.available and macd_hist.available:
        assert abs(macd_hist.value - (macd.value - macd_signal.value)) < 1e-6, \
            "MACD histogram must equal macd - signal"

    # Volume indicators: trend/confirmation states must use the allowed vocab,
    # and the volume averages must be positive when present.
    for key in ("obv_trend", "ad_trend"):
        m = group.metrics[key]
        if m.available:
            assert m.value in ("rising", "falling", "flat"), \
                f"{key} has unexpected value {m.value!r}"
    vol_confirm = group.metrics["vol_confirm"]
    if vol_confirm.available:
        assert vol_confirm.value in ("confirmed", "unconfirmed", "neutral"), \
            f"vol_confirm has unexpected value {vol_confirm.value!r}"
    for key in ("vol_recent", "vol_avg"):
        m = group.metrics[key]
        if m.available:
            assert m.value > 0, f"{key} should be positive, got {m.value!r}"

    available = sum(1 for k in TECH_KEYS if group.metrics[k].available)
    print(f"      {symbol} technicals: {available}/{len(TECH_KEYS)} fields available")


def expect_metrics_not_found(symbol):
    """A fake ticker must return not-found for both metric groups, no crash."""
    company = get_company_metrics(symbol)
    technicals = get_stock_technicals(symbol)
    assert not company.found, f"expected company not-found for {symbol}"
    assert not technicals.found, f"expected technicals not-found for {symbol}"


def _weight_of(horizon_verdict, key):
    """Find the weight a horizon applied to a given signal key (or None)."""
    for ws in horizon_verdict.breakdown:
        if ws.key == key:
            return ws.weight
    return None


def expect_verdict(symbol):
    """A real ticker should produce all three horizons, each with a valid
    label, in-range score, and a weighted breakdown consistent with the score —
    and the horizons must re-weight the signals differently."""
    verdict = compute_verdict(symbol)

    assert verdict.found, f"expected a verdict result for {symbol}"
    assert set(verdict.horizons.keys()) == set(HORIZONS), \
        f"expected horizons {HORIZONS}, got {list(verdict.horizons)}"

    scores = {}
    for horizon in HORIZONS:
        hv = verdict.horizons[horizon]
        assert hv.enough_data, f"{symbol} {horizon}: not enough data ({hv.reason})"
        assert hv.label in VERDICT_LABELS, \
            f"{symbol} {horizon}: bad label {hv.label!r}"
        assert 0 <= hv.score <= 100, \
            f"{symbol} {horizon}: score out of range {hv.score}"
        assert hv.breakdown, f"{symbol} {horizon}: empty breakdown"

        # Each row's weighted value must equal points * weight.
        for ws in hv.breakdown:
            assert abs(ws.weighted - ws.points * ws.weight) < 1e-9, \
                f"{symbol} {horizon}: weighted != points*weight for {ws.key}"

        # The score direction must match the weighted sum of counted signals.
        weighted_sum = sum(ws.weighted for ws in hv.breakdown if ws.weight > 0)
        if weighted_sum > 0:
            assert hv.score > 50, f"{symbol} {horizon}: wsum>0 but score<=50"
        elif weighted_sum < 0:
            assert hv.score < 50, f"{symbol} {horizon}: wsum<0 but score>=50"
        else:
            assert abs(hv.score - 50) < 1e-9, \
                f"{symbol} {horizon}: wsum 0 but score != 50"
        scores[horizon] = hv.score

    # Horizons must re-weight differently: short-term cares about momentum more,
    # so RSI must weigh more for 6M than for 5Y. This proves independence.
    w6 = _weight_of(verdict.horizons["6M"], "rsi")
    w5 = _weight_of(verdict.horizons["5Y"], "rsi")
    if w6 is not None and w5 is not None:
        assert w6 > w5, f"expected RSI weight 6M({w6}) > 5Y({w5})"

    print(f"      {symbol}: "
          f"6M {verdict.horizons['6M'].label} ({scores['6M']:.0f}) | "
          f"1Y {verdict.horizons['1Y'].label} ({scores['1Y']:.0f}) | "
          f"5Y {verdict.horizons['5Y'].label} ({scores['5Y']:.0f})")


def expect_verdict_not_usable(symbol):
    """A fake ticker must be not-found, or have every horizon flagged
    not-enough-data — never a real label."""
    verdict = compute_verdict(symbol)
    if not verdict.found:
        return
    for horizon in HORIZONS:
        hv = verdict.horizons.get(horizon)
        assert hv is not None and not hv.enough_data, \
            f"expected no usable verdict for {symbol} at {horizon}"


def expect_company_resilient(symbol, min_fields=5):
    """Part A: a real ticker must NOT report 'not found', and should expose
    several real fundamental fields (the MSFT empty-.info bug)."""
    group = get_company_metrics(symbol)
    assert group.found, \
        f"{symbol}: company metrics reported not-found (Part A regression)"
    available = sum(1 for m in group.metrics.values() if m.available)
    assert available >= min_fields, \
        f"{symbol}: expected >= {min_fields} fundamentals, got {available}"
    print(f"      {symbol} company: {available} fundamental fields "
          f"(via {group.source_note})")


def expect_volume_signals_in_verdict(symbol):
    """The OBV and A/D signals should appear in the verdict, weighted more for
    the short (6M) horizon than the long (5Y) one."""
    verdict = compute_verdict(symbol)
    assert verdict.found, f"expected a verdict for {symbol}"
    keys_present = {s.key for s in verdict.signals}
    assert "obv" in keys_present, f"{symbol}: OBV signal missing from verdict"
    assert "ad" in keys_present, f"{symbol}: A/D signal missing from verdict"
    for key in ("obv", "ad"):
        w6 = _weight_of(verdict.horizons["6M"], key)
        w5 = _weight_of(verdict.horizons["5Y"], key)
        assert w6 is not None and w5 is not None and w6 > w5, \
            f"{symbol}: expected {key} weight 6M({w6}) > 5Y({w5})"
    vol_keys = sorted(k for k in keys_present if k in ("vol_confirm", "obv", "ad"))
    print(f"      {symbol} verdict volume signals: {vol_keys}")


def expect_find_includes_ta(query, expected_symbol, name_contains):
    """A query for an Israeli stock must surface its .TA ticker among matches."""
    matches = find_tickers(query)
    symbols = [m.symbol for m in matches]
    assert expected_symbol in symbols, \
        f"expected {expected_symbol} in matches for {query!r}, got {symbols}"
    match = next(m for m in matches if m.symbol == expected_symbol)
    assert name_contains.lower() in match.name.lower(), \
        f"unexpected name for {expected_symbol}: {match.name!r}"
    print(f"      {query!r} -> {symbols}")


def expect_find_number_unresolvable(number):
    """A bare TASE security number isn't resolvable via Yahoo -> no matches
    (so the UI can advise using the .TA ticker)."""
    matches = find_tickers(number)
    assert matches == [], \
        f"expected no matches for bare number {number!r}, got {[m.symbol for m in matches]}"


def expect_unconfirmed_move_logic():
    """Synthetic check (no network): a strong recent GAIN on BELOW-average
    volume must be flagged 'unconfirmed' — the basis for the -2 verdict rule."""
    # 60 days: flat, then a ~+6% rise over the last 5 days...
    closes = pd.Series([100.0] * 55 + [100.0, 101.0, 103.0, 104.0, 106.0])
    # ...but recent volume is far BELOW the longer-run average.
    volumes = pd.Series([1_000_000.0] * 55 + [200_000.0] * 5)
    result = _volume_confirmation(closes, volumes)
    assert result is not None, "expected a volume-confirmation result"
    assert result["move_pct"] > 3, f"expected a notable gain, got {result['move_pct']}"
    assert result["state"] == "unconfirmed", \
        f"expected 'unconfirmed', got {result['state']!r}"
    print(f"      synthetic spike: +{result['move_pct']:.1f}% on volume ratio "
          f"{result['ratio']:.2f} -> {result['state']}")


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

    # Step 4: deterministic verdict for real tickers + graceful invalid.
    for symbol in ["AAPL", "MSFT", "TEVA"]:
        results.append(check(f"{symbol} verdict is valid and consistent",
                             lambda s=symbol: expect_verdict(s)))
    results.append(check(f"{INVALID_TICKER} yields no usable verdict",
                         lambda: expect_verdict_not_usable(INVALID_TICKER)))

    # Part A: company metrics must be resilient (real fundamentals, not empty).
    for symbol in ["MSFT", "AAPL"]:
        results.append(check(f"{symbol} company metrics are resilient",
                             lambda s=symbol: expect_company_resilient(s)))

    # Part B: volume signals feed the verdict, weighted per horizon.
    for symbol in ["AAPL", "TEVA"]:
        results.append(check(f"{symbol} verdict includes volume signals",
                             lambda s=symbol: expect_volume_signals_in_verdict(s)))
    results.append(check("unconfirmed gain (synthetic) is flagged",
                         lambda: expect_unconfirmed_move_logic()))

    # Search: an Israeli ticker query surfaces its .TA match; a bare TASE
    # security number is (honestly) unresolvable via Yahoo.
    results.append(check("AVIV query surfaces AVIV.TA (Mordechai Aviv)",
                         lambda: expect_find_includes_ta("AVIV", "AVIV.TA", "Aviv")))
    results.append(check("TEVA.TA resolves via search",
                         lambda: expect_find_includes_ta("TEVA.TA", "TEVA.TA", "Teva")))
    results.append(check("bare TASE number 444018 is unresolvable",
                         lambda: expect_find_number_unresolvable("444018")))

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
