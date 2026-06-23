# 📈 Stock Analysis App

A personal stock-analysis web app for looking up a stock and getting a quick,
explainable read on it — price history, the company's fundamentals, the share
price's technical behaviour, and a transparent rule-based verdict alongside the
Wall Street analyst consensus. Built as a learning project, so the code is kept
simple and heavily commented.

> ⚠️ **Not financial advice.** Every "verdict" here is an automated, rule-based
> opinion derived from public data. It re-weights *current* data; it does **not**
> predict the future. Do your own research.

## Tech stack

- **Python + [Streamlit](https://streamlit.io/)** — the web UI
- **[yfinance](https://pypi.org/project/yfinance/)** — free stock market data
- **[Altair](https://altair-viz.github.io/)** — the candlestick / volume charts
- **GitHub** — source hosting
- **[Streamlit Community Cloud](https://streamlit.io/cloud)** — free deployment

## Status

✅ **Live:** <https://evyat-stocks.streamlit.app>

Actively built in small steps. Current features:

- **Search** by ticker, company name, or Tel-Aviv `.TA` symbol, with a "did you
  mean…?" picker when a query is ambiguous (e.g. `AVIV.TA` vs a US ETF).
- **Live quote** — latest price, the day's change, currency, and exchange, with
  resilient multi-source fallbacks.
- **Price history chart** over 1D / 1W / 1M / 6M / 1Y / 5Y, as a **line** or
  **candlestick**, with a **"% change from start of range"** toggle (a normalised
  line, or — in candlestick mode — a linked right-hand `% change` axis and a 0%
  baseline, keeping price on the left `$` axis), plus a subtle volume sub-panel.
- **Company analysis** — market cap, P/E, forward P/E, PEG, EPS, revenue,
  earnings/revenue growth, margins, dividend yield, debt-to-equity, free cash
  flow, sector/industry, next earnings date.
- **Stock analysis** — 52-week range, 50/200-day moving averages, RSI, beta,
  MACD, Bollinger Bands, plus volume-pressure estimates (OBV, Accumulation/
  Distribution, volume confirmation).
- **Three-horizon verdict** (6-month / 1-year / 5-year) — colour-coded Sell →
  Strong Buy with a per-signal breakdown of how each score was reached.
- **Our verdict vs Wall Street** — the analyst consensus, mean price target and
  implied upside, recent upgrades/downgrades, and a plain-language explanation
  when our view and the analysts' diverge.
- **Watchlist** — follow stocks from the sidebar, persisted in your browser.
- A **"?" tooltip** on every metric, and a Debug panel showing where each value
  came from.

## Run it locally

```bash
# 1. (Recommended) create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 2. Install the dependencies
pip install -r requirements.txt

# 3. Start the app
streamlit run app.py
```

Then open the URL Streamlit prints (usually <http://localhost:8501>).

> 💡 **Tel-Aviv stocks** use a `.TA` ticker on Yahoo Finance (e.g. `TEVA.TA`,
> `AVIV.TA`) — **not** their TASE security number, which Yahoo can't look up.

## Tests

The data logic lives in `engine.py` (pure Python, no Streamlit); the UI in
`app.py`. There are two test files:

```bash
venv\Scripts\python test_engine.py   # engine vs live Yahoo (needs internet)
venv\Scripts\python test_app.py      # chart builder, no network (fast)
```

Each prints a plain-text `PASS`/`FAIL` per check and exits `0` if everything
passed, `1` otherwise.

## Secrets & API keys

Never commit API keys or secrets to this repository. When we need them later,
we'll use [Streamlit secrets](https://docs.streamlit.io/develop/concepts/connections/secrets-management)
(a local `.streamlit/secrets.toml` file, which is git-ignored, and the secrets
manager in Streamlit Community Cloud for deployment).
