"""
Stock Analysis App — entry point.

This is the minimal starting point. For now it just renders a single page
so we can confirm the app runs and deploys correctly. Real features
(fetching prices, charts, indicators) will be added later.

Run locally with:
    streamlit run app.py
"""

import streamlit as st

# Basic page configuration (title shown in the browser tab, etc.).
st.set_page_config(page_title="Stock Analysis App", page_icon="📈")

# Everything below is the page content.
st.title("📈 Stock Analysis App")
st.success("Hello, it works!")
st.write("The skeleton is up and running. Features will be added soon.")
