import json
import time
import os
from typing import Any, Literal, Optional
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

import market_tools
import news_tools

# Configure API Key
try:
    from main import GEMINI_API_KEY
except ImportError:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# New SDK client (Gemini Developer API). Uses GEMINI_API_KEY env var.
_GENAI_CLIENT = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else genai.Client()

# Models
MODEL_RESEARCHER = 'gemini-2.5-flash-lite'
MODEL_QUANT = 'gemini-2.5-flash-lite'
MODEL_ADVERSARIAL = 'gemini-2.5-flash-lite'
MODEL_ECONOMIST = 'gemini-2.5-flash'
MODEL_ORCHESTRATOR = 'gemini-2.5-flash'
MODEL_UNIVERSE_PICKER = 'gemini-2.5-flash-lite'
MODEL_THEME = 'gemini-2.5-flash-lite'


JsonShape = Literal["object", "array", "any"]


def _choose_model(primary: str, prompt: str) -> str:
    """
    Simple router to reduce timeouts/cost:
    - If the prompt is large, prefer the stronger model.
    - Otherwise prefer the lite model when available.
    """
    prompt_len = len(prompt or "")
    if prompt_len > 18_000:
        # Big prompts benefit from a stronger model's robustness.
        if primary.endswith("-lite"):
            return primary.replace("-lite", "")
        return primary
    if primary.endswith("-lite"):
        return primary
    # Default: stay on primary unless it's clearly small.
    return primary


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```json"):
        t = t[7:]
    elif t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


def _safe_json_loads(text: str, expected: JsonShape) -> Optional[Any]:
    """
    Best-effort JSON parse with minor cleanup.
    """
    cleaned = _strip_code_fences(text)
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
    except Exception:
        # Some models accidentally prefix text; try to salvage by slicing to first JSON token.
        start = min([i for i in [cleaned.find("["), cleaned.find("{")] if i != -1], default=-1)
        if start == -1:
            return None
        try:
            parsed = json.loads(cleaned[start:])
        except Exception:
            return None

    if expected == "object" and not isinstance(parsed, dict):
        return None
    if expected == "array" and not isinstance(parsed, list):
        return None
    return parsed


def _call_model(
    model_name: str,
    prompt: str,
    system_instruction: str = None,
    *,
    expected_json: JsonShape = "any",
    timeout_s: int = 60,
    fallback_model: Optional[str] = None,
    tools: Optional[list] = None,
) -> str:
    # Add backoff for rate limits
    max_retries = 3
    backoff = 10
    
    kwargs = {}
    selected = _choose_model(model_name, prompt)
    
    # Gemma doesn't support system_instruction directly in GenerativeModel init 
    # the same way, so we prepend it for gemma models.
    if system_instruction and "gemma" in selected:
        full_prompt = f"SYSTEM: {system_instruction}\n\nUSER: {prompt}"
    else:
        full_prompt = prompt

    for attempt in range(1, max_retries + 1):
        try:
            config_kwargs = {}
            if system_instruction and "gemini" in selected:
                config_kwargs["system_instruction"] = system_instruction
            if tools:
                config_kwargs["tools"] = tools
            
            config = types.GenerateContentConfig(**config_kwargs)

            if tools:
                chat = _GENAI_CLIENT.chats.create(
                    model=selected,
                    config=config,
                )
                response = chat.send_message(full_prompt)
            else:
                response = _GENAI_CLIENT.models.generate_content(
                    model=selected,
                    contents=full_prompt,
                    config=config,
                )
            final_text = _strip_code_fences(getattr(response, "text", "")).strip()
            if not final_text:
                print(f"⚠️ Agent {selected} returned an empty response.")
                return "[]" if expected_json == "array" else "{}"

            # Validate JSON early so we can retry/fallback.
            if expected_json in ("object", "array"):
                if _safe_json_loads(final_text, expected_json) is None:
                    raise ValueError("Model returned invalid JSON for expected shape")

            return final_text
            
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "exhausted" in err_str or "quota" in err_str:
                if attempt < max_retries:
                    # Sometimes Gemini asks to wait up to 60s
                    wait_time = 60 if backoff < 60 else backoff
                    print(f"⚠️ Agent {selected} Rate Limited. Waiting {wait_time}s before retry (Attempt {attempt}/{max_retries})...")
                    time.sleep(wait_time)
                    backoff = wait_time * 2
                    continue
            elif "timeout" in err_str or "deadline" in err_str or "504" in err_str:
                print(f"⚠️ Agent {selected} Timed Out on attempt {attempt}.")
                if attempt < max_retries:
                    continue

                # Fallback once on timeout if provided (common for orchestrator)
                if fallback_model and fallback_model != selected:
                    try:
                        fb_config_kwargs = {}
                        if system_instruction and "gemini" in fallback_model:
                            fb_config_kwargs["system_instruction"] = system_instruction
                        if tools:
                            fb_config_kwargs["tools"] = tools
                        fb_config = types.GenerateContentConfig(**fb_config_kwargs)

                        if tools:
                            fb_chat = _GENAI_CLIENT.chats.create(
                                model=fallback_model,
                                config=fb_config,
                            )
                            fb_response = fb_chat.send_message(full_prompt)
                        else:
                            fb_response = _GENAI_CLIENT.models.generate_content(
                                model=fallback_model,
                                contents=full_prompt,
                                config=fb_config,
                            )
                        fb_text = _strip_code_fences(getattr(fb_response, "text", "")).strip()
                        if expected_json in ("object", "array") and _safe_json_loads(fb_text, expected_json) is None:
                            return "[]" if expected_json == "array" else "{}"
                        return fb_text or ("[]" if expected_json == "array" else "{}")
                    except Exception:
                        return "[]" if expected_json == "array" else "{}"

                return "[]" if expected_json == "array" else "{}"
                    
            print(f"⚠️ Agent {selected} Error: {e}")
            return "[]" if expected_json == "array" else "{}"
    return "[]" if expected_json == "array" else "{}"


def run_researcher_agent(news_data: dict) -> dict:
    """
    Scrapes news and outputs a JSON sentiment map.
    Input: dict of symbol -> list of headlines
    Output: dict of symbol -> {sentiment: "Bullish"|"Bearish"|"Neutral", reason: str}
    """
    sys_prompt = "You are a quantitative news researcher for Indian stocks. Use your tools to fetch stock news and determine sentiment. Output ONLY a valid JSON dictionary mapping ticker strings to an object with fields 'sentiment' (Must be 'Bullish', 'Bearish', or 'Neutral') and 'reason'. Do not use markdown fences."
    
    prompt = f"Analyze the news for the following stocks and determine sentiment:\n{json.dumps(news_data, separators=(',', ':'))}"
    
    res = _call_model(MODEL_RESEARCHER, prompt, sys_prompt, expected_json="object", timeout_s=45, fallback_model="gemini-2.5-flash", tools=[news_tools.get_stock_news])
    try:
        return json.loads(res)
    except Exception as e:
        print(f"Failed to parse Researcher JSON: {e}")
        return {}


def run_quant_agent(price_data: dict) -> dict:
    """
    Analyzes price history and fundamentals.
    Input: dict of symbol -> {price_history: [], tech: {}, fundamentals: {}}
    Output: dict of symbol -> {signal: "Strong"|"Weak"|"Neutral", reason: str}
    """
    sys_prompt = "You are a quantitative technical analyst. Use your tools to fetch price history and fundamental data. Output ONLY a valid JSON dictionary mapping ticker strings to an object with fields 'signal' (Must be 'Strong', 'Weak', or 'Neutral') and 'reason'. Do not use markdown fences."
    
    # To save tokens, we might need to compress price_data slightly if it's too large,
    # but for 60 stocks it should fit within Gemma's context.
    prompt = f"Analyze the following stocks and determine the technical signal for each stock:\n{json.dumps(price_data, separators=(',', ':'))}"
    
    res = _call_model(MODEL_QUANT, prompt, sys_prompt, expected_json="object", timeout_s=60, fallback_model="gemini-2.5-flash", tools=[market_tools.get_price_history, market_tools.get_fundamentals])
    try:
        return json.loads(res)
    except Exception as e:
        print(f"Failed to parse Quant JSON: {e}")
        return {}


def run_adversarial_agent(candidates: list, portfolio_data: list) -> dict:
    """
    Generates a Counter-Thesis (Bear Case) for 'Strong' + 'Bullish' signals.
    Input: list of symbols
    Output: dict of symbol -> bear_case (str)
    """
    sys_prompt = "You are the Devil's Advocate for an aggressive hedge fund. Your job is to destroy the bullish thesis for these stocks. Be pessimistic. Consider macro headwinds, RSI divergence, high valuation, etc. Output ONLY a valid JSON dictionary mapping ticker strings to a string field containing the bear case. Do not use markdown."
    
    filtered_data = [d for d in portfolio_data if d['symbol'] in candidates]
    
    prompt = f"Write the strongest possible Bear Case AGAINST buying these specific candidate stocks based on their data. Be ruthless:\n{json.dumps(filtered_data, separators=(',', ':'))}"
    
    res = _call_model(MODEL_ADVERSARIAL, prompt, sys_prompt, expected_json="object", timeout_s=60, fallback_model="gemini-2.5-flash")
    try:
        return json.loads(res)
    except Exception as e:
        print(f"Failed to parse Adversarial JSON: {e}")
        return {sym: "Warning: Agent failed to generate bear case." for sym in candidates}



def run_economist_agent(macro_data: dict, sector_data: dict, market_news: dict) -> dict:
    """
    Reads global macro indicators and top Indian market news to output a sector-by-sector
    impact matrix. Predicts which sectors will face headwinds or tailwinds.
    Input: dicts for macro snapshot, sector performance, and market headlines.
    Output: dict of Sector -> {"sentiment": "BULLISH"|"BEARISH"|"NEUTRAL", "reason": str}
    """
    sys_prompt = "You are the Chief Macro-Economist for an institutional fund. Read the global/domestic market cues and determine the systemic outlook for these Indian equity sectors: IT, Bank, Auto, Pharma, FMCG, Energy, Metal, Realty, Media. Use your tools to fetch macro snapshots, sector performance, and market news. Output ONLY a valid JSON dictionary mapping sector names to an object with fields 'sentiment' (Must be 'BULLISH', 'BEARISH', or 'NEUTRAL') and 'reason'. Do not use markdown fences."
    
    state = {
        "macro_snapshot": macro_data,
        "sector_performance_today": sector_data,
        "latest_market_news": market_news
    }
    prompt = f"Generate the sector impact matrix based on this current global state and your tools:\n{json.dumps(state, separators=(',', ':'))}"
    
    res = _call_model(MODEL_ECONOMIST, prompt, sys_prompt, expected_json="object", timeout_s=75, fallback_model="gemini-2.5-flash-lite", tools=[market_tools.get_macro_snapshot, market_tools.get_sector_performance, news_tools.get_market_news])
    try:
        return json.loads(res)
    except Exception as e:
        print(f"Failed to parse Economist JSON: {e}")
        return {}


def run_universe_picker_agent(universe_snapshot: dict) -> list:
    """
    Pick a small watchlist from a broad NSE universe snapshot.

    Input: {
      "held_symbols": [...],
      "candidates": [{"symbol":"XYZ","score":...,"why":...}, ...],
      "constraints": {"max_watchlist": 60}
    }
    Output: JSON array of symbols (strings), length <= max_watchlist.
    """
    sys_prompt = """
You are a swing-trading universe selector for NSE.
Goal: choose a WATCHLIST to research/trade this cycle without scanning the whole market.
You have time; do not over-trade.

Rules:
- ALWAYS include all held_symbols in the output.
- Prefer liquid, trending, catalyst-driven names suitable for multi-day swings.
- Avoid extremely illiquid microcaps.
- Use market_context + theme to rotate into sectors that benefit from current affairs (e.g., war, crude spike, USD/INR move, RBI events).
- If candidate.news.keyword_hits is present, treat it as a strong catalyst signal.
- Output ONLY a valid JSON array of ticker strings, no markdown.
"""
    prompt = "Select the watchlist symbols (JSON array of strings) from this snapshot:\n" + json.dumps(
        universe_snapshot, separators=(",", ":")
    )
    res = _call_model(
        MODEL_UNIVERSE_PICKER,
        prompt,
        sys_prompt,
        expected_json="array",
        timeout_s=45,
        fallback_model="gemini-2.5-flash",
    )
    try:
        parsed = json.loads(res)
        if not isinstance(parsed, list):
            return []
        out = []
        seen = set()
        for x in parsed:
            sym = str(x).strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
        return out
    except Exception:
        return []


def run_theme_agent(market_context: dict) -> dict:
    """
    Summarize current affairs / macro into actionable trading themes.

    Input: {
      "macro_snapshot": {...},
      "sector_performance": {...},
      "fii_dii": {...},
      "market_news": {"headlines":[...], ...}
    }

    Output JSON object:
    {
      "themes": ["WAR_RISK", "CRUDE_SPIKE", ...],
      "favored_sectors": ["Energy", "Metal", ...],
      "avoid_sectors": ["IT", ...],
      "headline_keywords": ["defence", "order", "sanction", ...],
      "notes": "short reasoning"
    }
    """
    sys_prompt = """
You are a macro/news synthesizer for Indian swing trading.
Convert current affairs into portfolio-rotation guidance.

Return ONLY a valid JSON object with:
- themes: array of short tags (e.g. WAR_RISK, RBI_RATE_CUTS, USD_INR_UP, CRUDE_UP, RISK_OFF, RISK_ON)
- favored_sectors: array from [IT,Bank,Auto,Pharma,FMCG,Energy,Metal,Realty,Media,Defence]
- avoid_sectors: same set
- headline_keywords: 8-20 lowercase keywords/phrases to scan in stock headlines for catalysts
- notes: short text

Be practical: during war risk, defence/energy can outperform; during risk-off, prefer quality/low-beta.
"""
    prompt = "Extract themes and sector rotation guidance from:\n" + json.dumps(
        market_context, separators=(",", ":")
    )
    res = _call_model(
        MODEL_THEME,
        prompt,
        sys_prompt,
        expected_json="object",
        timeout_s=45,
        fallback_model="gemini-2.5-flash",
    )
    try:
        parsed = json.loads(res)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except Exception:
        return {}


def run_orchestrator(portfolio_state: dict, researcher_map: dict, quant_map: dict, adv_map: dict, economist_map: dict, trade_journal_context: list, cash: float) -> list:
    """
    The final decision maker. Weighs all inputs and outputs execution JSON.
    """
    sys_prompt = f"""
You are an AGGRESSIVE but calculated Orchestrator Portfolio Manager. Your goal is to ATTACK high-conviction opportunities to maximize absolute returns, while still holding positions for days/weeks to capture full swings.
Primary trade objective per position: capture a 6–7% swing when possible.
Deploy capital aggressively into high-conviction trades. You do not need to hold a cash buffer, and there are no maximum allocation limits per stock.

CRITICAL: At the end of the month we need a NET PROFIT after brokerage and taxes. Real-world Indian trading fees (₹20 brokerage, 0.1% STT, ₹15.93 DP charges) will DESTROY your PnL if you trade frequently for small gains.
- ONLY initiate a BUY if you expect a high-probability >2% profit margin over a multi-day swing.
- DO NOT SELL just because the Quant agent flipped to 'Weak' temporarily if the broader structural trend holds.
- DO NOT SELL a position at a loss unless the thesis is clearly broken OR it breaches the configured hard stop-loss. Otherwise HOLD and allow time for recovery.
- When holding a losing position, prefer to WAIT rather than crystallize a small loss (fees + spread worsen outcomes).

You are receiving data from 4 subordinate agents:
1. Macro-Economist (Sector-level global outlook)
2. Researcher (Stock News Sentiment)
3. Quant (Technical/Fundamental Signal)
4. Adversarial (Bear Case for the best candidates)

VETO RULE: Do NOT BUY a stock if the Macro-Economist has flagged its Sector as BEARISH, regardless of how good the Quant signal is.

And you have context from the Trade Journal on past trades. Learn from past mistakes, particularly over-trading.
Also provided is the `average_slippage` percentage for each stock.
If average slippage for a stock is >0.2%, you MUST automatically adjust your BUY limit orders 
to be 0.1% below the observed current LTP to avoid chasing the price.

Generate the final trade decisions. Only BUY if the Bullish/Strong conviction defeats the Adversarial bear case, and the setup is strong enough for a multi-day swing trade.

Output Format (ONLY valid JSON array):
[
  {{
    "action": "BUY",
    "symbol": "TCS",
    "quantity": 25,
    "limit_price": 3890.50, // Only if slippage >0.2%, else omit
    "bear_case": "...",
    "reason": "..."
    // If you recommend SELLING at a loss (rare), you MUST include:
    // "allow_loss": true
  }}
]
Or SELL, or HOLD.
"""

    state = {
        "portfolio_state": portfolio_state,
        "available_cash": cash,
        "agent_signals": {
            "economist_sector_outlook": economist_map,
            "researcher": researcher_map,
            "quant": quant_map,
            "adversarial_bear_cases": adv_map
        },
        "recent_trade_journal": trade_journal_context
    }

    prompt = f"Make final trading decisions based on all inputs and use tools if you need more data:\n{json.dumps(state, separators=(',', ':'))}"
    
    res = _call_model(
        MODEL_ORCHESTRATOR,
        prompt,
        sys_prompt,
        expected_json="array",
        timeout_s=90,
        fallback_model="gemini-2.5-flash-lite",
        tools=[market_tools.get_price_history, market_tools.get_macro_snapshot, market_tools.get_sector_performance, news_tools.get_stock_news, market_tools.check_market_health]
    )
    try:
        parsed = json.loads(res)
        if isinstance(parsed, dict):
            return [parsed]
        return parsed
    except Exception as e:
        print(f"Failed to parse Orchestrator JSON: {e}")
        print(f"RAW RESPONSE: |{res}|")
        return [{"action": "HOLD", "reason": f"JSON Parse Error: {str(e)}"}]
