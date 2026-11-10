"""
dev/demo_app.py -- run the REAL app.py with made-up sample data (no internet).

Useful for working on the look of the app when Yahoo isn't reachable, and for
screenshots. Every number shown is FAKE sample data (see dev/demo_data.py).

    streamlit run dev/demo_app.py

Sample tickers: AAPL, NVDA, TEVA.TA (anything else -> "not found").
"""

import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("STOCKAPP_DISABLE_GEMINI", "1")

import demo_data  # noqa: E402

demo_data.install()
os.chdir(ROOT)
runpy.run_path(os.path.join(ROOT, "app.py"), run_name="__main__")
