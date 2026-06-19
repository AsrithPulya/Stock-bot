import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import agents
import json

def test_orchestrator():
    portfolio_state = {"portfolio": [{"symbol": "TCS", "price": 3800, "tech": {}, "news": "Good news"}], "market_health": "Bullish"}
    researcher_map = {"TCS": {"sentiment": "Bullish", "reason": "Positive news"}}
    quant_map = {"TCS": {"signal": "Strong", "reason": "Technical strength"}}
    adv_map = {"TCS": "Competition is rising"}
    economist_map = {"IT": {"sentiment": "BULLISH", "reason": "Global demand"}}
    trade_journal_context = []
    cash = 25000

    print("Calling run_orchestrator...")
    decisions = agents.run_orchestrator(
        portfolio_state, researcher_map, quant_map, adv_map, economist_map, trade_journal_context, cash
    )
    print("Decisions:", decisions)

if __name__ == "__main__":
    test_orchestrator()
