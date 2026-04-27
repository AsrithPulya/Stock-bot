"""
news_tools.py
=============
News-scraping helpers exposed as Gemini function-calling tools.

Sources (all free, no API key needed):
  1. Google News RSS  — broad English news for any query
  2. Economic Times Markets RSS — India-specific financial news
  3. Yahoo Finance (via yfinance) — per-ticker news

Function called by the LLM during its reasoning loop via Gemini tool use.
"""

import time
import urllib.parse
import feedparser
import yfinance as yf

# ── RSS feed templates ────────────────────────────────────────────────────────
_GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q={query}+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en"
)
_ET_MARKETS_RSS = (
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"
)
_LIVEMINT_RSS = (
    "https://www.livemint.com/rss/markets"
)

# Simple in-process cache: {cache_key: (timestamp, result)}
_news_cache: dict = {}
_CACHE_TTL_SECS = 300   # 5-minute freshness window


def _cached(key: str, fn):
    """Return cached result if fresh, else call fn() and cache it."""
    if key in _news_cache:
        ts, val = _news_cache[key]
        if time.time() - ts < _CACHE_TTL_SECS:
            return val
    result = fn()
    _news_cache[key] = (time.time(), result)
    return result


def get_stock_news(symbol: str, company_name: str = "") -> dict:
    """
    Fetch up to 6 recent news headlines for a stock.

    Searches Google News RSS and Yahoo Finance for the given stock symbol
    and company name. Returns sentiment-rich context for trading decisions.

    Use this to detect macro shocks (war, rate hikes, currency moves),
    company-specific events (earnings beats, CEO exits, contract wins),
    and sector trends that might warrant early exits or aggressive entries.

    Args:
        symbol: NSE/BSE ticker symbol (e.g. "TCS", "RELIANCE", "TATSILV")
        company_name: Full company name for better search results (optional)

    Returns:
        dict with keys:
          - symbol: str
          - headlines: list[str]  — up to 6 headlines, newest first
          - sources: list[str]    — corresponding source names
    """
    def _fetch():
        headlines, sources = [], []
        query_term = urllib.parse.quote(company_name if company_name else symbol)

        # 1. Google News RSS
        try:
            url = _GOOGLE_NEWS_RSS.format(query=query_term)
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                title = entry.get("title", "").strip()
                source = entry.get("source", {}).get("title", "Google News")
                if title:
                    headlines.append(title)
                    sources.append(source)
        except Exception:
            pass

        # 2. Yahoo Finance news
        try:
            yf_sym = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
            ticker = yf.Ticker(yf_sym)
            for item in (ticker.news or [])[:3]:
                title = item.get("title", "").strip()
                if title and title not in headlines:
                    headlines.append(title)
                    sources.append("Yahoo Finance")
        except Exception:
            pass

        return {
            "symbol": symbol,
            "headlines": headlines[:6],
            "sources": sources[:6],
        }

    return _cached(f"news_{symbol}", _fetch)


def get_market_news() -> dict:
    """
    Fetch top 8 broader Indian market headlines from Economic Times and Livemint.

    Use this to detect macro events that affect the entire portfolio:
    RBI rate decisions, budget announcements, geopolitical events (war, sanctions),
    global cues (US Fed, crude oil shocks, currency crises).

    Returns:
        dict with keys:
          - headlines: list[str]  — up to 8 market-wide headlines
          - sources: list[str]    — corresponding source names
    """
    def _fetch():
        headlines, sources = [], []
        for rss_url, src_name in [(_ET_MARKETS_RSS, "ET Markets"),
                                   (_LIVEMINT_RSS,   "Livemint")]:
            try:
                feed = feedparser.parse(rss_url)
                for entry in feed.entries[:4]:
                    title = entry.get("title", "").strip()
                    if title and title not in headlines:
                        headlines.append(title)
                        sources.append(src_name)
            except Exception:
                pass
        return {"headlines": headlines[:8], "sources": sources[:8]}

    return _cached("market_news", _fetch)
