import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import agents
import main

sim = main.MultiStockTradingSimulator()
main.TEST_MODE = True
portfolio_data = sim.fetch_all_stock_data() # all 65 stocks

news_data = {s['symbol']: s['news'] for s in portfolio_data}
price_data = {s['symbol']: {"price": s['price'], "tech": s['tech']} for s in portfolio_data}

r = agents.run_researcher_agent(news_data)
q = agents.run_quant_agent(price_data)

candidates = []
for s in portfolio_data:
    sym = s['symbol']
    sent = str(r.get(sym, {}).get("sentiment", "Neutral")).strip().lower()
    signal = str(q.get(sym, {}).get("signal", "Neutral")).strip().lower()
    if sent == 'bullish' and signal == 'strong':
        candidates.append(sym)
    elif sent == 'bullish' or signal == 'strong':
        print(f"Partial: {sym} (Sent: {sent}, Signal: {signal})")
print(f"Gate Found {len(candidates)} candidates:", candidates)
