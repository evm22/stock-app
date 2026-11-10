# stock-app-data (private)

Private data for the stock app (https://evyat-stocks.streamlit.app). Keep this
repo **private**: it holds your portfolio and trades.

| File | Written by | What it is |
|---|---|---|
| `data/portfolio.json` | the app | Your buys and sells (the portfolio is worked out from them) |
| `data/watchlist.json` | the app | The stocks you follow |
| `data/ai_picks.json` | daily run | "ה-AI ממליץ": short- and long-term picks |
| `data/ranked/` | daily run | The ranked list of the day (and `latest.json`) |
| `data/screen_results.json` | daily run | Today's scan of every stock in the universe |
| `data/score_history.csv` | daily run | One row per stock per day: scores (0–100) and price. Used for "was the score right?" |
| `data/last_run.json` | daily run | When the last run happened and how many stocks succeeded |
| `data/last_summary.txt` | daily run | The one-line message sent to Telegram |

## The daily run (`.github/workflows/daily.yml`)
Runs once a day in the morning (Israel time), scans the stocks with the app's
own code, saves the files above, wakes the live app if it's asleep, and sends
a Telegram summary. To run it by hand: **Actions → daily → Run workflow**
(set `limit` to e.g. `20` and tick `force` for a quick test).

Secrets it needs (Settings → Secrets and variables → Actions):
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`.
