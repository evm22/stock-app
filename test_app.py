"""
test_app.py - checks for the display helpers in app.py that DON'T need network.

The data engine is covered by test_engine.py (which hits live Yahoo). This file
covers the chart-building side, focusing on make_candlestick(): we feed it a
small synthetic OHLC table (no Yahoo call) and inspect the resulting Vega-Lite
spec, so these tests are fast and deterministic.

Run it from the project folder with the venv's python:

    python test_app.py

Prints a clear ASCII PASS/FAIL per check and exits 0 if all passed, 1 otherwise.
"""

import os
import sys

# The Hebrew-alias test prints non-ASCII; keep stdout from crashing on a Windows
# console whose encoding can't represent it.
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# app.py reaches for the browser's localStorage on import; this seam keeps the
# (browser-blocking) component out of the way when there's no real Streamlit
# server, exactly like AppTest does. Set it BEFORE importing app.
os.environ.setdefault("STOCKAPP_DISABLE_BROWSER_STORAGE", "1")

import pandas as pd

import engine
import app


def check(description, test_function):
    """Run one test; print PASS/FAIL. Returns True/False for the tally."""
    try:
        test_function()
        print(f"PASS: {description}")
        return True
    except AssertionError as error:
        print(f"FAIL: {description} -> {error}")
        return False
    except Exception as error:
        print(f"FAIL: {description} -> unexpected error: {error}")
        return False


def _sample_history():
    """A tiny, hand-made OHLC table (no network) for charting tests.

    First close is 100.0 on purpose, so percentages are easy to reason about.
    """
    return pd.DataFrame({
        "Date": pd.to_datetime(["2026-01-02", "2026-01-03", "2026-01-06",
                                "2026-01-07", "2026-01-08"]),
        "Open":   [100.0, 102.0,  99.0, 105.0, 108.0],
        "High":   [103.0, 104.0, 106.0, 110.0, 112.0],
        "Low":    [ 98.0,  97.0,  96.0, 104.0, 107.0],
        "Close":  [100.0, 101.0, 103.0, 108.0, 110.0],
        "Volume": [1_000, 1_200,   900, 1_500, 1_300],
    })


def _layer_values(spec, layer):
    """A layer's inline data rows, whether inlined or via top-level datasets."""
    data = layer.get("data", {})
    if "values" in data:
        return data["values"]
    name = data.get("name")
    if name and "datasets" in spec:
        return spec["datasets"].get(name, [])
    return []


def expect_plain_candles_unchanged():
    """Without pct_first_close: just candles (wick + body) on one Price axis,
    and no secondary-scale resolution (the non-% path must stay simple)."""
    spec = app.make_candlestick(_sample_history()).to_dict()
    layers = spec.get("layer", [])
    assert len(layers) == 2, f"expected 2 candle layers, got {len(layers)}"
    assert "resolve" not in spec, "plain candles should not resolve scales"
    wick_y = layers[0]["encoding"]["y"]
    assert wick_y.get("title") == "Price", \
        f"left axis should be 'Price', got {wick_y.get('title')!r}"
    print("      plain candlestick: wick+body on a single Price axis")


