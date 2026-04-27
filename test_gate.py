import agents
import main
import json

sim = main.MultiStockTradingSimulator()
main.TEST_MODE = True
portfolio_data = sim.fetch_all_stock_data()[:10] # just 10 stocks

news_data = {s['symbol']: s['news'] for s in portfolio_data}
price_data = {s['symbol']: {"price": s['price'], "tech": s['tech']} for s in portfolio_data}

r = agents.run_researcher_agent(news_data)
q = agents.run_quant_agent(price_data)

print("Researcher:", r)
print("Quant:", q)

for s in portfolio_data:
    sym = s['symbol']
    sent = r.get(sym, {}).get("sentiment", "Neutral")
    signal = q.get(sym, {}).get("signal", "Neutral")
    print(f"{sym}: sent={sent}, signal={signal}")
