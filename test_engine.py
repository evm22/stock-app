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

import os
import sys

# Force the no-key path for EVERY AppTest run in this suite, so the tests never
# touch Gemini (network/quota/key) even though a real secrets.toml may exist
# locally. Must be set before app.py is imported by AppTest. See app.get_gemini_key.
os.environ.setdefault("STOCKAPP_DISABLE_GEMINI", "1")

import pandas as pd
import yfinance as yf  # for the Hebrew-alias English-name fallback search

# The Hebrew-alias test prints non-ASCII (Hebrew keys); keep stdout from crashing
# on a Windows console whose encoding can't represent it.
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

from engine import (
    get_stock_quote,
    get_price_history,
    first_close,
    price_to_pct_change,
    get_company_metrics,
    get_stock_technicals,
    compute_verdict,
    find_tickers,
    add_to_watchlist,
    remove_from_watchlist,
    in_watchlist,
    get_analyst_consensus,
    explain_divergence,
    Verdict,
    HorizonVerdict,
    WeightedSignal,
    AnalystConsensus,
    HEBREW_ALIASES,
    _quick_resolve,
    VERDICT_LABELS,
    HORIZONS,
    RANGES,
    HELP_TEXTS,
    _volume_confirmation,
    _rsi,
    _ema,
    _macd,
    _bollinger,
    _obv,
    _accum_dist,
    _trend_state,
    _label_for_score,
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
COMPANY_KEYS = ["market_cap", "pe", "forward_pe", "peg", "eps", "revenue",
                "earnings_growth", "revenue_growth",
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


def expect_metric_tiles_colored(symbol):
    """Drive the real UI (AppTest) for a live ticker and confirm the metric tiles
    render with color cues (a 🟢/🟡/🔴 status dot on the label), keep their '?'
    tooltips, and raise no exceptions."""
    os.environ.setdefault("STOCKAPP_DISABLE_BROWSER_STORAGE", "1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("app.py", default_timeout=90)
    at.run()
    at.text_input(key="search_box").set_value(symbol).run()
    if len(at.selectbox) >= 1:                 # disambiguation picker
        at.selectbox[0].set_value(symbol).run()
    assert not at.exception, f"{symbol}: app raised {at.exception}"

    tiles = at.metric
    assert tiles, f"{symbol}: no metric tiles rendered"

    def label_of(m):
        return (getattr(m, "label", None)
                or getattr(getattr(m, "proto", None), "label", "") or "")

    def help_of(m):
        return (getattr(m, "help", None)
                or getattr(getattr(m, "proto", None), "help", "") or "")

    dots = ("🟢", "🟡", "🔴")
    colored = [m for m in tiles if any(d in label_of(m) for d in dots)]
    with_help = [m for m in tiles if help_of(m)]
    assert colored, f"{symbol}: expected some color-coded tiles, found none"
    assert with_help, f"{symbol}: tiles lost their '?' tooltips"
    # The dot is a prefix — the original metric text must still be there.
    assert any(("P/E" in label_of(m)) or ("MA" in label_of(m)) for m in colored), \
        f"{symbol}: colored tiles missing expected metric labels"
    print(f"      {symbol}: {len(tiles)} tiles, {len(colored)} color-coded, "
          f"{len(with_help)} keep tooltips")


def expect_app_renders_without_gemini():
    """With NO Gemini key, the two AI buttons render but are DISABLED, no AI box
    appears (nothing is auto-generated), and nothing raises. The
    STOCKAPP_DISABLE_GEMINI seam (set at module load) forces the no-key path, so
    this never touches Gemini even if a local secrets.toml exists. (Reuses the
    AppTest harness; live AAPL only — no Gemini click-through.)"""
    assert os.environ.get("STOCKAPP_DISABLE_GEMINI"), \
        "test seam must be set so we never call Gemini"
    os.environ.setdefault("STOCKAPP_DISABLE_BROWSER_STORAGE", "1")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("app.py", default_timeout=90)
    at.run()
    at.text_input(key="search_box").set_value("AAPL").run()
    if len(at.selectbox) >= 1:
        at.selectbox[0].set_value("AAPL").run()
    assert not at.exception, f"app raised with key=None: {at.exception}"

    def label_of(b):
        return (getattr(b, "label", None)
                or getattr(getattr(b, "proto", None), "label", "") or "")

    def disabled_of(b):
        return bool(getattr(getattr(b, "proto", None), "disabled", False))

    buttons = at.button
    quick = [b for b in buttons if "Quick take" in label_of(b)]
    deep = [b for b in buttons if "Deep dive" in label_of(b)]
    assert quick, "the 'Quick take' button should render"
    assert deep, "the 'Deep dive' button should render"
    # No key -> both AI buttons must be disabled.
    assert disabled_of(quick[0]), "Quick take must be disabled without a key"
    assert disabled_of(deep[0]), "Deep dive must be disabled without a key"
    # Nothing was clicked -> the AI summary box / caption must be ABSENT.
    captions = [getattr(c, "value", "") for c in at.caption]
    assert not any("AI-generated summary" in c for c in captions), \
        "no AI box should appear before a click"
    assert not any("AI analysis unavailable" in c for c in captions), \
        "the unavailable note must only appear after a click"
    print(f"      app key=None: Quick+Deep buttons present & disabled, "
          f"no AI box ({len(buttons)} buttons total)")


def expect_hebrew_aliases_resolve():
    """Every curated Hebrew alias must resolve to a LIVE match: either its
    preferred ticker resolves, or a Yahoo search on its English name returns at
    least one result. This is the guard that no dead ticker ships.

    Prints any hard failures, and a 'SUGGESTED FIX' line whenever the preferred
    ticker is dead but the English-name search turns up a valid .TA symbol — so
    the map can be corrected rather than guessed.
    """
    # Collapse to unique targets: the english_name + Hebrew keys per ticker.
    targets, keys_for = {}, {}
    for heb, (ticker, eng) in HEBREW_ALIASES.items():
        targets[ticker] = eng
        keys_for.setdefault(ticker, []).append(heb)

    failures = []      # (ticker, english, hebrew_keys) — resolved to NOTHING
    suggestions = []   # (hebrew_keys, bad_ticker, suggested_symbol)

    for ticker, eng in sorted(targets.items()):
        hebs = ", ".join(keys_for[ticker])
        if _quick_resolve(ticker):
            continue  # preferred ticker is live — all good
        # Preferred ticker is dead. Try the English-name search fallback.
        try:
            results = yf.Search(eng, max_results=8).quotes
        except Exception:
            results = []
        # Surface a live .TA from the search as a SUGGESTED FIX for the map.
        for r in results:
            sym = (r.get("symbol") or "").upper()
            if sym.endswith(".TA") and _quick_resolve(sym):
                suggestions.append((hebs, ticker, sym))
                break
        if not results:
            failures.append((ticker, eng, hebs))

    if suggestions:
        print("\n  SUGGESTED FIX (preferred ticker dead, but a live .TA was found):")
        for hebs, bad, good in suggestions:
            print(f"    {hebs}: {bad} -> {good}")
    if failures:
        print("\n  FAILING ALIASES (neither the ticker nor the English search resolved):")
        for ticker, eng, hebs in failures:
            print(f"    {hebs}: {ticker} ({eng})")

    print(f"      hebrew aliases: {len(targets)} targets checked, "
          f"{len(failures)} failed, {len(suggestions)} suggested fix(es)")
    assert not failures, (
        f"{len(failures)} Hebrew alias(es) resolved to nothing: "
        + "; ".join(f"{h} -> {t}" for t, e, h in failures))


ANALYST_LABELS = ["Strong Buy", "Buy", "Hold", "Underperform", "Sell", "Strong Sell"]


def expect_analyst_coverage(symbol):
    """A covered stock returns a consensus label, a sane mean rating + target,
    and some recent analyst actions."""
    a = get_analyst_consensus(symbol)
    assert a.found, f"{symbol}: analyst consensus reported not-found"
    assert a.has_coverage, f"{symbol}: expected analyst coverage ({a.reason})"
    assert a.label in ANALYST_LABELS, f"{symbol}: bad consensus label {a.label!r}"
    if a.mean is not None:
        assert 1.0 <= a.mean <= 5.0, f"{symbol}: mean rating out of range {a.mean}"
    if a.target_mean is not None:
        assert a.target_mean > 0, f"{symbol}: target should be positive {a.target_mean}"
    assert a.actions, f"{symbol}: expected recent analyst actions"
    print(f"      {symbol}: analyst {a.label} (mean {a.mean}, {a.num_analysts} analysts), "
          f"target {a.target_mean}, {len(a.actions)} actions")


def expect_analyst_no_coverage(symbol):
    """A real-but-uncovered stock (e.g. small Tel-Aviv name) is found but flagged
    has_coverage=False — not a crash."""
    a = get_analyst_consensus(symbol)
    assert a.found, f"{symbol}: expected found=True"
    assert not a.has_coverage, f"{symbol}: expected NO analyst coverage"


def expect_analyst_not_found(symbol):
    """A fake ticker -> not found."""
    a = get_analyst_consensus(symbol)
    assert not a.found, f"{symbol}: expected not-found"


def expect_growth_aware_verdict(symbol):
    """A growth stock should get a 'growth' signal, and (when PEG exists) a
    growth-adjusted PEG valuation rather than a raw-P/E penalty."""
    verdict = compute_verdict(symbol)
    assert verdict.found, f"{symbol}: verdict not found"
    keys = {s.key for s in verdict.signals}
    assert "growth" in keys, f"{symbol}: expected a growth signal, got {sorted(keys)}"
    valuation = next((s for s in verdict.signals if s.key == "pe"), None)
    assert valuation is not None, f"{symbol}: expected a valuation signal"
    print(f"      {symbol}: valuation -> {valuation.measured} ({valuation.points:+d}); "
          f"1Y {verdict.horizons['1Y'].label}")


def expect_watchlist_logic():
    """Pure watchlist helpers (no network): add de-dupes + normalises + keeps
    order; remove works; membership check is case-insensitive."""
    wl = []
    wl = add_to_watchlist(wl, "aapl")          # lower-case in
    wl = add_to_watchlist(wl, "MSFT")
    wl = add_to_watchlist(wl, "AAPL")          # duplicate -> ignored
    wl = add_to_watchlist(wl, "  teva.ta  ")   # spaces trimmed
    assert wl == ["AAPL", "MSFT", "TEVA.TA"], f"unexpected watchlist {wl}"
    assert in_watchlist(wl, "aapl"), "membership should be case-insensitive"
    assert not in_watchlist(wl, "GOOG")
    wl = remove_from_watchlist(wl, "msft")
    assert wl == ["AAPL", "TEVA.TA"], f"unexpected after remove {wl}"
    wl = add_to_watchlist(wl, "")              # blank ignored
    assert wl == ["AAPL", "TEVA.TA"], f"blank should be ignored, got {wl}"
    print(f"      watchlist add/remove/dedupe/normalise OK -> {wl}")


def expect_divergence_explained():
    """Synthetic (no network): our Hold vs analysts' Strong Buy must produce a
    'analysts more bullish' explanation that names the signals dragging us down;
    an equal pair must produce no divergence."""
    breakdown = [
        WeightedSignal("pe", "Valuation (P/E)", "P/E 60 (expensive)", -1, 1.0, -1.0),
        WeightedSignal("growth", "Growth", "earnings 80% (rapid)", 2, 1.5, 3.0),
        WeightedSignal("macd", "Momentum (MACD)", "bearish", -1, 1.0, -1.0),
    ]
    hv = HorizonVerdict("1Y", label="Hold", score=52.0, enough_data=True,
                        breakdown=breakdown)
    verdict = Verdict(True, "TEST", horizons={"1Y": hv}, signals=[])

    bullish = AnalystConsensus(True, "TEST", has_coverage=True, label="Strong Buy",
                               mean=1.3)
    d = explain_divergence(verdict, bullish, "1Y")
    assert d.diverges, "expected a divergence"
    assert d.direction == "analysts_more_bullish", d.direction
    assert d.drivers, "expected drivers explaining the gap"
    assert all(weighted < 0 for (_, _, weighted) in d.drivers), \
        f"drivers should be the negative signals, got {d.drivers}"

    aligned = AnalystConsensus(True, "TEST", has_coverage=True, label="Hold", mean=3.0)
    aligned_result = explain_divergence(verdict, aligned, "1Y")
    assert not aligned_result.diverges, "equal labels should not diverge"
    # Even when aligned, there must be a (positive) note so the UI isn't empty.
    assert aligned_result.note, "aligned case should still carry a note"
    print(f"      divergence: {d.our_label} vs {d.analyst_label} -> {d.direction}, "
          f"drivers={[n for n, _, _ in d.drivers]}; aligned note OK")


def expect_help_texts_cover_all_metrics():
    """Every Company/Stock metric key (plus the verdict score and analyst mean)
    must have a non-empty help tooltip."""
    needed = COMPANY_KEYS + TECH_KEYS + ["verdict_score", "analyst_mean"]
    missing = [k for k in needed if not HELP_TEXTS.get(k)]
    assert not missing, f"missing help text for: {missing}"
    print(f"      help texts cover all {len(needed)} metrics/items")


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


def expect_pct_change_mapping():
    """Synthetic (no network): the % mapping used by the chart's normalised line
    and the candlestick's linked right-hand axis. The start bar must read exactly
    0%, and later bars must be (price / first_close - 1) * 100."""
    df = pd.DataFrame({
        "Date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03",
                                "2026-01-04"]),
        "Close": [100.0, 110.0, 90.0, 150.0],
    })

    base = first_close(df)
    assert base == 100.0, f"expected first close 100.0, got {base!r}"

    # The start bar is the 0% baseline by construction (exactly 0, no rounding).
    assert price_to_pct_change(base, base) == 0.0, "start bar must map to 0%"

    # A few hand-checked points: +10%, -10%, +50% (tolerant of float rounding).
    def _close(a, b):
        return abs(a - b) < 1e-9
    assert _close(price_to_pct_change(110.0, base), 10.0)
    assert _close(price_to_pct_change(90.0, base), -10.0)
    assert _close(price_to_pct_change(150.0, base), 50.0)

    # Vectorised over the whole Close column: first element is exactly 0%.
    pct = price_to_pct_change(df["Close"], base)
    assert pct.iloc[0] == 0.0, f"first % must be 0, got {pct.iloc[0]}"
    expected = [0.0, 10.0, -10.0, 50.0]
    assert all(_close(a, b) for a, b in zip(pct, expected)), \
        f"unexpected % series {list(pct)}"

    # Missing / empty data is handled (callers skip the % view instead of crashing).
    assert first_close(pd.DataFrame({"Close": []})) is None, "empty -> None"
    assert first_close(pd.DataFrame({"Open": [1.0]})) is None, "no Close -> None"

    print(f"      % mapping: start={base:.0f} -> 0%, series {list(pct)}")


def expect_indicator_math():
    """Synthetic (no network): the pure technical indicators on hand-made series
    with known answers, so the math is pinned down without relying on live data."""
    # _ema: a flat series stays flat; a rising series lags below the latest price.
    flat = pd.Series([5.0] * 30)
    assert abs(_ema(flat, 12).iloc[-1] - 5.0) < 1e-9, "EMA of a flat series = the value"
    rising = pd.Series([float(i) for i in range(1, 31)])
    assert _ema(rising, 12).iloc[-1] < rising.iloc[-1], "EMA should lag a rising series"

    # _rsi: strictly up -> 100 (no down days); strictly down -> 0 (no up days).
    up = pd.Series([float(i) for i in range(1, 21)])
    down = pd.Series([float(i) for i in range(20, 0, -1)])
    assert abs(_rsi(up) - 100.0) < 1e-9, f"all-up RSI should be 100, got {_rsi(up)}"
    assert abs(_rsi(down) - 0.0) < 1e-9, f"all-down RSI should be 0, got {_rsi(down)}"
    assert _rsi(pd.Series([1.0, 2.0])) is None, "too-short RSI must be None"

    # _macd: histogram is exactly macd - signal; a flat series gives all zeros.
    series = pd.Series([float(i) for i in range(1, 51)])
    m, s, h = _macd(series)
    assert abs(h - (m - s)) < 1e-9, "MACD histogram must equal macd - signal"
    fm, fs, fh = _macd(pd.Series([7.0] * 50))
    assert max(abs(fm), abs(fs), abs(fh)) < 1e-9, "flat series -> MACD all zero"
    assert _macd(pd.Series([1.0, 2.0])) == (None, None, None), "too-short -> Nones"

    # _bollinger: middle is the SMA, bands are symmetric, upper > middle > lower.
    closes20 = pd.Series([float(i) for i in range(1, 21)])  # mean 10.5
    upper, middle, lower = _bollinger(closes20, period=20, num_std=2.0)
    assert abs(middle - 10.5) < 1e-9, f"Bollinger middle should be 10.5, got {middle}"
    assert upper > middle > lower, "Bollinger order upper > middle > lower"
    assert abs((upper - middle) - (middle - lower)) < 1e-9, "bands must be symmetric"

    # _obv: +volume on up-closes, -volume on down-closes, 0 on unchanged.
    obv = _obv(pd.Series([10.0, 11.0, 10.0, 10.0, 12.0]),
               pd.Series([100.0, 200.0, 300.0, 400.0, 500.0]))
    assert list(obv) == [0.0, 200.0, -100.0, -100.0, 400.0], f"OBV wrong: {list(obv)}"

    # _accum_dist: a close at the high adds +volume; a close at the low adds -volume.
    ad = _accum_dist(pd.DataFrame({
        "High": [10.0, 10.0], "Low": [8.0, 8.0],
        "Close": [10.0, 8.0], "Volume": [100.0, 200.0],
    }))
    assert ad.iloc[0] == 100.0 and ad.iloc[-1] == -100.0, f"A/D wrong: {list(ad)}"

    # _trend_state: rising / falling / flat vs `lookback` bars ago.
    assert _trend_state(pd.Series([float(i) for i in range(25)])) == "rising"
    assert _trend_state(pd.Series([float(i) for i in range(25, 0, -1)])) == "falling"
    assert _trend_state(pd.Series([3.0] * 25)) == "flat"
    assert _trend_state(pd.Series([1.0, 2.0])) is None, "too-short -> None"

    print("      indicators: EMA/RSI/MACD/Bollinger/OBV/A-D/trend math OK")


def expect_verdict_label_bands():
    """Synthetic (no network): the 0..100 score -> label bands, exactly at their
    boundaries (<35 Sell, <55 Hold, <75 Buy, else Strong Buy)."""
    cases = [
        (0, "Sell"), (34.999, "Sell"),
        (35, "Hold"), (54.999, "Hold"),
        (55, "Buy"), (74.999, "Buy"),
        (75, "Strong Buy"), (100, "Strong Buy"),
    ]
    for score, expected in cases:
        got = _label_for_score(score)
        assert got == expected, f"score {score} -> {got!r}, expected {expected!r}"
    # The bands must use the canonical label vocabulary, in order.
    assert [_label_for_score(s) for s in (10, 45, 65, 90)] == VERDICT_LABELS, \
        "label bands should map onto VERDICT_LABELS in order"
    print(f"      verdict bands: {[ (c[0], c[1]) for c in cases ]}")


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
    # Hebrew-input search: every curated alias must resolve to a live ticker.
    results.append(check("Hebrew aliases all resolve to a live match",
                         lambda: expect_hebrew_aliases_resolve()))

    # Color-coded metric tiles render in the real UI (AppTest), with tooltips.
    for symbol in ["AAPL", "MSFT"]:
        results.append(check(f"{symbol} metric tiles render with color cues",
                             lambda s=symbol: expect_metric_tiles_colored(s)))

    # The optional Gemini layer is skipped silently when no key is configured.
    results.append(check("app renders normally with no Gemini key (box absent)",
                         lambda: expect_app_renders_without_gemini()))

    # Step 5: analyst consensus (covered, uncovered, and invalid).
    for symbol in ["AAPL", "TEVA"]:
        results.append(check(f"{symbol} analyst consensus present",
                             lambda s=symbol: expect_analyst_coverage(s)))
    results.append(check("AVIV.TA has no analyst coverage (graceful)",
                         lambda: expect_analyst_no_coverage("AVIV.TA")))
    results.append(check(f"{INVALID_TICKER} analyst consensus not found",
                         lambda: expect_analyst_not_found(INVALID_TICKER)))

    # Engine refinement: growth-aware verdict + divergence explanation.
    results.append(check("AVGO verdict is growth-aware (PEG + growth signal)",
                         lambda: expect_growth_aware_verdict("AVGO")))
    results.append(check("divergence is explained (synthetic)",
                         lambda: expect_divergence_explained()))
    results.append(check("help tooltips cover every metric",
                         lambda: expect_help_texts_cover_all_metrics()))

    # Step 6: pure watchlist list helpers.
    results.append(check("watchlist add/remove/dedupe logic",
                         lambda: expect_watchlist_logic()))

    # Chart %-change mapping (start bar = 0%), used by the line and the
    # candlestick's linked right-hand % axis.
    results.append(check("percent-change mapping (start bar = 0%)",
                         lambda: expect_pct_change_mapping()))

    # Pure indicator + verdict-band math (no network), with known answers.
    results.append(check("technical indicator math (synthetic)",
                         lambda: expect_indicator_math()))
    results.append(check("verdict score -> label bands",
                         lambda: expect_verdict_label_bands()))

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
