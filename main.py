import datetime
import time
import random
import json
import os
import sys
import signal
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import yfinance as yf
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# Tool modules — autonomous LLM agent
import news_tools
import market_tools
import groww_broker
import agents
from trade_journal import TradeJournal
import performance_monitor
import concurrent.futures

# Files used by Streamlit dashboard
LOG_FILE = "bot_log.txt"
PID_FILE = "bot.pid"

# ══════════════════════════════════════════════════════════════════════════════
# SWING STRATEGY CONFIG (edit these, not the code)
# ══════════════════════════════════════════════════════════════════════════════
#
# Key requirement (per your request):
# - The bot should NOT sell just because a position is down a few percent.
# - It has time: days/weeks/months/years to let a swing play out.
# - Primary swing target: ~6–7% per trade.
#
SWING_PROFIT_TARGET_PCT = 6.5  # used as a reference in prompts/guards

# Loss exits:
# - If set to None, the engine never auto-sells losers (AI can still sell only if allow_loss=true).
# - If set (e.g. -20.0), it becomes the true hard stop-loss enforced in Python.
HARD_STOP_LOSS_PCT = None

# Optional time-based exit (None means "can hold indefinitely")
MAX_HOLD_DAYS = None

# Universe configuration
UNIVERSE_INDEX_NAME = "NIFTY 500"           # broad NSE coverage without “all NSE” overload
UNIVERSE_CACHE_PATH = os.path.join("data", "nse_universe_symbols.txt")
UNIVERSE_REFRESH_HOURS = 24                # refresh constituents daily
MAX_WATCHLIST = 20                         # REDUCED FOR 512MB RAM (was 60)
UNIVERSE_CANDIDATE_POOL = 40              # REDUCED FOR 512MB RAM (was 140)

# News-aware selection limits (to avoid timeouts)
THEME_CANDIDATE_POOL = 220                 # symbols to consider before news fetch
NEWS_SHORTLIST = 120                       # symbols to fetch stock-specific headlines for
FUNDAMENTALS_SHORTLIST = 180               # symbols to fetch fundamentals for (sector/industry)
WATCHLIST_RECALC_EVERY_LOOPS = 3           # cache watchlist for N loops to reduce API calls


def bot_log(msg: str):
    """Append a timestamped log line to LOG_FILE (read by Streamlit dashboard)."""
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════════
# TRADING MODES
# ──────────────────────────────────────────────────────────────────────────────
# TEST_MODE   — True  : bypass market hours, use simulated prices (safe for dev)
#             — False : live NSE/BSE market hours + real Yahoo Finance prices
#
# PAPER_TRADE — True  : orders are simulated only, NO real money moves  ← DEFAULT
#             — False : REAL orders sent to Groww (⚠️ requires Groww API subscription)
#
# CHECKLIST before setting PAPER_TRADE = False:
#   ☐ Groww Trading API subscribed (₹499/mo at groww.in/trade-api)
#   ☐ GROWW_API_KEY + GROWW_API_SECRET exported as env vars
#   ☐ Ran bot in PAPER_TRADE=True for ≥5 days with satisfactory results
#   ☐ TEST_MODE set to False (real prices)
# ══════════════════════════════════════════════════════════════════════════════
TEST_MODE   = False
PAPER_TRADE = True    # ←←← KEEP TRUE UNTIL READY TO GO LIVE

TEST_LOOP_SLEEP  = 60        # seconds between iterations in test mode
TEST_INITIAL_CASH = 25_000  # ₹25 Thousand starting capital


# ══════════════════════════════════════════════════════════════════════════════
# 2026 NSE / BSE Market Holidays (as published by NSE India)
# ══════════════════════════════════════════════════════════════════════════════
NSE_HOLIDAYS_2026 = [
    datetime.date(2026, 1, 26),   # Republic Day
    datetime.date(2026, 2, 26),   # Mahashivratri
    datetime.date(2026, 3, 20),   # Holi
    datetime.date(2026, 4, 2),    # Ram Navami
    datetime.date(2026, 4, 3),    # Good Friday
    datetime.date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    datetime.date(2026, 5, 1),    # Maharashtra Day
    datetime.date(2026, 6, 17),   # Id-Ul-Adha (Bakri Eid) *
    datetime.date(2026, 8, 15),   # Independence Day
    datetime.date(2026, 8, 27),   # Ganesh Chaturthi
    datetime.date(2026, 10, 2),   # Mahatma Gandhi Jayanti / Dussehra *
    datetime.date(2026, 10, 20),  # Diwali – Laxmi Puja *
    datetime.date(2026, 10, 21),  # Diwali – Balipratipada *
    datetime.date(2026, 11, 5),   # Guru Nanak Jayanti *
    datetime.date(2026, 12, 25),  # Christmas Day
]
# * Dates marked with * may shift by ±1 day based on lunar calendar.

# ══════════════════════════════════════════════════════════════════════════════
# 2026 US Market Holidays (NYSE / NASDAQ)
# ══════════════════════════════════════════════════════════════════════════════
US_HOLIDAYS_2026 = [
    datetime.date(2026, 1, 1),    # New Year's Day
    datetime.date(2026, 1, 19),   # Martin Luther King Jr. Day
    datetime.date(2026, 2, 16),   # Presidents' Day
    datetime.date(2026, 4, 3),    # Good Friday
    datetime.date(2026, 5, 25),   # Memorial Day
    datetime.date(2026, 6, 19),   # Juneteenth
    datetime.date(2026, 7, 3),    # Independence Day (observed)
    datetime.date(2026, 9, 7),    # Labor Day
    datetime.date(2026, 11, 26),  # Thanksgiving Day
    datetime.date(2026, 12, 25),  # Christmas Day
]


def is_market_open():
    """
    Check if NSE/BSE is currently open (IST 09:15–15:30 weekdays).
    In TEST_MODE always returns True.
    """
    if TEST_MODE:
        return True
    now = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    )
    if now.weekday() > 4:
        return False
    if now.date() in NSE_HOLIDAYS_2026:
        return False
    return datetime.time(9, 15) <= now.time() <= datetime.time(15, 30)

# ══════════════════════════════════════════════════════════════════════════════
# Price Fetching
# ══════════════════════════════════════════════════════════════════════════════

def get_simulated_price(symbol, last_price=None, exchange="NSE"):
    """
    Generate a realistic simulated price for TEST_MODE (±2% random walk).
    """
    NSE_PRICES = {
        "RELIANCE": 2900, "TCS": 3800, "INFY": 1800, "HCLTECH": 1650,
        "WIPRO": 560, "TECHM": 1550, "LTIM": 5500, "HDFCBANK": 1750,
        "ICICIBANK": 1300, "SBIN": 800, "KOTAKBANK": 1800, "AXISBANK": 1150,
        "BAJFINANCE": 7200, "BAJAJFINSV": 1700, "HINDUNILVR": 2400,
        "ITC": 470, "BRITANNIA": 5000, "TATACONSUM": 900, "MARUTI": 12500,
        "TATAMOTORS": 800, "EICHERMOT": 5200, "HEROMOTOCO": 4500,
        "TVSMOTOR": 2700, "SUNPHARMA": 1800, "CIPLA": 1550,
        "DRREDDY": 6500, "BHARTIARTL": 1900, "LT": 3700,
        "ULTRACEMCO": 12000, "NTPC": 380, "POWERGRID": 330,
        "ONGC": 270, "TATAPOWER": 440, "TATASTEEL": 150,
        "JSWSTEEL": 980, "HINDALCO": 660, "VEDL": 440,
        "ASIANPAINT": 2500, "TITAN": 3400, "ADANIPORTS": 1250,
        "HAL": 4200, "BEL": 290,
        # ETF & Small-cap
        "TATSILV": 160, "SYLPH": 25,
        # New 20 Small / Mid-cap / Growth Stocks & Funds
        "SUZLON": 60, "IREDA": 250, "RVNL": 450, "IRFC": 150,
        "ZOMATO": 280, "PAYTM": 700, "JIOFIN": 330, "AWL": 350,
        "MAZDOCK": 4200, "ANGELONE": 2800, "BSE": 2600, "CDSL": 1400,
        "KALYANKJIL": 600, "RAILTEL": 450, "PNB": 110, "NHPC": 90,
        "SJVN": 120, "HUDCO": 220, "NBCC": 115, 
        "SMALLCAP": 200, # Nippon India ETF Nifty Smallcap 250
    }
    US_PRICES = {
        "AAPL": 220, "MSFT": 415, "NVDA": 850, "GOOGL": 175,
        "META": 560, "AMZN": 210, "TSLA": 250, "NFLX": 700,
        "AMD": 145, "INTC": 22, "CRM": 310, "ORCL": 160,
        "PYPL": 75, "SHOP": 105, "COIN": 230, "PLTR": 28,
        "JPM": 240, "BAC": 45, "GS": 550, "V": 300,
    }
    prices = US_PRICES if exchange == "US" else NSE_PRICES
    base = last_price if last_price else prices.get(symbol, 100)
    change_pct = random.uniform(-0.02, 0.02)
    return round(base * (1 + change_pct), 2)


