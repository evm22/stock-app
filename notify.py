"""
notify.py -- send a message to Telegram (used by daily.py).

No extra dependencies: talks to the Telegram Bot HTTP API with urllib.

Credentials, first match wins (NEVER commit them):
  1. environment variables TELEGRAM_TOKEN and TELEGRAM_CHAT_ID
  2. .streamlit/secrets.toml (git-ignored), keys of the same names:
         TELEGRAM_TOKEN   = "123456:ABC..."
         TELEGRAM_CHAT_ID = "123456789"

Quick check:
    python notify.py "hello"      # sends a test message
"""

import json
import os
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request

SECRETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".streamlit", "secrets.toml")
TELEGRAM_MAX_LEN = 4096   # Telegram's per-message character limit


def load_credentials(secrets_path: str = None):
    """Return (token, chat_id), or (None, None) if not configured."""
    secrets_path = secrets_path or SECRETS_PATH
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        return token, chat
    try:
        with open(secrets_path, "rb") as fh:
            secrets = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        secrets = {}
    token = token or secrets.get("TELEGRAM_TOKEN")
    chat = chat or secrets.get("TELEGRAM_CHAT_ID")
    if token and chat:
        return str(token), str(chat)
    return None, None


def split_message(text: str, limit: int = TELEGRAM_MAX_LEN) -> list:
    """Split on line boundaries so each chunk fits Telegram's limit."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:            # pathological single long line
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def send_telegram(text: str, token=None, chat_id=None, timeout=20) -> bool:
    """Send text (split if long). Returns True if every part was accepted."""
    if not (token and chat_id):
        token, chat_id = load_credentials()
    if not (token and chat_id):
        print("Telegram not configured: set TELEGRAM_TOKEN and "
              "TELEGRAM_CHAT_ID in .streamlit/secrets.toml")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for part in split_message(text):
        body = urllib.parse.urlencode({
            "chat_id": chat_id, "text": part,
            "disable_web_page_preview": "true",
        }).encode()
        try:
            with urllib.request.urlopen(url, data=body, timeout=timeout) as r:
                ok &= bool(json.load(r).get("ok"))
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            # Don't echo the URL -- it contains the bot token.
            print(f"Telegram send failed: {type(exc).__name__}: "
                  f"{getattr(exc, 'code', '') or getattr(exc, 'reason', '')}")
            ok = False
    return ok


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) or "stock-app: test message ✅"
    sys.exit(0 if send_telegram(msg) else 1)