def expect_percent_mode_adds_baseline_and_axis():
    """With pct_first_close: candles + a dashed 0% baseline on the LEFT price
    scale + a linked right-hand '% change' axis; y scales resolved independent."""
    df = _sample_history()
    base = engine.first_close(df)
    assert base == 100.0, f"sanity: first close should be 100.0, got {base}"
    spec = app.make_candlestick(df, pct_first_close=base).to_dict()

    layers = spec.get("layer", [])
    assert len(layers) == 3, \
        f"expected 3 layers (candles, baseline, % axis), got {len(layers)}"
    assert spec.get("resolve", {}).get("scale", {}).get("y") == "independent", \
        "y scales should be independent so the two axes keep their own domains"

    # Candles keep the left $ axis, with the domain padded around Low..High.
    left_y = layers[0]["layer"][0]["encoding"]["y"]
    assert left_y.get("title") == "Price", "candles must stay on the Price axis"
    left_domain = left_y["scale"]["domain"]
    assert left_domain[0] < df["Low"].min() and left_domain[1] > df["High"].max(), \
        f"price domain should pad Low..High, got {left_domain}"

    # Baseline: a dashed rule at y == base (the start close), on the LEFT scale.
    baseline = layers[1]
    assert baseline["mark"]["type"] == "rule", "baseline must be a rule"
    assert baseline["mark"].get("strokeDash"), "baseline rule should be dashed"
    assert baseline["encoding"]["y"]["scale"]["domain"] == left_domain, \
        "baseline must share the candles' left price scale (so it lines up)"
    ys = [row.get("y") for row in _layer_values(spec, baseline)]
    assert any(abs(y - base) < 1e-9 for y in ys if y is not None), \
        f"baseline should sit at the start close {base}, got {ys}"
    # The baseline only DRAWS the dashed rule; it must contribute NO axis of its
    # own (axis=None). Otherwise resolve_scale(y="independent") renders a second,
    # duplicate right-hand PRICE axis on top of the "% change" axis -- two sets of
    # labels overlapping. axis=None keeps its scale (so it stays aligned) but
    # suppresses the extra axis.
    assert "axis" in baseline["encoding"]["y"] \
        and baseline["encoding"]["y"]["axis"] is None, \
        "baseline y must set axis=None so it adds no duplicate right-hand axis"

    # Right axis: '% change', oriented right, domain straddling 0%.
    right_y = layers[2]["encoding"]["y"]
    assert right_y["axis"]["orient"] == "right", "% axis must be on the right"
    assert right_y["title"] == "% change", \
        f"right axis title wrong: {right_y['title']!r}"
    dom = right_y["scale"]["domain"]
    assert dom[0] < 0 < dom[1], f"% domain should straddle 0, got {dom}"
    # The % axis must be the exact linear image of the left price domain, so the
    # two scales stay aligned pixel-for-pixel and the start close reads 0%.
    assert abs(dom[0] - engine.price_to_pct_change(left_domain[0], base)) < 1e-9
    assert abs(dom[1] - engine.price_to_pct_change(left_domain[1], base)) < 1e-9
    print(f"      percent candlestick: 3 layers; dashed 0% baseline at "
          f"{base:.2f}; right axis [{dom[0]:.1f}%, {dom[1]:.1f}%]")


def expect_abbreviate_big_numbers():
    """_abbreviate shortens large numbers with a T/B/M/K suffix, and leaves
    small ones plain."""
    cases = [
        (4_362_291_642_368, "4.36T"),
        (1_500_000_000, "1.50B"),
        (2_500_000, "2.50M"),
        (3_500, "3.50K"),
        (500, "500"),
        (-2_500_000, "-2.50M"),   # magnitude picks the suffix, sign is kept
    ]
    for number, expected in cases:
        got = app._abbreviate(number)
        assert got == expected, f"_abbreviate({number}) -> {got!r}, want {expected!r}"
    print(f"      _abbreviate: {[c[1] for c in cases]}")


def expect_format_metric_by_fmt():
    """format_metric renders each fmt hint correctly. The tricky one (flagged in
    the code) is percent_frac (a FRACTION, *100) vs percent (ALREADY a percent)."""
    def m(value, fmt, available=True):
        return engine.Metric("label", value, available, fmt, "src")

    # Missing values always show a friendly n/a, whatever the fmt.
    assert app.format_metric(m(None, "money", available=False), "USD") == "n/a"

    # Money formats carry the currency; ratios/percents do not.
    assert app.format_metric(m(4_362_291_642_368, "large_money"), "USD") == "4.36T USD"
    assert app.format_metric(m(123.456, "money"), "USD") == "123.46 USD"
    assert app.format_metric(m(12.345, "ratio"), "USD") == "12.35"
    # The footgun: profitMargins is a fraction -> *100; dividendYield already %.
    assert app.format_metric(m(0.2715, "percent_frac"), "USD") == "27.15%"
    assert app.format_metric(m(0.36, "percent"), "USD") == "0.36%"
    assert app.format_metric(m(5_000_000, "int_large"), "USD") == "5.00M"
    # text / date pass straight through.
    assert app.format_metric(m("Technology", "text"), "USD") == "Technology"
    # No currency string -> money values just omit the suffix.
    assert app.format_metric(m(10.0, "money"), "") == "10.00"
    print("      format_metric: large_money/money/ratio/percent_frac/percent/"
          "int_large/text OK")


