# How the Stock-bot Project Was Created

This document outlines the architecture, design workflow, and the AI prompts that were used to create this **autonomous NSE/BSE Swing Trading Bot** project.

---

## 🏗️ Project Architecture Overview

The project is structured as an autonomous agentic system that uses Gemini to make trading decisions, paired with a custom dashboard to monitor its state:

1. **Dashboard (`app.py`)**: A Streamlit application styled with a dark glassmorphism aesthetic. It manages the bot's background process (`bot.pid`, `bot_log.txt`), monitors current portfolio status (`trading_bot_state.json`), and visualizes metrics like current cash, equity value, trade logs, and performance.
2. **AI Decision Engine (`main.py`)**: The central engine that runs in a loop. It orchestrates the trading loop, retrieves market data using tools, calls the Gemini API to get buy/sell actions, and updates the bot state.
3. **Agent & API Wrapper (`agents.py` & `list_models.py`)**: Standardizes the LLM API setup utilizing the modern `google-genai` SDK.
4. **Research Tools (`market_tools.py` & `news_tools.py`)**: Modules that download indices data, calculate indicators (RSI, Moving Averages), fetch FII/DII activities, check holidays, and fetch news feeds.
5. **Execution Wrapper (`groww_broker.py` & `trade_journal.py`)**: Simulates order placement and journals the trades to JSON logs, calculating slippage and realized P&L.
6. **Telemetry & Safeguards (`performance_monitor.py` & `debug_gate.py`)**: Tracks latencies, token usage, cost optimization, and acts as a firewall guarding against faulty AI instructions (e.g. buying with insufficient cash).

---

## 🤖 The AI Prompts Used for Generation

This project was built incrementally using highly structured, modular instructions. Below are the prompts that represent the core phases of its creation.

### Phase 1: The Core Broker & Market Tools
**Prompt to write `groww_broker.py` & `market_tools.py`**:
```text
Write a Python file `market_tools.py` and `groww_broker.py` to support an autonomous stock trading bot trading Indian stocks (NSE/BSE). 

`market_tools.py` should use yfinance to:
1. Load Nifty 500 symbols or indices.
2. Calculate simple technical indicators: RSI (14-day), 50-day and 200-day Simple Moving Averages, and ATR (Average True Range).
3. Fetch daily FII/DII flow statistics and macroeconomic indicators.
4. Filter out active/candidate lists based on volume and basic momentum.

`groww_broker.py` should implement a mock trading interface that:
1. Simulates account balance, holding details, and order execution.
2. Simulates typical market slippage (e.g., executing slightly off the LTP depending on order size).
3. Provides helper methods: `get_holdings()`, `get_cash_balance()`, `place_buy_order()`, and `place_sell_order()`.
```

### Phase 2: The Streamlit Dashboard
**Prompt to write `app.py`**:
```text
Build a premium, responsive Streamlit dashboard (`app.py`) for a Python-based trading bot.
Design Guidelines:
1. Use custom CSS styling to create a dark glassmorphism layout (gradient dark background #0a0e1a to #0f1629, translucent white borders, custom fonts like Inter).
2. Layout standard metric boxes: Net Asset Value, Cash Balance, Unrealised P&L, Realised P&L, Bot Status (Running/Stopped).
3. Add dashboard action buttons to start and stop the background bot process safely by writing/reading a `bot.pid` file and spawning/killing the `main.py` execution subprocess.
4. Read logs from `bot_log.txt` in real-time and display them in a scrollable monospace terminal box.
5. Read holdings and show them in a clean Pandas DataFrame, styled for readability.
```

### Phase 3: The AI Decision Engine (The Master Prompt)
**Prompt to write `main.py`**:
```text
Create the main bot execution script `main.py` which runs a periodic loop (e.g., every 5-15 minutes during market hours). 
The bot must implement an autonomous swing trading strategy using the Gemini API via the `google-genai` SDK.

Core Requirements:
1. Load `GEMINI_API_KEY` from the environment via dotenv.
2. Run a main loop that checks if the NSE market is currently open (and not a weekend/holiday).
3. During active hours:
   a. Get current portfolio holdings and cash balance.
   b. Call research tools to fetch macro indicators, institutional flows, sector trends, and news.
   c. Build a candidate watchlist from NIFTY 500 (capping at top candidates for cost and rate limit efficiency).
   d. Pass this context to Gemini using a strict system instruction.
4. The system instruction must mandate:
   - Primary Objective: Generate a minimum 5% monthly return.
   - Capital Rule: Always keep a 20% cash buffer. No single stock should exceed 20% of the portfolio.
   - Profit Booking: Evaluate profit targets at +6.5% to +7%. Let winners run if technicals are strong.
   - Adversarial Protocol: For every BUY decision, Gemini must explicitly outline the Bear Case (Why this trade could fail) and answer why the Bull case outweighs it.
5. The model output must be JSON format containing:
   - "analysis": Detailed reasoning.
   - "bear_case_analysis": Devil's advocate check.
   - "trades": List of actions to take e.g. [{"symbol": "TATASTEEL.NS", "action": "BUY", "quantity": 100, "reason": "..."}].
6. Write the trades to a trade journal file (`trade_history.json`) and run error handling guards to avoid executing invalid trades.
```

---

## 📈 Key Lessons & Engineering Choices in this Codebase
* **Separation of Concerns**: Keeping raw calculations and API logic in tool files (`market_tools.py`, `news_tools.py`) prevents prompt pollution and keeps the LLM's context size optimized.
* **Safe Subprocesses**: The bot runs in a detached subprocess managed via a `.pid` file. This lets the user close the Streamlit tab while the trading loop keeps executing reliably on a local machine.
* **Cost & Safety Guardrails**: Files like `debug_gate.py` act as runtime check guards before hitting broker APIs or Gemini endpoints to prevent runaway token spend or faulty order executions.