def get_real_stock_price(symbol, last_price=None, exchange="NSE"):
    """
    Fetch real-time stock price via Yahoo Finance.
    NSE stocks use '{symbol}.NS'; US stocks use '{symbol}' directly.
    Falls back to simulated price on error.
    """
    if TEST_MODE:
        return get_simulated_price(symbol, last_price, exchange)

    try:
        yf_symbol = symbol if exchange == "US" else f"{symbol}.NS"
        ticker = yf.Ticker(yf_symbol)
        data = ticker.history(period="5d")
        if not data.empty:
            return float(data['Close'].iloc[-1])
        info = ticker.info
        for key in ('currentPrice', 'regularMarketPrice', 'previousClose'):
            if info.get(key):
                return float(info[key])
    except Exception:
        pass

    return get_simulated_price(symbol, last_price, exchange)


def get_real_stock_news(symbol, exchange="NSE"):
    """
    Fetch the most recent news headline for a stock using news_tools.
    """
    if TEST_MODE:
        sentiments = [
            f"{symbol} posts strong quarterly results",
            f"Analysts upgrade {symbol} on solid outlook",
            f"{symbol} faces macro headwinds",
            f"Institutional buying seen in {symbol}",
            f"{symbol} wins major contract",
        ]
        return random.choice(sentiments)
    try:
        news_dict = news_tools.get_stock_news(symbol)
        headlines = news_dict.get("headlines", [])
        if headlines:
            return " | ".join(headlines)
    except Exception:
        pass
    return "Market conditions stable"


# ══════════════════════════════════════════════════════════════════════════════
# Gemini AI Decision Engine — Aggressive + Smart Strategy
# ══════════════════════════════════════════════════════════════════════════════

# Prefer env var; do not hardcode secrets in code.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

SYSTEM_INSTRUCTION = """\
You are an autonomous portfolio manager trading NSE/BSE Indian stocks.

═══════════════════════════════════════════════════════════
PRIMARY OBJECTIVE
═══════════════════════════════════════════════════════════
Generate a MINIMUM 5.0% MONTHLY RETURN on the total portfolio value.
5.0% per month = ~0.22% per day compounded. Calibrate position sizes
and trade frequency to achieve this consistently without over-trading.
Quality over quantity — one well-researched 5% trade beats five 1% trades.

═══════════════════════════════════════════════════════════
YOUR TOOLS — USE THEM BEFORE DECIDING
═══════════════════════════════════════════════════════════
You have 8 research tools and 1 execution tool. Always research first.

Suggested workflow each cycle:
  1. get_portfolio_state()          → Know your current position & P&L
  2. get_macro_snapshot()           → Is the overall market bull/bear?
  3. get_market_news()              → Any macro shock (war, Fed, RBI, crisis)?
  4. get_fii_dii_activity()         → Which way are institutions flowing?
  5. get_sector_performance()       → Which sectors are leading/lagging?
  6. get_economic_calendar()        → Any binary event in the next 3 days?
  7. For your top-5 candidate stocks:
       get_stock_news(symbol)       → Any company-specific catalyst?
       get_price_history(symbol)    → Price action, trend, momentum
       get_fundamentals(symbol)     → Valuation — is it cheap or stretched?
  8. place_order(symbol, action, qty) → Execute your decisions

You may call tools in any order and as many times as needed.

═══════════════════════════════════════════════════════════
MACRO & NEWS CONTEXT (for your awareness)
═══════════════════════════════════════════════════════════
Use news and macro data to enrich your judgment — not as rigid rules.
Here are some typical relationships worth being aware of:

• WAR / GEOPOLITICAL SHOCK:
    – Defence stocks that already ran up may be fully priced in
    – Import-heavy sectors (auto, paints, chemicals) often face cost pressure
    – Metal exporters, energy stocks may benefit — but check USD/INR first,
      since a strong dollar can hurt commodities (e.g. silver falling in war)

• STRONG USD / RISING USD-INR:
    – IT exporters (TCS, INFY, HCLTECH, WIPRO) earn in USD — tends to benefit
    – Import-heavy companies face higher costs
    – Domestic consumption stocks are largely insulated

• RBI RATE CHANGES:
    – Rate hikes tend to pressure NBFCs and rate-sensitive borrowers
    – Rate cuts tend to benefit housing finance, capex-heavy infra, NBFCs
    – PSU banks often react differently from private lenders

• CRUDE OIL MOVE:
    – Spike hurts downstream consumers (paint, tyre, airlines)
    – Benefits upstream producers (ONGC)

• BROAD MARKET SELL-OFF:
    – Quality large-caps historically recover; not all dips need a response
    – Hard stop-losses may be enforced automatically in Python based on configuration

═══════════════════════════════════════════════════════════
POSITION SIZING & CAPITAL RULES
═══════════════════════════════════════════════════════════
• Diversification: Do NOT allocate all capital to a single stock. Maintain a maximum limit of 30% of Total Portfolio Value per stock.
• Distribute cash across 3–4 high-conviction positions to reduce systemic exposure and avoid holding single-stock concentration risk.

═══════════════════════════════════════════════════════════
PROFIT BOOKING & LOSS CUTTING
═══════════════════════════════════════════════════════════
• Taking Profits: Evaluate taking profits when a position hits +7.0% or more.
  You are entirely in control. If technicals and news are still strong, let your winners run!
  If momentum is slowing or the adversarial case looks risky at these highs, SELL to lock in gains.
• Hard stop: Only enforced if configured in Python; otherwise avoid crystallizing small losses.
• Monthly target check: if running ahead of 5.0% monthly target,
  do NOT stop trading. 5.0% is a MINIMUM baseline, not a ceiling. Keep capturing upside and let winners run!
  protect gains — raise cash buffer. If behind, look for catching-up trades.
  Do NOT over-trade trying to catch up — wait for high-conviction setups.

═══════════════════════════════════════════════════════════
ADVERSARIAL DECISION PROTOCOL — MANDATORY FOR EVERY BUY
═══════════════════════════════════════════════════════════
Before authorising any BUY, you must play Devil's Advocate:

STEP 1 — GENERATE THE BEAR CASE:
  Ask yourself: "Why could this trade fail?"
  Consider: sector headwinds, elevated VIX, RSI divergence, weak FII flows,
  upcoming binary events (earnings, RBI), recent negative news, high valuation.
  Be pessimistic. Write the strongest possible case AGAINST this trade.

STEP 2 — REBUT OR STAND DOWN:
  Only authorise the BUY if you can clearly articulate WHY the bulls
  outweigh the bears. If you cannot convincingly rebut the bear case— HOLD.
  A trade you aren't sure about is a trade you shouldn't take.

This protocol exists to prevent emotional momentum chasing and overtrading.

═══════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════
After your research, return ONLY a valid JSON array — no markdown:
[
  {
    "action": "BUY",
    "symbol": "TCS",
    "quantity": 25,
    "bear_case": "IT sector faces US recession fears; TCS guidance was cautious last quarter.",
    "reason": "Bull case overrides: strong USD tailwind (+3% move), FII net buying ₹800cr in IT sector, RSI at 42 (oversold). Bear case doesn't hold given currency momentum."
  },
  {
    "action": "SELL",
    "symbol": "INFY",
    "quantity": 10,
    "reason": "..."
  },
  {
    "action": "HOLD",
    "reason": "No compelling opportunity this cycle."
  }
]
Notes:
- BUY decisions MUST include a non-empty bear_case field.
- SELL and HOLD decisions do NOT require bear_case.
- If only HOLD: [{"action": "HOLD", "reason": "..."}]
"""


