"""
dev/demo_data.py -- made-up sample data that stands in for Yahoo (no internet).

install() swaps the engine's data functions for these fakes. Used by
dev/demo_app.py (to look at the app offline) and handy in tests. Every number
here is FAKE sample data. Sample tickers: AAPL, NVDA, TEVA.TA.
"""

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import engine  # noqa: E402

M = engine.Metric

SAMPLE = {
    "AAPL": dict(name="Apple Inc.", price=228.15, prev=229.57, cur="USD", ex="NASDAQ",
                 trend=0.0006, seed=7, scores=(52, 54, 63), sector="Technology",
                 analyst=("buy", "Buy", 2.0, 41, 245.0, 290.0, 190.0)),
    "NVDA": dict(name="NVIDIA Corporation", price=182.40, prev=178.28, cur="USD", ex="NASDAQ",
                 trend=0.0018, seed=3, scores=(71, 78, 80), sector="Technology",
                 analyst=("strong_buy", "Strong Buy", 1.4, 58, 215.0, 260.0, 150.0)),
    # Yahoo gives Tel Aviv prices in agorot ("ILA"), so the sample does too.
    "TEVA.TA": dict(name="Teva Pharmaceutical Industries", price=6890.0, prev=6814.0, cur="ILA",
                    ex="TLV", trend=0.0011, seed=11, scores=(62, 63, 48), sector="Healthcare",
                    analyst=None),
}


def _hist(sym, range_key):
    s = SAMPLE[sym]
    n = {"1D": 78, "1W": 35, "1M": 22, "6M": 126, "1Y": 252, "5Y": 260}.get(range_key, 60)
    freq = {"1D": "5min", "1W": "30min", "5Y": "W"}.get(range_key, "B")
    rng = np.random.default_rng(s["seed"] + len(range_key))
    steps = rng.normal(s["trend"], 0.015, n)
    close = s["price"] / np.exp(steps[::-1].cumsum())[::-1]
    close = close * s["price"] / close[-1]
    open_ = close * (1 + rng.normal(0, 0.006, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.006, n)))
    dates = pd.date_range(end=pd.Timestamp("2026-11-09"), periods=n, freq=freq)
    vol = rng.integers(20_000_000, 90_000_000, n)
    return pd.DataFrame({"Date": dates, "Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": vol})


FX = {"USDILS=X": 3.72}  # fake dollar rate in shekels


def get_stock_quote(t):
    t = (t or "").upper()
    if t in FX:
        return engine.StockQuote(True, t, t, FX[t], "ILS", FX[t], 0.0, 0.0, "CCY", "demo", "demo", {})
    if t not in SAMPLE:
        return engine.StockQuote(False, t)
    s = SAMPLE[t]
    ch = s["price"] - s["prev"]
    return engine.StockQuote(True, t, s["name"], s["price"], s["cur"], s["prev"], ch,
                             ch / s["prev"] * 100, s["ex"], "demo", "demo", {})


def find_tickers(q, max_results=6):
    q = (q or "").strip().upper()
    hebrew = {"אפל": "AAPL", "אנבידיה": "NVDA", "טבע": "TEVA.TA"}
    q = hebrew.get(q.strip(), q)
    hits = [k for k in SAMPLE if q and (q in k or q in SAMPLE[k]["name"].upper())]
    return [engine.TickerMatch(k, SAMPLE[k]["name"], SAMPLE[k]["ex"], SAMPLE[k]["cur"])
            for k in hits]


def get_price_history(t, range_key):
    t = t.upper()
    if t not in SAMPLE:
        return engine.PriceHistory(False, t, range_key, reason="unknown")
    return engine.PriceHistory(True, t, range_key, data=_hist(t, range_key))


def get_company_metrics(t):
    t = t.upper()
    s = SAMPLE[t]
    tech = s["sector"] == "Technology"
    vals = {
        "market_cap": M("Market cap", 3.4e12 if tech else 7.8e10, True, "large_money"),
        "pe": M("P/E (trailing)", 34.2 if t == "AAPL" else (45.1 if tech else None), t != "TEVA.TA", "ratio"),
        "forward_pe": M("Forward P/E", 29.0 if tech else 9.1, True, "ratio"),
        "peg": M("PEG ratio", 2.4 if t == "AAPL" else 1.1, True, "ratio"),
        "eps": M("EPS (trailing)", 6.67, True, "money"),
        "revenue": M("Revenue (ttm)", 3.9e11, True, "large_money"),
        "earnings_growth": M("Earnings growth (yoy)", 0.11 if t == "AAPL" else 0.6, True, "percent_frac"),
        "revenue_growth": M("Revenue growth (yoy)", 0.04 if t == "AAPL" else (0.62 if tech else 0.05), True, "percent_frac"),
        "profit_margin": M("Profit margin", 0.243 if t == "AAPL" else (0.55 if tech else None), t != "TEVA.TA", "percent_frac"),
        "dividend_yield": M("Dividend yield", 0.44 if t == "AAPL" else None, t == "AAPL", "percent"),
        "debt_to_equity": M("Debt-to-equity", 145.0 if t == "AAPL" else (13.0 if tech else 260.0), True, "ratio"),
        "free_cash_flow": M("Free cash flow", 1.0e11, True, "large_money"),
        "next_earnings": M("Next earnings", "2026-01-29", True, "date"),
        "sector": M("Sector", s["sector"], True, "text"),
        "industry": M("Industry", "Consumer Electronics" if t == "AAPL" else "Semiconductors", True, "text"),
    }
    return engine.MetricGroup(True, t, s["cur"], vals, "demo")


