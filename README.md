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

## Opportunity screener (offline batch)

Separate from the app, a two-step screener scores the whole universe
(~611 tickers: S&P 500 + Israeli stocks + themes, see `universe.py`):

```bash
python scan.py             # scores every ticker -> screen_results.json (~30 min)
python scan.py --resume    # continue an interrupted scan from its checkpoint
python rank.py             # top 25 opportunities, 1-year horizon
python rank.py --horizon 5y --theme ai --max-beta 1.5 --top 10
python rank.py --region IL --agree-only
```

`rank.py` blends **60% our verdict score + 40% analyst implied upside**
(clamped to -20%..+50%; no coverage = neutral +10%), minus a small risk penalty for high beta and heavy
debt. The formula and its knobs are at the top of `rank.py`.

The daily run that does all of this automatically is described under
**Daily automatic run** below.

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
venv\Scripts\python test_scan.py     # screener rows, no network
venv\Scripts\python test_data_store.py  # data store (local + mocked GitHub)
venv\Scripts\python test_daily_job.py   # daily run: guard, history, ranking, Telegram (mocked)
venv\Scripts\python test_rank.py     # screener ranking + resume, no network
```

Each prints a plain-text `PASS`/`FAIL` per check and exits `0` if everything
passed, `1` otherwise.

## Secrets & API keys

Never commit API keys or secrets to this repository. When we need them later,
we'll use [Streamlit secrets](https://docs.streamlit.io/develop/concepts/connections/secrets-management)
(a local `.streamlit/secrets.toml` file, which is git-ignored, and the secrets
manager in Streamlit Community Cloud for deployment).

Optional secrets the app understands (all can be left out; the app then works
exactly as before):

| Secret | What it does |
|---|---|
| `APP_PASSWORD` | Locks the app behind a password screen |
| `DATA_REPO`, `DATA_REPO_TOKEN` | Saves the app's own data in a **private** GitHub repo instead of the local `local_data/` folder |

## Daily automatic run

`daily_job.py` runs the screener once a day, saves `screen_results.json`,
appends each stock's numeric scores + price to `score_history.csv`, and saves
the full ranked list to `ranked/YYYY-MM-DD.json` (both kept for checking
"were the picks right?" later). It only runs from 07:00 Israel time and once
per day. It then sends a Hebrew Telegram summary (top 10, who entered / left
the top since the previous run, top Israeli stocks) through `notify.py`.

Telegram credentials: `TELEGRAM_BOT_TOKEN` (or `TELEGRAM_TOKEN`) and
`TELEGRAM_CHAT_ID`, from the environment or the git-ignored
`.streamlit/secrets.toml`. Test with `python notify.py "hello"`.

```bash
venv\Scripts\python daily_job.py --limit 5 --force --dry-run  # quick test, print only
venv\Scripts\python daily_job.py --send-at 09:00               # hold message until 09:00
```

**On the Mac (active now):** launchd starts the job at 08:00 and the message is
sent at 09:00. The Mac must be on; if it is asleep at 08:00 the job runs when
it wakes. Logs: `logs/daily.log`.

```bash
cp com.evyat.stockapp.daily.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.evyat.stockapp.daily.plist
```

The GitHub Actions workflow that runs it every morning is kept as a template in
`deploy/stock-app-data/`. It is meant for the private data repo and is **not**
active in this public repo (GitHub only runs workflows under `.github/`).
`keep_awake.py` (used by that workflow) visits the live app so it isn't asleep.

## Working on the look offline

`dev/demo_app.py` runs the real `app.py` with made-up sample data (AAPL, NVDA,
TEVA.TA), so you can see and screenshot the UI without internet access:

```bash
streamlit run dev/demo_app.py
```

The colors live in `.streamlit/config.toml` (committed); the Hebrew
right-to-left layout and the small cards are CSS at the top of `app.py`.