# ── Gemini Rate Limiter ───────────────────────────────────────────────────────
# Model   : gemini-2.5-flash-lite / gemini-2.5-flash (Paid Tier)
# With autonomous tool calling, each tool response costs ~1 request.
# Paid tier handles high RPM, so artificial delays are removed for faster execution.
MIN_API_INTERVAL_SECS = 1     # small guard between loops
_last_gemini_call: float = 0.0


def get_multi_agent_portfolio_decision(portfolio_data, cash, trade_journal, simulator):
    """
    Orchestrates Researcher, Quant, Adversarial, and final Orchestrator agents.
    """
    bot_log(f"🤖 [Multi-Agent] Starting Parallel Analysis for {len(portfolio_data)} stocks...")

    # Data transformation for agents
    news_data = {s['symbol']: s['news'] for s in portfolio_data}
    price_data = {
        s['symbol']: {
            "price": s['price'],
            "tech": s['tech'],
        } for s in portfolio_data
    }
    
    # 1. Global Macro-Economist Execution
    bot_log("🌐 [Economist Agent] Checking global macro data and market news...")
    macro_data = market_tools.get_macro_snapshot()
    sector_data = market_tools.get_sector_performance()
    market_news = news_tools.get_market_news()
    
    economist_map = agents.run_economist_agent(macro_data, sector_data, market_news)
    bullish_sectors = [s for s, data in economist_map.items() if data.get('sentiment') == 'BULLISH']
    bearish_sectors = [s for s, data in economist_map.items() if data.get('sentiment') == 'BEARISH']
    bot_log(f"🌐 [Economist] Bullish Sectors: {bullish_sectors} | Bearish Sectors: {bearish_sectors}")

    # 2. Sequential Execution for Researcher & Quant
    bot_log("🤖 [Research Agent] Running...")
    researcher_map = agents.run_researcher_agent(news_data)
    
    bot_log("🤖 [Quant Agent] Running...")
    quant_map = agents.run_quant_agent(price_data)

    bot_log(f"✅ [Research] Analyzed {len(researcher_map)} stocks out of {len(portfolio_data)}")
    bot_log(f"✅ [Quant] Analyzed {len(quant_map)} stocks out of {len(portfolio_data)}")

    # 2. Gate / Intersection Logic
    candidates = []
    for s in portfolio_data:
        sym = s['symbol']
        sent = str(researcher_map.get(sym, {}).get("sentiment", "Neutral")).strip().upper()
        signal = str(quant_map.get(sym, {}).get("signal", "Neutral")).strip().upper()
        
        # Pass candidates that are strong technically OR have bullish news.
        # This handles cases where news is absent (Neutral) but chart is Strong, or vice versa.
        if (sent == "BULLISH" and signal in ["STRONG", "NEUTRAL"]) or \
           (signal == "STRONG" and sent in ["BULLISH", "NEUTRAL"]):
            candidates.append(sym)

    bot_log(f"🔥 [Gate] Found {len(candidates)} Strong+Bullish candidates: {candidates}")

    # 3. Adversarial Agent
    adv_map = {}
    if candidates:
        bot_log("👿 [Adversarial Agent] Generating Bear Cases for candidates...")
        adv_map = agents.run_adversarial_agent(candidates, portfolio_data)
    else:
        bot_log("👿 [Adversarial Agent] Skipped (no candidates)")

    # 4. Orchestrator
    bot_log("👑 [Orchestrator] Reviewing all agents and Trade Journal context...")
    
    # Filter data for Orchestrator to save tokens and avoid noise
    # We only care about: 1. Current holdings, 2. Buy candidates
    held_symbols = [s['symbol'] for s in portfolio_data if s.get('shares', 0) > 0]
    relevant_symbols = list(set(candidates + held_symbols))
    
    filtered_portfolio = [s for s in portfolio_data if s['symbol'] in relevant_symbols]
    filtered_researcher = {sym: researcher_map[sym] for sym in relevant_symbols if sym in researcher_map}
    filtered_quant = {sym: quant_map[sym] for sym in relevant_symbols if sym in quant_map}
    filtered_adv = {sym: adv_map[sym] for sym in relevant_symbols if sym in adv_map}

    # Build complete portfolio state for orchestrator
    full_state = {
        "portfolio": filtered_portfolio,
        "market_health": market_tools.check_market_health(),
    }
    
    # Get last 5 trades for context
    tj_context = trade_journal.get_last_5_trades()

    decisions = agents.run_orchestrator(
        portfolio_state=full_state,
        researcher_map=filtered_researcher,
        quant_map=filtered_quant,
        adv_map=filtered_adv,
        economist_map=economist_map,
        trade_journal_context=tj_context,
        cash=cash
    )
    
    # Validate structure
    valid = []
    if isinstance(decisions, list):
        for d in decisions:
            action = str(d.get("action", "")).strip().upper()
            if action in ("BUY", "SELL", "HOLD"):
                d["action"] = action
                valid.append(d)
    
    return valid if valid else [{"action": "HOLD", "reason": "No valid decisions parsed"}]


# ══════════════════════════════════════════════════════════════════════════════
# Multi-Stock Trading Simulator
# ══════════════════════════════════════════════════════════════════════════════

