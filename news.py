"""
news.py -- latest headlines for a stock, from free sources only (mockup 16/18).

1. Yahoo Finance news (via yfinance) when it answers;
2. otherwise Google News RSS (free, no key), in Hebrew for .TA stocks.

Only headlines, source, time and link are used (no article text).
fetch_headlines() never raises; parse_rss() is pure and unit-tested.
"""

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

TIMEOUT = 12
UA = "Mozilla/5.0 (compatible; stock-app)"


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else None


def parse_rss(xml_bytes, limit=5):
    """Google News RSS bytes -> [{"title", "source", "published", "link"}]."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        # Google appends " - Source" to the title; drop it, we show it apart.
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)].strip()
        try:
            published = _iso(parsedate_to_datetime(item.findtext("pubDate") or ""))
        except (TypeError, ValueError):
            published = None
        if title:
            out.append({"title": title, "source": source, "published": published,
                        "link": (item.findtext("link") or "").strip()})
        if len(out) >= limit:
            break
    return out


def _from_yahoo(symbol, limit):
    import yfinance as yf
    out = []
    for raw in yf.Ticker(symbol).get_news(count=limit) or []:
        c = raw.get("content") or raw
        title = (c.get("title") or "").strip()
        if not title:
            continue
        prov = c.get("provider") or {}
        url = (c.get("canonicalUrl") or {}).get("url") or c.get("link") or ""
        pub = c.get("pubDate") or c.get("providerPublishTime")
        if isinstance(pub, (int, float)):
            pub = _iso(datetime.fromtimestamp(pub, timezone.utc))
        out.append({"title": title, "source": prov.get("displayName", "") if isinstance(prov, dict) else "",
                    "published": pub, "link": url})
        if len(out) >= limit:
            break
    return out


def google_query(symbol, name):
    """(query, hl, gl, ceid) -- Hebrew edition for Tel-Aviv stocks."""
    base = (name or symbol).split(",")[0]
    for suffix in (" Inc.", " Inc", " Corporation", " Corp.", " Ltd", " Limited", " plc"):
        base = base.replace(suffix, "")
    if symbol.upper().endswith(".TA"):
        return f"{base} מניה when:7d", "iw", "IL", "IL:he"
    return f"{base} stock when:7d", "en-US", "US", "US:en"


def _from_google(symbol, name, limit):
    q, hl, gl, ceid = google_query(symbol, name)
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": hl, "gl": gl, "ceid": ceid})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return parse_rss(r.read(), limit)


def fetch_headlines(symbol, name="", limit=5):
    """Latest headlines (newest sources first as the feed gives them). Never raises."""
    for getter in (lambda: _from_yahoo(symbol, limit),
                   lambda: _from_google(symbol, name, limit)):
        try:
            items = getter()
        except Exception:
            items = []
        if items:
            return items
    return []


def age_he(published_iso, now=None):
    """'לפני 3 שעות' / 'אתמול' / 'לפני 4 ימים' (Hebrew, rough)."""
    if not published_iso:
        return ""
    try:
        t = datetime.fromisoformat(published_iso)
    except ValueError:
        return ""
    now = now or datetime.now(timezone.utc)
    hours = (now - t).total_seconds() / 3600
    if hours < 1:
        return "לפני פחות משעה"
    if hours < 24:
        return f"לפני {int(hours)} שעות"
    days = int(hours // 24)
    return "אתמול" if days == 1 else f"לפני {days} ימים"
