import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import agents
import main
import json

sim = main.MultiStockTradingSimulator()
main.TEST_MODE = True
portfolio_data = sim.fetch_all_stock_data() 

news_data = {s['symbol']: s['news'] for s in portfolio_data}
price_data = {s['symbol']: {"price": s['price'], "tech": s['tech']} for s in portfolio_data}

r = agents.run_researcher_agent(news_data)
q = agents.run_quant_agent(price_data)

candidates = []
for s in portfolio_data:
    sym = s['symbol']
    sent = str(r.get(sym, {}).get("sentiment", "Neutral")).strip().upper()
    signal = str(q.get(sym, {}).get("signal", "Neutral")).strip().upper()
    print(f"{sym}: sent='{sent}', signal='{signal}'")
    if sent == "BULLISH" and signal == "STRONG":
        candidates.append(sym)

print(f"Gate Found {len(candidates)} Strong+Bullish candidates: {candidates}")
