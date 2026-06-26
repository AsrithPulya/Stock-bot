"""
market_tools.py
===============
Market intelligence tools exposed as Gemini function-calling tools.

All data sourced from yfinance (free, no API key needed) and NSE public endpoints.

Tools provided:
  get_price_history(symbol)     — raw OHLCV ticks
  get_fundamentals(symbol)      — P/E, EPS, market cap, 52w range
  get_fii_dii_activity()        — NSE institutional flows (FII vs DII)
  get_macro_snapshot()          — Nifty, USD/INR, Crude, India VIX
  get_sector_performance()      — sector index % changes today
  get_economic_calendar()       — RBI dates, key macro events
"""

import time
import datetime
import requests
import yfinance as yf
import os
import contextlib
import json
import os

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {}
_CACHE_TTL = 300   # 5 minutes


def _cached(key: str, fn):
    if key in _cache:
        ts, val = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return val
    result = fn()
    _cache[key] = (time.time(), result)
    return result


# ── NSE helpers ───────────────────────────────────────────────────────────────

def _nse_json(url: str, timeout: int = 10) -> dict:
    """
    NSE endpoints often require a session cookie + browser-like headers.
    This helper tries a minimal session bootstrap.
    """
    s = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    }
    # Bootstrap cookie
    try:
        s.get("https://www.nseindia.com", headers=headers, timeout=timeout)
    except Exception:
        pass
    r = s.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_index_constituents(index_name: str = "NIFTY 500") -> dict:
    """
    Fetch constituents for an NSE index (e.g., NIFTY 500) from NSE public endpoint.

    Returns:
      {
        "index": "NIFTY 500",
        "symbols": ["RELIANCE", "TCS", ...],
        "count": 500
      }
    """
    def _fetch():
        try:
            url = "https://www.nseindia.com/api/equity-stockIndices?index=" + requests.utils.quote(index_name)
            data = _nse_json(url, timeout=12)
            symbols = []
            for row in data.get("data", []) or []:
                sym = (row.get("symbol") or "").strip()
                if sym:
                    symbols.append(sym)
            # De-dup while preserving order
            seen = set()
            uniq = []
            for s in symbols:
                if s not in seen:
                    uniq.append(s)
                    seen.add(s)
            return {"index": index_name, "symbols": uniq, "count": len(uniq)}
        except Exception as e:
            return {"index": index_name, "symbols": [], "count": 0, "error": str(e)}

    return _cached(f"idx_const_{index_name}", _fetch)


