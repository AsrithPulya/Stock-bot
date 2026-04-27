import agents
import json
news_data = {"RELIANCE": "Reliance posts strong quarterly results", "TCS": "TCS wins major contract"}
price_data = {"RELIANCE": {"price": 2900, "tech": {"trend": "STRONG UPTREND", "rsi": 40}}, "TCS": {"price": 3800, "tech": {"trend": "STRONG UPTREND", "rsi": 30}}}
print("Research:", agents.run_researcher_agent(news_data))
print("Quant:", agents.run_quant_agent(price_data))