def expect_volume_panel_readable():
    """make_volume_chart: tall enough to read (height 200, up from 120) with
    compact SI y-tick labels (50,000,000 -> '50M'). Crucially it KEEPS the zero
    baseline -- volume must anchor at 0, so no domain-restricted y-scale."""
    spec = app.make_volume_chart(_sample_history()).to_dict()
    assert spec.get("height") == 200, \
        f"volume panel should be height 200 (was 120), got {spec.get('height')!r}"
    y = spec["encoding"]["y"]
    assert y.get("field") == "Volume", "volume chart must encode Volume on y"
    assert y.get("axis", {}).get("format") == "~s", \
        f"volume y ticks should use SI format '~s', got {y.get('axis')!r}"
    # Zero baseline: no domain restriction and zero not disabled.
    scale = y.get("scale", {})
    assert "domain" not in scale and scale.get("zero", True) is not False, \
        f"volume y must keep its zero baseline (no domain, zero!=False), got {scale!r}"
    print("      volume panel: height 200, SI y-ticks, zero baseline kept")


def expect_hebrew_alias_lookup():
    """normalize_hebrew + the alias map turn Hebrew names into the right target,
    with NO network (pure dict/normalization logic). The live tickers themselves
    are checked separately in test_engine.expect_hebrew_aliases_resolve."""
    assert engine.hebrew_alias("אפל") == ("AAPL", "Apple")
    assert engine.hebrew_alias("בנק הפועלים") == ("POLI.TA", "Bank Hapoalim")
    # The short form and the "בנק "-prefixed form both resolve (prefix stripped).
    assert engine.hebrew_alias("פועלים") == ("POLI.TA", "Bank Hapoalim")
    # Geresh/apostrophe variants are ignored: "נופר אנרג'י" == "נופר אנרגי".
    assert engine.hebrew_alias("נופר אנרג'י") == ("NOFR.TA", "Nofar Energy")
    # Surrounding whitespace is tidied.
    assert engine.hebrew_alias("  אפל  ") == ("AAPL", "Apple")
    # A non-alias (English ticker) returns None, so the normal English/ticker
    # search path is left completely untouched.
    assert engine.hebrew_alias("AAPL") is None
    # "קבוצת " is stripped, so the group form matches the short form.
    assert engine.normalize_hebrew("קבוצת דלק") == engine.normalize_hebrew("דלק") == "דלק"
    print("      hebrew alias lookup: אפל->AAPL, בנק הפועלים->POLI.TA, geresh OK")


def main():
    print("Running app display tests (no network)...\n")
    results = [
        check("plain candlestick is wick+body on one Price axis",
              expect_plain_candles_unchanged),
        check("percent candlestick adds 0% baseline + linked % axis",
              expect_percent_mode_adds_baseline_and_axis),
        check("_abbreviate shortens big numbers (T/B/M/K)",
              expect_abbreviate_big_numbers),
        check("format_metric renders each fmt hint (percent_frac vs percent)",
              expect_format_metric_by_fmt),
        check("volume panel is taller (200) with SI y-ticks and a zero baseline",
              expect_volume_panel_readable),
        check("hebrew alias lookup maps Hebrew names to tickers (no network)",
              expect_hebrew_alias_lookup),
    ]

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} tests passed.")

    # Plain ASCII on purpose (Windows cp1252/cp1255 consoles choke on emoji).
    if passed == total:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