def save_universe_symbols(symbols: list, path: str) -> dict:
    """
    Persist a universe list to disk (one symbol per line).
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cleaned = []
        seen = set()
        for s in symbols or []:
            sym = str(s).strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            cleaned.append(sym)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(cleaned) + ("\n" if cleaned else ""))
        return {"ok": True, "count": len(cleaned), "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}


def load_universe_symbols(path: str) -> dict:
    """
    Load a universe list from disk (one symbol per line).
    """
    try:
        if not os.path.exists(path):
            return {"ok": False, "symbols": [], "count": 0, "path": path}
        with open(path, "r", encoding="utf-8") as f:
            syms = [ln.strip().upper() for ln in f.read().splitlines() if ln.strip()]
        # De-dup preserve order
        seen = set()
        uniq = []
        for s in syms:
            if s not in seen:
                uniq.append(s)
                seen.add(s)
        return {"ok": True, "symbols": uniq, "count": len(uniq), "path": path}
    except Exception as e:
        return {"ok": False, "symbols": [], "count": 0, "path": path, "error": str(e)}

# ── Kill Switch ───────────────────────────────────────────────────────────────

def check_market_health() -> dict:
    """
    Market Kill Switch — triggers Sentry Mode when market stress is detected.

    Conditions (any one triggers KILL_SWITCH = True):
      1. India VIX > 25           — extreme fear, elevated crash risk
      2. VIX 1-day jump > 15%    — sudden volatility spike (flash crash signal)
      3. Nifty 50 drop > 1.5%    — broad market sell-off in progress

    In Sentry Mode the bot HALTS all new BUY orders and only monitors
    existing positions for stop-loss exits. SELLs remain fully active.

    Returns:
        dict with keys:
          - KILL_SWITCH: bool
          - reason: str  — human-readable explanation
          - vix: float
          - vix_change_pct: float
          - nifty_change_pct: float
    """
    def _fetch():
        try:
            # Fetch VIX and Nifty — 3-day window to get yesterday + today
            with contextlib.redirect_stderr(open(os.devnull, 'w')):
                vix_hist   = yf.Ticker("^INDIAVIX").history(period="3d")
                nifty_hist = yf.Ticker("^NSEI").history(period="3d")

            kill = False
            reasons = []

            vix_now = vix_pct = nifty_pct = None

            if not vix_hist.empty and len(vix_hist) >= 2:
                vix_now  = float(vix_hist["Close"].iloc[-1])
                vix_prev = float(vix_hist["Close"].iloc[-2])
                vix_pct  = round((vix_now - vix_prev) / vix_prev * 100, 2) if vix_prev > 0 else 0

                if vix_now > 25:
                    kill = True
                    reasons.append(f"VIX={vix_now:.1f} > 25 (extreme fear)")
                if vix_pct > 15:
                    kill = True
                    reasons.append(f"VIX spiked +{vix_pct:.1f}% in 1 day (volatility shock)")

            if not nifty_hist.empty and len(nifty_hist) >= 2:
                n_now  = float(nifty_hist["Close"].iloc[-1])
                n_prev = float(nifty_hist["Close"].iloc[-2])
                nifty_pct = round((n_now - n_prev) / n_prev * 100, 2) if n_prev > 0 else 0

                if nifty_pct < -1.5:
                    kill = True
                    reasons.append(f"Nifty50 dropped {nifty_pct:.2f}% today (broad sell-off)")

            if not reasons:
                reasons.append("Market healthy — all conditions within normal range")

            return {
                "KILL_SWITCH":      kill,
                "reason":           " | ".join(reasons),
                "vix":              round(vix_now, 2) if vix_now else None,
                "vix_change_pct":   vix_pct,
                "nifty_change_pct": nifty_pct,
            }

        except Exception as e:
            # On error, default to safe — do NOT trigger kill switch on network glitch
            return {
                "KILL_SWITCH":    False,
                "reason":         f"Health check failed ({e}) — defaulting to SAFE",
                "vix":            None,
                "vix_change_pct": None,
                "nifty_change_pct": None,
            }

    return _cached("kill_switch", _fetch)


# ── Tool 1: Price History ─────────────────────────────────────────────────────

def get_price_history(symbol: str, exchange: str = "NSE") -> dict:
    """
    Return the last 20 closing prices for a stock from Yahoo Finance.

    Use this to spot trends, compute your own RSI/MACD/Bollinger Bands,
    identify support/resistance levels, or detect momentum shifts.
    Prices are in the stock's native currency (INR for NSE, USD for US stocks).

    Args:
        symbol:   Ticker (e.g. "TCS", "RELIANCE", "AAPL")
        exchange: "NSE" (default) or "US"

    Returns:
        dict with keys:
          - symbol, exchange
          - prices: list[float]  — last 20 closing prices, oldest first
          - current_price: float — latest price
          - pct_change_today: float — % change vs yesterday
    """
    def _fetch():
        yf_sym = symbol if exchange == "US" else f"{symbol}.NS"
        try:
            with contextlib.redirect_stderr(open(os.devnull, 'w')):
                data = yf.Ticker(yf_sym).history(period="30d")
                closes = [round(float(p), 2) for p in data["Close"].tolist()][-20:]
            
            # GROWW FALLBACK FOR NSE STOCKS IF YFINANCE FAILS
            if not closes and exchange == "NSE":
                url = f"https://groww.in/v1/api/stocks_data/v1/tr_live_prices/exchange/NSE/segment/CASH/{symbol}/latest"
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
                if r.status_code == 200 and 'ltp' in r.json():
                    p = float(r.json()['ltp'])
                    closes = [p] * 20
            
            pct = 0.0
            if len(closes) >= 2 and closes[-2] > 0:
                pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
            return {
                "symbol": symbol, "exchange": exchange,
                "prices": closes,
                "current_price": closes[-1] if closes else 0,
                "pct_change_today": pct,
            }
        except Exception as e:
            return {"symbol": symbol, "exchange": exchange,
                    "prices": [], "error": str(e)}

    return _cached(f"price_{symbol}_{exchange}", _fetch)


# ── Tool 2: Fundamentals ──────────────────────────────────────────────────────

def get_fundamentals(symbol: str, exchange: str = "NSE") -> dict:
    """
    Fetch key fundamental data for a stock via Yahoo Finance.

    Use this to assess valuation before buying — is the stock overpriced
    relative to earnings? Is it near a 52-week high (risk) or low (opportunity)?
    High insider/promoter holding suggests management confidence.

    Args:
        symbol:   Ticker (e.g. "TCS", "INFY")
        exchange: "NSE" (default) or "US"

    Returns:
        dict with keys:
          - trailingPE, forwardPE     — valuation multiples
          - earningsPerShare           — EPS
          - marketCap                  — in native currency
          - fiftyTwoWeekHigh/Low       — trading range
          - dividendYield              — as decimal (e.g. 0.015 = 1.5%)
          - heldPercentInsiders        — promoter/insider holding %
          - recommendationKey          — analyst consensus (buy/hold/sell)
    """
    def _fetch():
        yf_sym = symbol if exchange == "US" else f"{symbol}.NS"
        try:
            with contextlib.redirect_stderr(open(os.devnull, 'w')):
                info = yf.Ticker(yf_sym).info
            keys = [
                "trailingPE", "forwardPE", "earningsPerShare",
                "marketCap", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
                "dividendYield", "heldPercentInsiders", "recommendationKey",
                "sector", "industry",
            ]
            result = {k: info.get(k) for k in keys} if info else {}
            result["symbol"] = symbol
            return result
        except Exception as e:
            return {"symbol": symbol, "error": str(e)}

    return _cached(f"fund_{symbol}_{exchange}", _fetch)


# ── Tool 3: FII/DII Activity ──────────────────────────────────────────────────

def get_fii_dii_activity() -> dict:
    """
    Fetch today's FII (Foreign Institutional Investor) and DII
    (Domestic Institutional Investor) net buy/sell activity from NSE India.

    FII selling heavily → bearish market sentiment, consider reducing exposure.
    DII buying while FII sells → domestic confidence, may absorb selling.
    Both buying → strong bull market signal.
    Both selling → risk-off environment, stay defensive.

    Returns:
        dict with keys:
          - date: str
          - fii_net_crores: float  — positive = net buy, negative = net sell
          - dii_net_crores: float
          - fii_buy_crores, fii_sell_crores
          - dii_buy_crores, dii_sell_crores
          - sentiment: str  — "BULLISH" | "BEARISH" | "MIXED" | "NEUTRAL"
    """
    def _fetch():
        try:
            # NSE public API — returns JSON with FII/DII provisional data
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(
                "https://www.nseindia.com/api/fiidiiTradeReact",
                headers=headers, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            # NSE returns a list; first entry is usually today's provisional
            row = data[0] if data else {}
            fii_buy  = float(row.get("fiiBuyValue",  0) or 0)
            fii_sell = float(row.get("fiiSellValue", 0) or 0)
            dii_buy  = float(row.get("diiBuyValue",  0) or 0)
            dii_sell = float(row.get("diiSellValue", 0) or 0)
            fii_net  = round(fii_buy - fii_sell, 2)
            dii_net  = round(dii_buy - dii_sell, 2)

            if fii_net > 0 and dii_net > 0:
                sentiment = "BULLISH"
            elif fii_net < 0 and dii_net < 0:
                sentiment = "BEARISH"
            elif fii_net < -1000:
                sentiment = "BEARISH"
            elif fii_net > 0 or dii_net > 0:
                sentiment = "MIXED"
            else:
                sentiment = "NEUTRAL"

            return {
                "date":              row.get("date", datetime.date.today().isoformat()),
                "fii_net_crores":  fii_net,
                "fii_buy_crores":  round(fii_buy,  2),
                "fii_sell_crores": round(fii_sell, 2),
                "dii_net_crores":  dii_net,
                "dii_buy_crores":  round(dii_buy,  2),
                "dii_sell_crores": round(dii_sell, 2),
                "sentiment":       sentiment,
            }
        except Exception as e:
            return {"error": str(e), "sentiment": "UNKNOWN"}

    return _cached("fii_dii", _fetch)


# ── Tool 4: Macro Snapshot ────────────────────────────────────────────────────

def get_macro_snapshot() -> dict:
    """
    Fetch key macro indicators: Nifty50, USD/INR, Crude Oil, India VIX.

    Use this to set the overall market mood before making trade decisions.
    High VIX → fearful market, be cautious. Low VIX → calm, good for buying.
    Rising crude → bad for auto, airlines, paint stocks; good for ONGC.
    Strengthening USD/INR → bad for import-heavy sectors; good for IT exporters.
    Falling Nifty → risk-off, raise cash. Rising Nifty → deploy capital.

    Returns:
        dict with keys:
          - nifty50:      {price, pct_change}
          - usdinr:       {price, pct_change}
          - crude_oil:    {price_usd, pct_change}
          - india_vix:    {value, signal}  — "LOW"/<15 "ELEVATED"/15-25 "HIGH"/>25
          - market_mood:  "BULLISH" | "CAUTIOUS" | "BEARISH"
    """
    def _fetch():
        tickers = {
            "nifty50":   "^NSEI",
            "usdinr":    "USDINR=X",
            "crude_oil": "CL=F",
            "india_vix": "^INDIAVIX",
        }
        result = {}
        for label, sym in tickers.items():
            try:
                with contextlib.redirect_stderr(open(os.devnull, 'w')):
                    hist = yf.Ticker(sym).history(period="2d")
                if not hist.empty:
                    closes = list(hist["Close"])
                    price  = round(float(closes[-1]), 2)
                    pct    = 0.0
                    if len(closes) >= 2 and closes[-2] > 0:
                        pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
                    result[label] = {"price": price, "pct_change": pct}
                else:
                    result[label] = {"price": None, "pct_change": 0}
            except Exception as e:
                result[label] = {"error": str(e)}

        # Interpret VIX
        vix_val = result.get("india_vix", {}).get("price", 15)
        if vix_val:
            vix_signal = "HIGH" if vix_val > 25 else "ELEVATED" if vix_val > 15 else "LOW"
        else:
            vix_signal = "UNKNOWN"
        if "india_vix" in result:
            result["india_vix"]["signal"] = vix_signal

        # Derive market mood
        nifty_pct = result.get("nifty50", {}).get("pct_change", 0) or 0
        if nifty_pct > 0.5 and vix_val and vix_val < 20:
            mood = "BULLISH"
        elif nifty_pct < -0.5 or (vix_val and vix_val > 22):
            mood = "BEARISH"
        else:
            mood = "CAUTIOUS"
        result["market_mood"] = mood
        return result

    return _cached("macro", _fetch)


# ── Tool 5: Sector Performance ────────────────────────────────────────────────

def get_sector_performance() -> dict:
    """
    Fetch today's % change for major NSE sector indices.

    Use this to identify which sectors are in/out of favour before picking stocks.
    Rotate into the strongest sector; avoid laggards unless there's a specific
    contrarian reason backed by news or fundamentals.

    Returns:
        dict mapping sector name → {price, pct_change_today}
        Sectors: IT, Bank, Auto, Pharma, FMCG, Energy, Metal, Realty, Media
    """
    def _fetch():
        sectors = {
            "IT":      "^CNXIT",
            "Bank":    "^NSEBANK",
            "Auto":    "^CNXAUTO",
            "Pharma":  "^CNXPHARMA",
            "FMCG":    "^CNXFMCG",
            "Energy":  "^CNXENERGY",
            "Metal":   "^CNXMETAL",
            "Realty":  "^CNXREALTY",
            "Media":   "^CNXMEDIA",
        }
        result = {}
        for name, sym in sectors.items():
            try:
                with contextlib.redirect_stderr(open(os.devnull, 'w')):
                    hist = yf.Ticker(sym).history(period="2d")
                if not hist.empty:
                    closes = list(hist["Close"])
                    price  = round(float(closes[-1]), 2)
                    pct    = 0.0
                    if len(closes) >= 2 and closes[-2] > 0:
                        pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
                    result[name] = {"price": price, "pct_change_today": pct}
            except Exception:
                pass
        # Sort by % change descending for easy reading
        result = dict(sorted(result.items(),
                             key=lambda x: x[1].get("pct_change_today", 0),
                             reverse=True))
        return result

    return _cached("sectors", _fetch)


# ── Tool 6: Economic Calendar ─────────────────────────────────────────────────

def get_economic_calendar() -> dict:
    """
    Return upcoming high-impact economic events for India and global markets.

    CRITICAL USE CASES:
    - Avoid initiating large positions the day before RBI MPC decisions
    - Exit or hedge before US CPI/Fed meeting if macro exposure is high
    - Use earnings dates to either position ahead or wait for clarity

    Returns:
        dict with keys:
          - rbi_mpc_dates:     list[str]  — remaining 2026 RBI MPC meeting dates
          - upcoming_events:   list[dict] — {date, event, impact: HIGH/MEDIUM}
          - warning:           str | None — if a major event is within 3 days
    """
    # 2026 RBI MPC scheduled dates (announced by RBI)
    RBI_MPC_2026 = [
        "2026-04-07", "2026-04-09",   # Q1
        "2026-06-05", "2026-06-06",
        "2026-08-05", "2026-08-07",   # Q2
        "2026-10-05", "2026-10-07",
        "2026-12-03", "2026-12-05",   # Q3
    ]

    # Other high-impact events (approximate — update as needed)
    HIGH_IMPACT = [
        {"date": "2026-02-01", "event": "India Union Budget 2026-27",      "impact": "HIGH"},
        {"date": "2026-03-31", "event": "India Q4 GDP & fiscal year close", "impact": "HIGH"},
        {"date": "2026-06-15", "event": "US Fed FOMC Decision",            "impact": "HIGH"},
        {"date": "2026-09-16", "event": "US Fed FOMC Decision",            "impact": "HIGH"},
        {"date": "2026-12-16", "event": "US Fed FOMC Decision",            "impact": "HIGH"},
    ]

    today = datetime.date.today()
    upcoming_rbi = [d for d in RBI_MPC_2026 if d >= today.isoformat()][:4]
    upcoming_events = [e for e in HIGH_IMPACT if e["date"] >= today.isoformat()][:5]

    # Warn if a major event is within 3 days
    warning = None
    for d in upcoming_rbi + [e["date"] for e in upcoming_events if e["impact"] == "HIGH"]:
        try:
            event_date = datetime.date.fromisoformat(d)
            delta = (event_date - today).days
            if 0 <= delta <= 3:
                warning = (
                    f"⚠️  HIGH-IMPACT EVENT IN {delta} DAY(S): {d} — "
                    "consider reducing position sizes or waiting for clarity."
                )
                break
        except Exception:
            pass

    return {
        "rbi_mpc_dates":   upcoming_rbi,
        "upcoming_events": upcoming_events,
        "warning":         warning,
    }