class MultiStockTradingSimulator:
    """
    Multi-stock portfolio trading bot for NSE/BSE and NYSE/NASDAQ.
    Supports both live trading and TEST_MODE simulation.
    """

    STATE_FILE = "trading_bot_state.json"

    # Backward-compatible fallback list (used if universe fetch/cache unavailable)
    PORTFOLIO_STOCKS = {
        # IT
        "RELIANCE":   "Reliance Industries Ltd",
        "TCS":        "Tata Consultancy Services",
        "INFY":       "Infosys Ltd",
        "HCLTECH":    "HCL Technologies Ltd",
        "WIPRO":      "Wipro Ltd",
        "TECHM":      "Tech Mahindra Ltd",
        "LTIM":       "LTIMindtree Ltd",
        # Banking & Finance
        "HDFCBANK":   "HDFC Bank Ltd",
        "ICICIBANK":  "ICICI Bank Ltd",
        "SBIN":       "State Bank of India",
        "KOTAKBANK":  "Kotak Mahindra Bank Ltd",
        "AXISBANK":   "Axis Bank Ltd",
        "BAJFINANCE": "Bajaj Finance Ltd",
        "BAJAJFINSV": "Bajaj Finserv Ltd",
        # FMCG & Consumer
        "HINDUNILVR": "Hindustan Unilever Ltd",
        "ITC":        "ITC Ltd",
        "BRITANNIA":  "Britannia Industries Ltd",
        "TATACONSUM": "Tata Consumer Products Ltd",
        # Auto
        "MARUTI":     "Maruti Suzuki India Ltd",
        "TATAMOTORS": "Tata Motors Ltd",
        "EICHERMOT":  "Eicher Motors Ltd",
        "HEROMOTOCO": "Hero MotoCorp Ltd",
        "TVSMOTOR":   "TVS Motor Company Ltd",
        # Pharma
        "SUNPHARMA":  "Sun Pharmaceutical Industries Ltd",
        "CIPLA":      "Cipla Ltd",
        "DRREDDY":    "Dr Reddy's Laboratories Ltd",
        # Telecom
        "BHARTIARTL": "Bharti Airtel Ltd",
        # Infra & Cement
        "LT":         "Larsen & Toubro Ltd",
        "ULTRACEMCO": "UltraTech Cement Ltd",
        # Energy
        "NTPC":       "NTPC Ltd",
        "POWERGRID":  "Power Grid Corporation of India Ltd",
        "ONGC":       "Oil & Natural Gas Corporation Ltd",
        "TATAPOWER":  "Tata Power Company Ltd",
        "ADANIGREEN": "Adani Green Energy Ltd",
        # Metals
        "TATASTEEL":  "Tata Steel Ltd",
        "JSWSTEEL":   "JSW Steel Ltd",
        "HINDALCO":   "Hindalco Industries Ltd",
        "VEDL":       "Vedanta Ltd",
        # Others
        "ASIANPAINT": "Asian Paints Ltd",
        "TITAN":      "Titan Company Ltd",
        "ADANIPORTS": "Adani Ports and SEZ Ltd",
        "HAL":        "Hindustan Aeronautics Ltd",
        "BEL":        "Bharat Electronics Ltd",
        # ETF & Small-cap
        "TATSILV":    "Tata Silver ETF",
        "SYLPH":      "Sylph Industries Ltd",
        # New 20 Small / Mid-cap / Growth Stocks & Funds
        "SUZLON":     "Suzlon Energy Ltd",
        "IREDA":      "Indian Renewable Energy Dev Agency Ltd",
        "RVNL":       "Rail Vikas Nigam Ltd",
        "IRFC":       "Indian Railway Finance Corp Ltd",
        "ZOMATO":     "Zomato Ltd",
        "PAYTM":      "One 97 Communications Ltd",
        "JIOFIN":     "Jio Financial Services Ltd",
        "AWL":        "Adani Wilmar Ltd",
        "MAZDOCK":    "Mazagon Dock Shipbuilders Ltd",
        "ANGELONE":   "Angel One Ltd",
        "BSE":        "BSE Ltd",
        "CDSL":       "Central Depository Services Ltd",
        "KALYANKJIL": "Kalyan Jewellers India Ltd",
        "RAILTEL":    "RailTel Corporation of India Ltd",
        "PNB":        "Punjab National Bank",
        "NHPC":       "NHPC Ltd",
        "SJVN":       "SJVN Ltd",
        "HUDCO":      "Housing & Urban Development Corp Ltd",
        "NBCC":       "NBCC (India) Ltd",
        "SMALLCAP":   "Nippon India ETF Nifty Smallcap 250 Fund",
    }

    def __init__(self, initial_cash=TEST_INITIAL_CASH):
        self.trade_journal = TradeJournal()
        # Sync PAPER_TRADE mode to groww_broker so the flag travels with the bot
        groww_broker.PAPER_TRADE = PAPER_TRADE

        self._universe_last_refresh = None
        if self.load_state():
            print("📂 Loaded previous portfolio state!")
        else:
            self.cash         = initial_cash
            self.initial_cash = initial_cash
            self.total_trades = 0
            all_symbols = list(self.PORTFOLIO_STOCKS)
            self.holdings   = {sym: 0   for sym in all_symbols}
            self.avg_cost   = {sym: 0.0 for sym in all_symbols}
            self.stock_data = {sym: {"price_history": []} for sym in all_symbols}
            # T+1 settlement tracker: symbol -> ISO date string of last buy
            self.bought_date: dict = {}
            # Trail & Scale tracker: symbols for which 50% has been sold at +7.0%
            # Value = break-even price (original avg cost). Stop-loss now at this price.
            self.break_even_stop: dict = {}  # symbol -> float (entry price)
            self.unsettled_cash = [] # List of {amount: float, timestamp: str}
            print("🆕 Starting fresh multi-stock portfolio (NSE Only)!")

        # Universe storage (symbols only). Names are optional and fetched lazily.
        self.universe_symbols = []
        self.refresh_universe_if_needed(force=False)
        self._cached_watchlist = []
        self._cached_watchlist_until_iter = 0

    # ── Technical Indicators ──────────────────────────────────────────────────

    def calculate_technical_indicators(self, symbol):
        """Calculate RSI, SMA, trend, momentum and volatility for a stock."""
        history = self.stock_data[symbol]["price_history"]
        current = history[-1] if history else 0

        if len(history) < 2:
            return {
                "sma_5": current, "sma_10": current,
                "rsi": 50.0, "price_change_pct": 0.0,
                "trend": "NEUTRAL", "volatility": "LOW"
            }

        sma_5  = sum(history[-5:])  / min(5,  len(history))
        sma_10 = sum(history[-10:]) / min(10, len(history))

        price_change_pct = (
            (current - history[-2]) / history[-2] * 100
            if history[-2] > 0 else 0
        )

        # RSI
        gains, losses = [], []
        for i in range(1, min(14, len(history))):
            d = history[-i] - history[-(i+1)]
            (gains if d > 0 else losses).append(abs(d))
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = (sum(losses) / len(losses) if losses else 0) or 0.001
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        # Trend
        if current > sma_5 > sma_10:
            trend = "STRONG UPTREND"
        elif current > sma_5:
            trend = "UPTREND"
        elif current < sma_5 < sma_10:
            trend = "STRONG DOWNTREND"
        elif current < sma_5:
            trend = "DOWNTREND"
        else:
            trend = "NEUTRAL"

        # Volatility
        volatility = "LOW"
        if len(history) >= 5:
            r = max(history[-5:]) - min(history[-5:])
            v = (r / current * 100) if current > 0 else 0
            volatility = "HIGH" if v > 3 else "MEDIUM" if v > 1.5 else "LOW"

        return {
            "sma_5": sma_5, "sma_10": sma_10,
            "rsi": rsi, "price_change_pct": price_change_pct,
            "trend": trend, "volatility": volatility
        }

    # ── Data Fetching ─────────────────────────────────────────────────────────

    def fetch_all_stock_data(self):
        """
        Fetch prices and news concurrently for whichever market is open right now.
        - NSE session  (09:15–15:30 IST) → Indian stocks
        """
        portfolio_data = []
        nse_open = is_market_open()

        active_symbols = []
        if nse_open:
            # `iteration` is available in the main loop; pass 0 here for backward safety.
            watchlist = self.get_watchlist_symbols(max_watchlist=MAX_WATCHLIST, iteration=getattr(self, "_current_iteration", 0))
            for symbol in watchlist:
                # name is optional; fallback to symbol if unknown
                name = self.PORTFOLIO_STOCKS.get(symbol, symbol)
                active_symbols.append((symbol, name, "NSE", "INR"))

        def fetch_symbol_data(item):
            symbol, name, exchange, currency = item
            if symbol not in self.stock_data:
                self.stock_data[symbol] = {"price_history": []}
            if symbol not in self.holdings:
                self.holdings[symbol] = 0
            if symbol not in self.avg_cost:
                self.avg_cost[symbol] = 0.0

            last = (
                self.stock_data[symbol]["price_history"][-1]
                if self.stock_data[symbol]["price_history"] else None
            )
            price = get_real_stock_price(symbol, last_price=last, exchange=exchange)
            if price is None:
                price = last if last else None
            if price is None:
                return None

            # Temporarily modify local history to calculate indicators, 
            # we will commit thread-safely after
            history = list(self.stock_data[symbol]["price_history"])
            history.append(price)
            if len(history) > 20:
                history.pop(0)

            tech = self.calculate_technical_indicators(symbol)
            news = get_real_stock_news(symbol, exchange=exchange)
            
            # Slippage Monitor
            avg_slip = performance_monitor.get_average_weekly_slippage(symbol, self.trade_journal)

            return {
                "symbol":   symbol,
                "name":     name,
                "price":    price,
                "history":  history,
                "currency": currency,
                "exchange": exchange,
                "shares":   self.holdings[symbol],
                "avg_cost": self.avg_cost[symbol],
                "tech":     tech,
                "news":     news,
                "average_slippage": round(avg_slip, 4)
            }

        # Concurrently fetch data for speed
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(fetch_symbol_data, active_symbols))
            
        for res in results:
            if res:
                # Thread-safe commit to state
                sym = res["symbol"]
                self.stock_data[sym]["price_history"] = res["history"]
                
                # Cleanup internal key before yielding
                del res["history"]
                portfolio_data.append(res)

        return portfolio_data

    # ── Universe & Watchlist ─────────────────────────────────────────────────

    def refresh_universe_if_needed(self, force: bool = False):
        """
        Keep a broad NSE universe list on disk and in memory.
        Uses NSE index constituents (default NIFTY 500) for coverage.
        """
        # Load from disk first (fast path)
        loaded = market_tools.load_universe_symbols(UNIVERSE_CACHE_PATH)
        if loaded.get("ok") and loaded.get("symbols"):
            self.universe_symbols = loaded["symbols"]

        now = datetime.datetime.now()
        if self._universe_last_refresh is None and self.universe_symbols:
            self._universe_last_refresh = now

        if not force and self._universe_last_refresh:
            age_h = (now - self._universe_last_refresh).total_seconds() / 3600
            if age_h < UNIVERSE_REFRESH_HOURS and self.universe_symbols:
                return

        # Try fetch from NSE endpoint
        idx = market_tools.get_index_constituents(UNIVERSE_INDEX_NAME)
        syms = idx.get("symbols") or []
        if syms:
            market_tools.save_universe_symbols(syms, UNIVERSE_CACHE_PATH)
            self.universe_symbols = syms
            self._universe_last_refresh = now
            bot_log(f"🧠 Universe refreshed: {UNIVERSE_INDEX_NAME} ({len(syms)} symbols)")
            return

        # Fallback to hardcoded list if everything fails
        if not self.universe_symbols:
            self.universe_symbols = list(self.PORTFOLIO_STOCKS.keys())
            bot_log(f"🧠 Universe fallback: hardcoded list ({len(self.universe_symbols)} symbols)")

    def get_watchlist_symbols(self, max_watchlist: int = 60, iteration: int = 0) -> list:
        """
        Ask the LLM to select a watchlist from a broad NSE universe.
        Always includes held symbols.
        """
        # Cache watchlist for a few loops to reduce repeated API/news calls
        if iteration and self._cached_watchlist and iteration < self._cached_watchlist_until_iter:
            return list(self._cached_watchlist)[:max_watchlist]

        self.refresh_universe_if_needed(force=False)

        held = [s for s, sh in self.holdings.items() if sh and sh > 0]

        # 1) Market context first (current affairs / macro)
        market_context = {
            "macro_snapshot": market_tools.get_macro_snapshot(),
            "sector_performance": market_tools.get_sector_performance(),
            "fii_dii": market_tools.get_fii_dii_activity(),
            "market_news": news_tools.get_market_news(),
        }
        theme = agents.run_theme_agent(market_context) or {}

        favored = set([str(s).strip() for s in theme.get("favored_sectors", []) if str(s).strip()])
        avoid = set([str(s).strip() for s in theme.get("avoid_sectors", []) if str(s).strip()])
        keywords = [str(k).strip().lower() for k in theme.get("headline_keywords", []) if str(k).strip()]

        # 2) Build candidate pool (broad exploration but bounded)
        # - include held
        # - include a random sample from universe to explore new names
        # - include the original curated list as a stability anchor
        universe = list(self.universe_symbols or [])
        if not universe:
            universe = list(self.PORTFOLIO_STOCKS.keys())

        # Sample to avoid huge per-cycle network usage
        pool = set(held)
        pool.update(list(self.PORTFOLIO_STOCKS.keys()))
        if len(universe) > 0:
            sample_n = max(0, THEME_CANDIDATE_POOL - len(pool))
            if sample_n > 0:
                pool.update(random.sample(universe, k=min(sample_n, len(universe))))

        pool_list = list(pool)
        random.shuffle(pool_list)
        pool_list = pool_list[:THEME_CANDIDATE_POOL]

        # 3) Fetch fundamentals for sector/industry tags (bounded)
        fundamentals_symbols = pool_list[:min(len(pool_list), FUNDAMENTALS_SHORTLIST)]
        fundamentals_map = {}

        def fetch_fund(sym: str):
            return sym, market_tools.get_fundamentals(sym, exchange="NSE")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for sym, res in ex.map(fetch_fund, fundamentals_symbols):
                fundamentals_map[sym] = res or {}

        # Simple local scoring to decide who gets news fetched
        def score_symbol(sym: str) -> float:
            info = fundamentals_map.get(sym, {}) or {}
            sec = str(info.get("sector") or "").strip()
            ind = str(info.get("industry") or "").strip()
            sc = 0.0
            if sec and sec in favored:
                sc += 3.0
            if sec and sec in avoid:
                sc -= 2.5
            # Heuristic: defence exposure (NSE/YF often uses "Industrials"/"Aerospace & Defense")
            if ("defen" in (sec + " " + ind).lower()) or ("aerospace" in (sec + " " + ind).lower()):
                sc += 1.0
            # Prefer known liquid anchors slightly
            if sym in self.PORTFOLIO_STOCKS:
                sc += 0.25
            if sym in held:
                sc += 10.0
            return sc

        ranked = sorted(pool_list, key=score_symbol, reverse=True)
        news_symbols = ranked[:min(len(ranked), NEWS_SHORTLIST)]

        # 4) Fetch stock-specific headlines for shortlisted candidates
        news_map = {}

        def fetch_news(sym: str):
            name = self.PORTFOLIO_STOCKS.get(sym, "")
            return sym, news_tools.get_stock_news(sym, company_name=name)

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
            for sym, res in ex.map(fetch_news, news_symbols):
                headlines = (res or {}).get("headlines", []) or []
                # Compress headlines; also compute keyword hits for the LLM
                joined = " | ".join([str(h) for h in headlines[:6] if h])
                hits = []
                if keywords and joined:
                    low = joined.lower()
                    hits = [k for k in keywords if k in low][:8]
                news_map[sym] = {
                    "headlines": headlines[:6],
                    "keyword_hits": hits,
                }

        # 5) Let LLM pick final watchlist with theme + fundamentals + headlines
        candidate_rows = []
        for sym in news_symbols:
            info = fundamentals_map.get(sym, {}) or {}
            candidate_rows.append(
                {
                    "symbol": sym,
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "news": news_map.get(sym, {}),
                }
            )

        snapshot = {
            "held_symbols": held,
            "market_context": market_context,
            "theme": theme,
            "candidates": candidate_rows,
            "constraints": {"max_watchlist": max_watchlist},
            "objective": {
                "swing_profit_target_pct": SWING_PROFIT_TARGET_PCT,
                "notes": "Use news/current-affairs themes (e.g., war) to rotate sectors; you have time; do not over-trade.",
            },
        }

        picked = agents.run_universe_picker_agent(snapshot)

        # Enforce rules locally
        out = []
        seen = set()
        for sym in (held + picked):
            s = str(sym).strip().upper()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= max_watchlist:
                break

        # If LLM failed, fallback to held + curated subset
        if not out:
            out = (held + list(self.PORTFOLIO_STOCKS.keys()))[:max_watchlist]

        # Cache
        if iteration:
            self._cached_watchlist = out[:max_watchlist]
            self._cached_watchlist_until_iter = iteration + max(1, int(WATCHLIST_RECALC_EVERY_LOOPS))

        return out

    # ── Trail & Scale ─────────────────────────────────────────────────────────

    def process_t1_settlements(self):
        """Release T+1 settlement funds into deployable cash if 24 hours have passed."""
        now = datetime.datetime.now()
        settled_this_cycle = 0
        remaining = []
        for funds in self.unsettled_cash:
            dt = datetime.datetime.fromisoformat(funds["timestamp"])
            if (now - dt).total_seconds() >= 24 * 3600:
                self.cash += funds["amount"]
                settled_this_cycle += funds["amount"]
            else:
                remaining.append(funds)
        self.unsettled_cash = remaining
        if settled_this_cycle > 0:
            bot_log(f"💰 [SETTLEMENT] ₹{settled_this_cycle:,.2f} of T+1 funds released into deployable cash.")

    def enforce_trail_and_scale(self, portfolio_data):
        """
        Trail & Scale rule (Pro-Retail strategy):

        When a position is up >= 7.0% from avg cost:
          1. Sell 50% of the position to lock in profit.
          2. Set a break-even stop for the remaining 50% at the original
             entry price. If the price then falls back to entry, Python
             auto-sells the rest (enforced in enforce_hard_stop_losses).

        Fires BEFORE the LLM call each cycle. LLM inherits the trimmed
        position and sees the break-even stop via get_portfolio_state().
        """
        results = []
        for stock_info in portfolio_data:
            symbol = stock_info['symbol']
            shares = self.holdings.get(symbol, 0)
            if shares < 1:
                continue
            avg  = self.avg_cost.get(symbol, 0)
            if avg <= 0:
                continue
            price   = stock_info['price']
            pnl_pct = (price - avg) / avg * 100

            # Already triggered for this position — enforce break-even stop instead
            if symbol in self.break_even_stop:
                be_price = self.break_even_stop[symbol]
                if price <= be_price:
                    # Price fell back to entry — exit remaining position
                    stt_fees = (shares * price) * 0.001
                    total_fees = stt_fees + 15.93 + 20.0
                    proceeds = (shares * price) - total_fees
                    realized_pnl = proceeds - shares * avg
                    self.unsettled_cash.append({"amount": proceeds, "timestamp": datetime.datetime.now().isoformat()})
                    self.holdings[symbol] = 0
                    self.avg_cost[symbol] = 0.0
                    del self.break_even_stop[symbol]
                    self.bought_date.pop(symbol, None)
                    self.total_trades += 1
                    
                    self.trade_journal.log_trade(
                        symbol, "SELL", shares, price, "⏰ BREAK-EVEN STOP HIT",
                        pnl=realized_pnl, pnl_pct=pnl_pct
                    )
                    
                    results.append(
                        f"⏰ BREAK-EVEN STOP HIT: SOLD {shares} × {stock_info['name']} "
                        f"@ ₹{price:.2f} (BE: ₹{be_price:.2f}) | "
                        f"P&L: ₹{realized_pnl:+,.2f}"
                    )
                continue   # skip Trail check for this symbol

            # First time hitting +7.0% — trigger Trail & Scale
            if pnl_pct >= 7.0:
                sell_qty = max(1, shares // 2)
                if sell_qty < 1:
                    continue
                stt_fees = (sell_qty * price) * 0.001
                total_fees = stt_fees + 15.93 + 20.0
                proceeds = (sell_qty * price) - total_fees
                realized_pnl = proceeds - sell_qty * avg
                self.unsettled_cash.append({"amount": proceeds, "timestamp": datetime.datetime.now().isoformat()})
                self.holdings[symbol] -= sell_qty
                self.total_trades += 1
                # Lock remaining position to break-even stop at original entry
                self.break_even_stop[symbol] = avg
                
                self.trade_journal.log_trade(
                    symbol, "SELL", sell_qty, price, "✨ TRAIL & SCALE: +7.0% Profit Booking",
                    pnl=realized_pnl, pnl_pct=pnl_pct
                )
                
                paper_tag = " [PAPER]" if PAPER_TRADE else ""
                results.append(
                    f"✨{paper_tag} TRAIL & SCALE: SOLD {sell_qty} of {shares} × "
                    f"{stock_info['name']} @ ₹{price:.2f} (+{pnl_pct:.1f}%) | "
                    f"Realized: ₹{realized_pnl:+,.2f} | "
                    f"Remaining {self.holdings[symbol]} sh locked to BE @ ₹{avg:.2f}"
                )
        return results

    def enforce_hard_stop_losses(self, portfolio_data):
        """
        Optional hard stop-loss: automatically sell 100% of any position down
        beyond HARD_STOP_LOSS_PCT BEFORE consulting the AI.

        If HARD_STOP_LOSS_PCT is None, this is disabled to support swing holds.
        """
        if HARD_STOP_LOSS_PCT is None:
            return []

        results = []
        for stock_info in portfolio_data:
            symbol = stock_info['symbol']
            shares = self.holdings[symbol]
            if shares < 1:
                continue
            avg_cost = self.avg_cost[symbol]
            if avg_cost <= 0:
                continue
            price = stock_info['price']
            pnl_pct = (price - avg_cost) / avg_cost * 100
            if pnl_pct <= float(HARD_STOP_LOSS_PCT):
                stt_fees = (shares * price) * 0.001
                total_fees = stt_fees + 15.93 + 20.0
                proceeds = (shares * price) - total_fees
                realized_pnl = proceeds - shares * avg_cost
                self.unsettled_cash.append({"amount": proceeds, "timestamp": datetime.datetime.now().isoformat()})
                self.holdings[symbol] = 0
                self.avg_cost[symbol] = 0.0
                self.total_trades += 1
                
                self.trade_journal.log_trade(
                    symbol, "SELL", shares, price, f"🚨 HARD STOP-LOSS: Down <= {HARD_STOP_LOSS_PCT}%",
                    pnl=realized_pnl, pnl_pct=pnl_pct
                )
                
                results.append(
                    f"🚨 HARD STOP-LOSS: SOLD {shares} × {stock_info['name']} "
                    f"@ ₹{price:.2f} | P&L: 🔴 ₹{realized_pnl:+,.2f} ({pnl_pct:+.2f}%)"
                )
        return results

    def execute_trade(self, decision, portfolio_data):
        """
        Execute a single AI decision.
        Position sizing is validated against available cash and existing holdings.
        """
        action   = decision.get('action', 'HOLD')
        symbol   = decision.get('symbol', '')
        quantity = int(decision.get('quantity', 1))

        if action == 'HOLD' or not symbol:
            return None

        stock_info = next((s for s in portfolio_data if s['symbol'] == symbol), None)
        if stock_info is None:
            return None

        # Slippage Tracking points
        observed_ltp = stock_info['price']
        
        # If the LLM sent a limit price, use that as the target cost basis
        limit_price = decision.get('limit_price')
        if limit_price and isinstance(limit_price, (int, float)):
            order_requested_price = float(limit_price)
        else:
            order_requested_price = observed_ltp
            
        execution_actual_price = observed_ltp

        # [Verification Protocol] Inject simulated friction in TEST_MODE
        if TEST_MODE:
            time.sleep(0.5)  # 500ms latency sim
            # Add 0.05 to 0.10 INR slippage for buys, deduct for sells
            frict = round(random.uniform(0.05, 0.10), 2)
            if action == 'BUY':
                execution_actual_price += frict
            elif action == 'SELL':
                execution_actual_price -= frict

        total_portfolio = self.cash + sum(
            s['shares'] * s['price'] for s in portfolio_data
        )

        if action == 'BUY':
            # Limit exposure per stock to a maximum of 30% of total portfolio value
            max_stock_capital = total_portfolio * 0.30
            current_stock_value = self.holdings[symbol] * execution_actual_price
            allowed_new_value = max(0, max_stock_capital - current_stock_value)
            
            # Allow deploying cash up to the allowed position size limit
            deployable = min(max(0, self.cash), allowed_new_value)
            
            # We calculate max_qty using the actual execution price
            max_qty    = int(deployable / execution_actual_price) if execution_actual_price > 0 else 0
            quantity   = min(quantity, max_qty)

            if quantity < 1:
                return "⚠️  BUY skipped — insufficient cash / buffer constraint"

            total_fees = (quantity * execution_actual_price * 0.001) + 20.0
            cost = (quantity * execution_actual_price) + total_fees
            self.cash -= cost
            prev_shares = self.holdings[symbol]
            self.holdings[symbol] += quantity
            # Update weighted average cost
            if prev_shares == 0:
                self.avg_cost[symbol] = execution_actual_price
            else:
                self.avg_cost[symbol] = (
                    (self.avg_cost[symbol] * prev_shares + cost) /
                    self.holdings[symbol]
                )
            # Record purchase date for T+1 settlement guard
            self.bought_date[symbol] = datetime.date.today().isoformat()
            self.total_trades += 1
            
            # Slippage logging
            self.trade_journal.log_trade(
                symbol, "BUY", quantity, execution_actual_price, decision.get('reason', 'AI BUY Strategy'),
                observed_ltp=observed_ltp,
                order_requested_price=order_requested_price,
                execution_actual_price=execution_actual_price
            )
                
            paper_tag = " [PAPER]" if PAPER_TRADE else ""
            limit_tag = f" (Limit: ₹{order_requested_price:.2f})" if limit_price else ""
            return (f"✅{paper_tag} BOUGHT {quantity} × {stock_info['name']} @ ₹{execution_actual_price:.2f}{limit_tag} "
                    f"(Est. ₹{cost:,.2f}) | Avg cost: ₹{self.avg_cost[symbol]:.2f}")

        elif action == 'SELL':
            quantity = min(quantity, self.holdings[symbol])
            if quantity < 1:
                return "⚠️  SELL skipped — no shares held"

            # Swing hold guard: do not allow selling at a loss unless explicitly allowed.
            avg_cost = self.avg_cost.get(symbol, 0.0)
            if avg_cost > 0:
                pnl_pct = (execution_actual_price - avg_cost) / avg_cost * 100
                allow_loss = bool(decision.get("allow_loss", False))
                if pnl_pct < 0 and not allow_loss:
                    return (
                        f"🧘 SELL blocked — {symbol} is at a loss ({pnl_pct:+.2f}%). "
                        f"Swing mode allows time to recover; set allow_loss=true only for thesis-broken exits."
                    )

            # T+1 settlement guard (live mode only)
            # In India, shares bought today are only deliverable from the next
            # trading day. Trying to sell same-day in CNC mode will be rejected
            # by Groww. We catch it here to keep local state in sync.
            if not PAPER_TRADE:
                bought_on = self.bought_date.get(symbol)
                if bought_on and bought_on == datetime.date.today().isoformat():
                    return (
                        f"⏳ SELL skipped — {symbol} was bought today (T+0). "
                        f"T+1 settlement: shares available to sell from tomorrow."
                    )

            stt_fees     = (quantity * execution_actual_price) * 0.001
            total_fees   = stt_fees + 15.93 + 20.0
            proceeds     = (quantity * execution_actual_price) - total_fees
            buy_cost     = quantity * self.avg_cost[symbol]
            realized_pnl = proceeds - buy_cost

            self.unsettled_cash.append({"amount": proceeds, "timestamp": datetime.datetime.now().isoformat()})
            self.holdings[symbol] -= quantity
            if self.holdings[symbol] == 0:
                self.avg_cost[symbol]  = 0.0
                self.bought_date.pop(symbol, None)   # clear settlement tracker
            self.total_trades += 1
            
            self.trade_journal.log_trade(
                symbol, "SELL", quantity, execution_actual_price, decision.get('reason', 'AI SELL Strategy'),
                pnl=realized_pnl, pnl_pct=(realized_pnl / buy_cost * 100) if buy_cost > 0 else 0,
                observed_ltp=observed_ltp,
                order_requested_price=order_requested_price,
                execution_actual_price=execution_actual_price
            )
            
            pnl_sign  = "🟢" if realized_pnl >= 0 else "🔴"
            paper_tag = " [PAPER]" if PAPER_TRADE else ""
            return (f"💰{paper_tag} SOLD {quantity} × {stock_info['name']} @ ₹{execution_actual_price:.2f} "
                    f"(₹{proceeds:,.2f}) | Realized P&L: {pnl_sign} ₹{realized_pnl:+,.2f}")

        return None

    # ── Portfolio Metrics ─────────────────────────────────────────────────────

    def get_portfolio_value(self):
        total = self.cash
        for sym, shares in self.holdings.items():
            h = self.stock_data.get(sym, {}).get("price_history", [])
            if h and shares > 0:
                total += shares * h[-1]
                
        # Include proceeds from recent sales that haven't settled yet
        for unsettled in getattr(self, 'unsettled_cash', []):
            total += unsettled.get("amount", 0.0)
            
        return total

    def get_pnl_summary(self):
        current = self.get_portfolio_value()
        gain    = current - self.initial_cash
        return {
            "initial":    self.initial_cash,
            "current":    current,
            "gain_loss":  gain,
            "pnl_pct":    (gain / self.initial_cash * 100),
        }

    def get_holdings_summary(self):
        summary = []
        for sym, shares in self.holdings.items():
            if shares > 0:
                h = self.stock_data[sym]["price_history"]
                if h:
                    val = shares * h[-1]
                    pct = ((h[-1] - self.avg_cost[sym]) / self.avg_cost[sym] * 100
                           if self.avg_cost[sym] > 0 else 0)
                    summary.append(f"{sym}:{shares}sh(₹{val:,.0f},{pct:+.1f}%)")
        return summary or ["No holdings"]

    # ── State Persistence ─────────────────────────────────────────────────────

    def get_mongo_collection(self):
        mongo_uri = os.environ.get("MONGO_URI")
        if not mongo_uri:
            return None
        try:
            client = MongoClient(mongo_uri)
            db = client.get_database("stockbot")
            return db["bot_state"]
        except Exception as e:
            print(f"⚠️  Could not connect to MongoDB for bot state: {e}")
            return None

    def save_state(self):
        state = {
            "cash":         self.cash,
            "initial_cash": self.initial_cash,
            "total_trades": self.total_trades,
            "holdings":     self.holdings,
            "avg_cost":     self.avg_cost,
            "stock_data":   self.stock_data,
            "bought_date":    self.bought_date,   # T+1 settlement tracker
            "break_even_stop": self.break_even_stop,
            "unsettled_cash": getattr(self, "unsettled_cash", []),
            "paper_trade":    PAPER_TRADE,
            "last_updated": datetime.datetime.now().isoformat(),
        }
        
        coll = self.get_mongo_collection()
        if coll is not None:
            try:
                coll.replace_one({"_id": "current_state"}, state, upsert=True)
            except Exception as e:
                print(f"⚠️  Could not save state to MongoDB: {e}")
        else:
            # Fallback
            try:
                with open(self.STATE_FILE, 'w') as f:
                    json.dump(state, f, indent=2)
            except Exception as e:
                print(f"⚠️  Could not save fallback state: {e}")

    def load_state(self):
        coll = self.get_mongo_collection()
        state = None
        
        if coll is not None:
            try:
                state = coll.find_one({"_id": "current_state"})
            except Exception as e:
                print(f"⚠️  Could not load state from MongoDB: {e}")
                
        # Fallback to local json if mongo is empty or failed
        if not state:
            if not os.path.exists(self.STATE_FILE):
                return False
            try:
                with open(self.STATE_FILE) as f:
                    state = json.load(f)
            except Exception as e:
                print(f"⚠️  Could not load fallback state: {e}")
                return False

        try:
            self.cash         = state["cash"]
            self.initial_cash = state.get("initial_cash", TEST_INITIAL_CASH)
            self.total_trades = state.get("total_trades", 0)
            self.holdings     = state["holdings"]
            self.avg_cost     = state.get("avg_cost", {s: 0.0 for s in self.PORTFOLIO_STOCKS})
            self.stock_data   = state["stock_data"]
            self.bought_date  = state.get("bought_date", {})  # T+1 tracker
            self.break_even_stop = state.get("break_even_stop", {})
            self.unsettled_cash = state.get("unsettled_cash", [])
            saved_mode = "📜 [PAPER]" if state.get("paper_trade") else "🔴 [LIVE]"
            print(f"   Last session: {state.get('last_updated', 'unknown')}  {saved_mode}")
            return True
        except Exception as e:
            print(f"⚠️  Could not parse state: {e}")
            return False

    # ── Main Loop ─────────────────────────────────────────────────────────────

    def run_simulation(self):
        # Fully detach from the parent Streamlit process:
        # Ignore SIGHUP so a browser refresh / Streamlit rerun cannot kill the bot.
        # The bot will only stop when the dashboard sends SIGINT (Stop button).
        try:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        except (OSError, AttributeError, ValueError):
            pass  # Safe to ignore if not in main thread or not available

        # Write PID so Streamlit can stop the bot
        try:
            with open(PID_FILE, "w") as pf:
                pf.write(str(os.getpid()))
        except Exception:
            pass

        mode_label = "🧪 TEST MODE (simulated prices)" if TEST_MODE else "🔴 LIVE MODE (real NSE prices)"
        # 300s loop (5m): LLM uses ~8 tool calls per cycle ≈ 9 API requests.
        sleep_secs = TEST_LOOP_SLEEP if TEST_MODE else 60

        print("=" * 80)
        print("🤖  AUTONOMOUS LLM TRADING BOT — MULTI-TOOL RESEARCH AGENT  🤖")
        print("=" * 80)
        print(f"Mode         : {mode_label}")
        print(f"Starting Cash: ₹{self.cash:,.2f}")
        print(f"NSE Stocks   : {len(self.PORTFOLIO_STOCKS)}")
        print(f"Loop interval: {sleep_secs}s")
        print("=" * 80)
        print("Strategy     : Autonomous LLM | Tools: News+Macro+FII/DII+Fundamentals")
        print("Target       : 5.0% monthly return | Hard stop-loss: -4%")
        print(f"Paper Trade  : {'YES (no real orders)' if groww_broker.PAPER_TRADE else 'NO — LIVE ORDERS ACTIVE'}")
        print("=" * 80)
        print("Press Ctrl+C to stop safely")
        print("=" * 80)

        iteration = 0
        try:
            while True:
                iteration += 1
                self._current_iteration = iteration

                nse_open = is_market_open()
                if not nse_open:
                    pnl = self.get_pnl_summary()
                    msg = (
                        f"🌙 [#{iteration}] Market closed | "
                        f"Portfolio: ₹{pnl['current']:,.2f} | "
                        f"P&L: ₹{pnl['gain_loss']:+,.2f} ({pnl['pnl_pct']:+.2f}%)"
                    )
                    bot_log(msg)
                    time.sleep(sleep_secs)
                    continue

                bot_log(f"\n{'─'*60}")
                bot_log(f"📡 [#{iteration}] Fetching market data...")
                self.process_t1_settlements()

                portfolio_data = self.fetch_all_stock_data()
                if not portfolio_data:
                    print("⚠️  No data available, retrying...")
                    time.sleep(sleep_secs)
                    continue

                # ── 1. Hard stop-loss pass ────────────────────────────────
                stop_results = self.enforce_hard_stop_losses(portfolio_data)
                for sr in stop_results:
                    bot_log(f"   {sr}")

                # ── 2. Trail & Scale pass (DISABLED -> Let AI think instead) ────
                # trail_results = self.enforce_trail_and_scale(portfolio_data)
                # for tr in trail_results:
                #     bot_log(f"   {tr}")

                # ── 3. Market Kill Switch — Sentry Mode check ─────────────
                health = market_tools.check_market_health()
                kill_switch = health.get("KILL_SWITCH", False)
                if kill_switch:
                    bot_log(
                        f"🛡️  SENTRY MODE ACTIVE — {health['reason']} "
                        f"| VIX={health.get('vix')} | Nifty chg={health.get('nifty_change_pct')}%"
                    )
                    bot_log("🛡️  All BUY orders HALTED. Monitoring stop-losses only.")

                # ── 4. Multi-Agent Orchestration ─────────────────────────
                bot_log(f"🤖 Booting Multi-Agent Intelligence Pipeline...")
                decisions = get_multi_agent_portfolio_decision(portfolio_data, self.cash, self.trade_journal, self)

                trade_results = []
                for decision in decisions:
                    # Sentry Mode: suppress BUY orders when kill switch active
                    if kill_switch and decision.get("action") == "BUY":
                        bot_log(
                            f"🛡️  SENTRY: BUY {decision.get('symbol','')} BLOCKED — kill switch active"
                        )
                        continue

                    # Log bear case if present (adversarial protocol)
                    if decision.get("bear_case"):
                        bot_log(f"   👿 Bear case: {decision['bear_case']}")

                    result = self.execute_trade(decision, portfolio_data)
                    bot_log(
                        f"🤖 AI → {decision.get('action')} "
                        f"{decision.get('symbol','') or ''} "
                        f"×{decision.get('quantity', 0)}"
                    )
                    bot_log(f"   Reason : {decision.get('reason','')}")
                    if result:
                        bot_log(f"   Trade  : {result}")
                        trade_results.append(result)

                self.save_state()

                pnl = self.get_pnl_summary()
                bot_log(
                    f"💼 Portfolio : ₹{pnl['current']:,.2f} | "
                    f"Cash: ₹{self.cash:,.2f} | "
                    f"Unsettled: ₹{sum(f['amount'] for f in getattr(self, 'unsettled_cash', [])):,.2f} | "
                    f"P&L: ₹{pnl['gain_loss']:+,.2f} ({pnl['pnl_pct']:+.2f}%)"
                )
                bot_log(f"📦 Holdings  : {' | '.join(self.get_holdings_summary())}")
                bot_log(f"📈 Trades    : {self.total_trades}")
                bot_log('─' * 60)

                time.sleep(sleep_secs)

        except KeyboardInterrupt:
            bot_log("🛑 Bot stopped by user")
            self.save_state()
            pnl = self.get_pnl_summary()
            bot_log("💾 FINAL SUMMARY")
            bot_log(f"   Initial Capital : ₹{pnl['initial']:,.2f}")
            bot_log(f"   Final Value     : ₹{pnl['current']:,.2f}")
            bot_log(f"   Cash Available  : ₹{self.cash:,.2f}")
            bot_log(f"   Profit / Loss   : ₹{pnl['gain_loss']:+,.2f} ({pnl['pnl_pct']:+.2f}%)")
            bot_log(f"   Total Trades    : {self.total_trades}")
            bot_log(f"📊 Holdings: {' | '.join(self.get_holdings_summary())}")
        finally:
            # Clean up PID file
            try:
                os.remove(PID_FILE)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
class HealthCheckHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass # Suppress HTTP logs to keep terminal clean

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is awake and trading! Monitor your stats in MongoDB.")

def start_health_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        print(f"🌐 Lightweight Health Server listening on port {port} for Render...", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"⚠️ Failed to start health server: {e}", flush=True)

if __name__ == "__main__":
    def run_bot():
        bot = MultiStockTradingSimulator(initial_cash=TEST_INITIAL_CASH)
        bot.run_simulation()

    # Start the heavy trading bot in a background thread
    print("🤖 Starting Trading Bot in background thread...", flush=True)
    threading.Thread(target=run_bot, daemon=True).start()

    # Keep the main thread instantly available to bind the web server port for Render
    start_health_server()