import sys
import re

# ==========================================
# 1. Update agents.py
# ==========================================
with open("agents.py", "r") as f:
    agents_text = f.read()

# Add MODEL_ECONOMIST
agents_text = agents_text.replace("MODEL_ORCHESTRATOR = 'gemini-2.5-flash'",
                                  "MODEL_ECONOMIST = 'gemini-2.5-flash'\nMODEL_ORCHESTRATOR = 'gemini-2.5-flash'")

# Insert run_economist_agent before run_orchestrator
economist_code = """
def run_economist_agent(macro_data: dict, sector_data: dict, market_news: dict) -> dict:
    \"\"\"
    Reads global macro indicators and top Indian market news to output a sector-by-sector
    impact matrix. Predicts which sectors will face headwinds or tailwinds.
    Input: dicts for macro snapshot, sector performance, and market headlines.
    Output: dict of Sector -> {"sentiment": "BULLISH"|"BEARISH"|"NEUTRAL", "reason": str}
    \"\"\"
    sys_prompt = "You are the Chief Macro-Economist for an institutional fund. Read the global/domestic market cues and determine the systemic outlook for these Indian equity sectors: IT, Bank, Auto, Pharma, FMCG, Energy, Metal, Realty, Media. Output ONLY a valid JSON dictionary mapping sector names to an object with fields 'sentiment' (Must be 'BULLISH', 'BEARISH', or 'NEUTRAL') and 'reason'. Do not use markdown fences."
    
    state = {
        "macro_snapshot": macro_data,
        "sector_performance_today": sector_data,
        "latest_market_news": market_news
    }
    prompt = f"Generate the sector impact matrix based on this current global state:\\n{json.dumps(state, indent=2)}"
    
    res = _call_model(MODEL_ECONOMIST, prompt, sys_prompt)
    try:
        return json.loads(res)
    except Exception as e:
        print(f"Failed to parse Economist JSON: {e}")
        return {}

"""

# Replace the signature of run_orchestrator
old_orch_sig = "def run_orchestrator(portfolio_state: dict, researcher_map: dict, quant_map: dict, adv_map: dict, trade_journal_context: list, cash: float) -> list:"
new_orch_sig = "def run_orchestrator(portfolio_state: dict, researcher_map: dict, quant_map: dict, adv_map: dict, economist_map: dict, trade_journal_context: list, cash: float) -> list:"

agents_text = agents_text.replace(old_orch_sig, economist_code + new_orch_sig)

# Update sys_prompt in Orchestrator to include Economist
old_orch_sys_1 = "You are receiving data from 3 subordinate agents:\n1. Researcher (News Sentiment)\n2. Quant (Technical/Fundamental Signal)\n3. Adversarial (Bear Case for the best candidates)"
new_orch_sys_1 = "You are receiving data from 4 subordinate agents:\n1. Macro-Economist (Sector-level global outlook)\n2. Researcher (Stock News Sentiment)\n3. Quant (Technical/Fundamental Signal)\n4. Adversarial (Bear Case for the best candidates)\n\nVETO RULE: Do NOT BUY a stock if the Macro-Economist has flagged its Sector as BEARISH, regardless of how good the Quant signal is."
agents_text = agents_text.replace(old_orch_sys_1, new_orch_sys_1)

# Update the state passed to orchestrator prompt
old_state_dict = """        "agent_signals": {
            "researcher": researcher_map,
            "quant": quant_map,
            "adversarial_bear_cases": adv_map
        },"""
new_state_dict = """        "agent_signals": {
            "economist_sector_outlook": economist_map,
            "researcher": researcher_map,
            "quant": quant_map,
            "adversarial_bear_cases": adv_map
        },"""
agents_text = agents_text.replace(old_state_dict, new_state_dict)

with open("agents.py", "w") as f:
    f.write(agents_text)


# ==========================================
# 2. Update main.py
# ==========================================
with open("main.py", "r") as f:
    main_text = f.read()

# Update get_multi_agent_portfolio_decision signature and implementation
target_multi_start = "    # 1. Sequential Execution for Researcher & Quant"
replacement_multi_start = """    # 1. Global Macro-Economist Execution
    bot_log("🌐 [Economist Agent] Checking global macro data and market news...")
    macro_data = market_tools.get_macro_snapshot()
    sector_data = market_tools.get_sector_performance()
    market_news = news_tools.get_market_news()
    
    economist_map = agents.run_economist_agent(macro_data, sector_data, market_news)
    bullish_sectors = [s for s, data in economist_map.items() if data.get('sentiment') == 'BULLISH']
    bearish_sectors = [s for s, data in economist_map.items() if data.get('sentiment') == 'BEARISH']
    bot_log(f"🌐 [Economist] Bullish Sectors: {bullish_sectors} | Bearish Sectors: {bearish_sectors}")

    # 2. Sequential Execution for Researcher & Quant"""
main_text = main_text.replace(target_multi_start, replacement_multi_start)

# Update Orchestrator call inside get_multi_agent_portfolio_decision
target_orch_call = """    decisions = agents.run_orchestrator(
        portfolio_state=full_state,
        researcher_map=researcher_map,
        quant_map=quant_map,
        adv_map=adv_map,
        trade_journal_context=tj_context,
        cash=cash
    )"""
replacement_orch_call = """    decisions = agents.run_orchestrator(
        portfolio_state=full_state,
        researcher_map=researcher_map,
        quant_map=quant_map,
        adv_map=adv_map,
        economist_map=economist_map,
        trade_journal_context=tj_context,
        cash=cash
    )"""
main_text = main_text.replace(target_orch_call, replacement_orch_call)

with open("main.py", "w") as f:
    f.write(main_text)

print("Patch applied successfully.")