def get_stock_technicals(t):
    t = t.upper()
    s = SAMPLE[t]
    p = s["price"]
    keys = {
        "week52_high": ("52-week high", p * 1.12, "money"), "week52_low": ("52-week low", p * 0.7, "money"),
        "ma50": ("50-day MA", p * 0.97, "money"), "ma200": ("200-day MA", p * 0.9, "money"),
        "rsi": ("RSI (14)", 58.2, "ratio"), "beta": ("Beta", 1.24 if t == "AAPL" else 1.8, "ratio"),
        "avg_volume": ("Avg volume", 5.2e7, "int_large"), "macd": ("MACD line", 1.3, "ratio"),
        "macd_signal": ("MACD signal", 0.9, "ratio"), "macd_hist": ("MACD histogram", 0.4, "ratio"),
        "macd_state": ("MACD trend", "bullish", "text"), "bb_upper": ("Bollinger upper", p * 1.05, "money"),
        "bb_middle": ("Bollinger mid (20d SMA)", p, "money"), "bb_lower": ("Bollinger lower", p * 0.95, "money"),
        "bb_state": ("Bollinger position", "inside bands", "text"),
        "vol_recent": ("Recent volume (5d avg)", 4.8e7, "int_large"), "vol_avg": ("Avg volume (50d)", 5.2e7, "int_large"),
        "vol_move": ("5-day price move", 1.2, "percent"), "vol_confirm": ("Volume confirmation", "neutral", "text"),
        "obv_value": ("OBV (est. pressure)", 1.2e9, "int_large"), "obv_trend": ("OBV trend", "rising", "text"),
        "ad_value": ("Accum/Dist (est.)", 8e8, "int_large"), "ad_trend": ("A/D trend", "rising", "text"),
    }
    return engine.MetricGroup(True, t, s["cur"],
                              {k: M(l, v, True, f) for k, (l, v, f) in keys.items()}, "demo")


SIGNALS = [("rev_growth", "Revenue growth", "62% yoy", 2), ("margin", "Profit margin", "55%", 2),
           ("ma200", "Price vs 200-day MA", "10% above", 1), ("pe", "Valuation (P/E)", "45.1 vs sector 30", -1),
           ("beta", "Volatility (beta)", "1.80", -1), ("rsi", "Momentum (RSI)", "58", 0)]


def compute_verdict(t):
    t = t.upper()
    if t not in SAMPLE:
        return engine.Verdict(False, t)
    hz = {}
    for h, sc in zip(engine.HORIZONS, SAMPLE[t]["scores"]):
        br = [engine.WeightedSignal(k, n, m, p, 1.0, float(p)) for k, n, m, p in SIGNALS]
        hz[h] = engine.HorizonVerdict(h, engine._label_for_score(sc), float(sc), True, "", br)
    return engine.Verdict(True, t, hz, [engine.Signal(k, n, m, p) for k, n, m, p in SIGNALS])


def get_analyst_consensus(t):
    t = t.upper()
    a = SAMPLE[t]["analyst"]
    if not a:
        return engine.AnalystConsensus(True, t, has_coverage=False)
    key, label, mean, n, tgt, hi, lo = a
    p = SAMPLE[t]["price"]
    return engine.AnalystConsensus(True, t, True, key, label, mean, n, "USD", p, tgt, hi, lo, tgt,
                                   (tgt / p - 1) * 100,
                                   [{"date": "2026-11-03", "firm": "Morgan Stanley", "action": "up",
                                     "from_grade": "Equal-Weight", "to_grade": "Overweight",
                                     "price_target": tgt}])


def get_institutional_holders(t, top_n=10):
    t = t.upper()
    return engine.HoldersResult(True, t, 0.62, 3400, [
        engine.InstitutionalHolder("Vanguard Group Inc", 1_300_000_000, 0.087, None, "2026-09-30"),
        engine.InstitutionalHolder("Blackrock Inc.", 1_050_000_000, 0.07, None, "2026-09-30")])


FAKES = ("get_stock_quote", "find_tickers", "get_price_history", "get_company_metrics",
         "get_stock_technicals", "compute_verdict", "get_analyst_consensus",
         "get_institutional_holders")


def install():
    """Point the engine at the fake data (until the process ends)."""
    for name in FAKES:
        setattr(engine, name, globals()[name])
