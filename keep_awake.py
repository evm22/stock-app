"""
keep_awake.py -- visit the live app once a day so Streamlit Community Cloud
doesn't leave it asleep. (A free app with no visitors for a while goes to
sleep, and the next visitor has to wake it and wait.)

Opens the app in a headless browser; if the "wake up" page is showing, clicks
its button and waits for the app to load. Always exits 0: a hiccup here must
never fail the daily run. No Streamlit import.

Usage:
    python keep_awake.py [URL]
"""

import sys

APP_URL = "https://evyat-stocks.streamlit.app"
WAKE_TEXT = "get this app back up"


def main(url=APP_URL):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("keep_awake: playwright is not installed (pip install -r requirements-job.txt)")
        return 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(5_000)
            button = page.get_by_role("button", name=WAKE_TEXT, exact=False)
            if button.count() and button.first.is_visible():
                print("keep_awake: app was asleep, clicking the wake-up button")
                button.first.click()
                page.wait_for_timeout(120_000)  # cold start takes a while
                print("keep_awake: wake-up requested")
            else:
                print("keep_awake: app is awake")
            browser.close()
    except Exception as exc:
        print(f"keep_awake: could not check the app ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else APP_URL))
