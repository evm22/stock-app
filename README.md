# 📈 Stock Analysis App

A personal stock-analysis web app for exploring stock data and (later) running
simple analyses. Built as a learning project.

## Tech stack

- **Python + [Streamlit](https://streamlit.io/)** — the web UI
- **[yfinance](https://pypi.org/project/yfinance/)** — free stock market data
- **GitHub** — source hosting
- **Streamlit Community Cloud** — free deployment (set up later)

## Status

🚧 Early skeleton — just a "Hello, it works!" page for now. No features yet.

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

Then open the URL Streamlit prints (usually http://localhost:8501).

## Secrets & API keys

Never commit API keys or secrets to this repository. When we need them later,
we'll use [Streamlit secrets](https://docs.streamlit.io/develop/concepts/connections/secrets-management)
(a local `.streamlit/secrets.toml` file, which is git-ignored, and the secrets
manager in Streamlit Community Cloud for deployment).
