"""
test_gemini_models.py
=====================
Discover all Gemini models available on your API key, display their known
free-tier rate limits, run a live test call on every model that's suitable
for text generation, and recommend the best model for 24/7 bot operation.

Usage:
    python test_gemini_models.py

Results are saved to:  gemini_model_test_results.json
"""

import time
import json
import datetime
import google.generativeai as genai

# ── API Key ───────────────────────────────────────────────────────────────────
GEMINI_API_KEY = "AIzaSyASyJ-Yf2UUFEMNQAYS0F9cAoU24a3VMuY"   # ← Your key

# ── Test prompt (short, to save tokens) ──────────────────────────────────────
TEST_PROMPT = (
    'Reply with ONLY this JSON and nothing else: '
    '{"status": "ok", "model": "self", "message": "24/7 ready"}'
)

# ── Known free-tier rate limits (as of March 2026) ───────────────────────────
# Source: https://ai.google.dev/pricing
# Format: model_substring -> {"rpm": int, "rpd": int, "tpm": int, "notes": str}
#
# NOTE: Limits can change.  Verify at https://ai.google.dev/pricing
KNOWN_LIMITS = {
    # ─── Gemini 2.5 series ───────────────────────────────────────────────────
    "gemini-2.5-pro": {
        "rpm": 5, "rpd": 100, "tpm": 250_000,
        "notes": "Most capable; very low daily quota — not suitable for 24/7 bots"
    },
    "gemini-2.5-flash-lite": {
        "rpm": 15, "rpd": 1_000, "tpm": 250_000,
        "notes": "⭐ BEST FOR 24/7 — highest RPD on free tier, fast, efficient"
    },
    "gemini-2.5-flash": {
        "rpm": 10, "rpd": 250, "tpm": 250_000,
        "notes": "Good quality; moderate daily quota"
    },
    # ─── Gemini 2.0 series ───────────────────────────────────────────────────
    "gemini-2.0-flash-lite": {
        "rpm": 30, "rpd": 1_500, "tpm": 1_000_000,
        "notes": "⭐ BEST RPM + RPD — excellent for high-frequency 24/7 bots"
    },
    "gemini-2.0-flash": {
        "rpm": 15, "rpd": 1_500, "tpm": 1_000_000,
        "notes": "Great balance of speed and quota; currently used by main.py"
    },
    # ─── Gemini 3 series (preview — limits may change) ───────────────────────
    "gemini-3-flash-preview": {
        "rpm": 10, "rpd": 500, "tpm": 250_000,
        "notes": "Preview — limits not guaranteed; may get 429 frequently"
    },
    "gemini-3.1-flash-lite-preview": {
        "rpm": 15, "rpd": 500, "tpm": 250_000,
        "notes": "Preview — lite, faster; still unstable quotas"
    },
    # ─── Gemma open models ───────────────────────────────────────────────────
    "gemma-3-27b-it": {
        "rpm": 30, "rpd": 14_400, "tpm": 15_000,
        "notes": "⭐ HIGHEST RPD — open model; low TPM limit but JSON fits fine"
    },
    "gemma-3-12b-it": {
        "rpm": 30, "rpd": 14_400, "tpm": 15_000,
        "notes": "Smaller than 27b; same high RPD"
    },
    "gemma-3-4b-it": {
        "rpm": 30, "rpd": 14_400, "tpm": 15_000,
        "notes": "Lightest Gemma 3; fastest response; same high RPD"
    },
}

# Models NOT worth testing (TTS, image, robotics, etc.)
SKIP_KEYWORDS = [
    "tts", "image", "robotics", "computer-use",
    "deep-research", "banana", "nano"
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_limit_info(model_name: str) -> dict:
    """Return the known rate-limit entry for this model (by substring match)."""
    for key, info in KNOWN_LIMITS.items():
        if key in model_name:
            return info
    return {"rpm": "?", "rpd": "?", "tpm": "?", "notes": "Unknown / not mapped"}


def should_skip(model_name: str) -> bool:
    return any(kw in model_name.lower() for kw in SKIP_KEYWORDS)


def run_test(model_name: str) -> dict:
    """
    Send one test prompt to the model and return timing + result info.
    """
    result = {
        "model": model_name,
        "tested_at": datetime.datetime.now().isoformat(),
        "status": "untested",
        "latency_ms": None,
        "response_snippet": None,
        "error": None,
    }
    try:
        model = genai.GenerativeModel(model_name)
        t0 = time.time()
        resp = model.generate_content(TEST_PROMPT)
        latency_ms = round((time.time() - t0) * 1000)
        text = resp.text.strip()
        result.update({
            "status": "ok",
            "latency_ms": latency_ms,
            "response_snippet": text[:200],
        })
    except Exception as e:
        result.update({"status": "error", "error": str(e)})
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    genai.configure(api_key=GEMINI_API_KEY)
    print("\n" + "═" * 70)
    print(" GEMINI MODEL DISCOVERY & RATE-LIMIT REPORT")
    print("═" * 70)

    # 1. List all models
    all_models = [
        m for m in genai.list_models()
        if "generateContent" in m.supported_generation_methods
    ]

    print(f"\n📋 Found {len(all_models)} content-capable models on this API key.\n")

    # 2. Print rate-limit table
    print("─" * 70)
    print(f"{'MODEL':<45} {'RPM':>5} {'RPD':>6} {'TPM':>10}")
    print("─" * 70)
    for m in all_models:
        name = m.name.replace("models/", "")
        if should_skip(name):
            continue
        lim = get_limit_info(name)
        rpm = str(lim.get("rpm", "?"))
        rpd = str(lim.get("rpd", "?"))
        tpm = str(lim.get("tpm", "?"))
        print(f"  {name:<43} {rpm:>5} {rpd:>6} {tpm:>10}")
    print("─" * 70)

    # 3. Print notes for mapped models
    print("\n📝 Model Notes:")
    for key, info in KNOWN_LIMITS.items():
        print(f"  • {key:<40} → {info['notes']}")

    # 4. Run live tests
    print("\n" + "═" * 70)
    print(" LIVE API CALL TESTS  (1 call per model, ~2 s gap)")
    print("═" * 70)

    test_targets = [
        m.name for m in all_models
        if not should_skip(m.name.replace("models/", ""))
    ]

    # Prioritise the models we actually care about so they appear first
    priority = [
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemma-3-4b-it",
        "gemma-3-12b-it",
        "gemma-3-27b-it",
        "gemini-2.5-pro",
    ]

    ordered = []
    for p in priority:
        for t in test_targets:
            if p in t and t not in ordered:
                ordered.append(t)
    for t in test_targets:
        if t not in ordered:
            ordered.append(t)

    results = []
    for model_name in ordered:
        short = model_name.replace("models/", "")
        print(f"\n  ⏳ Testing  {short} …", end="", flush=True)
        r = run_test(model_name)
        lim = get_limit_info(short)
        r["rate_limits"] = lim
        results.append(r)

        if r["status"] == "ok":
            print(f"  ✅  {r['latency_ms']} ms")
        else:
            # Shorten error for display
            err = r["error"] or ""
            if "429" in err or "quota" in err.lower() or "exhausted" in err.lower():
                print(f"  🚫  RATE LIMITED (429)")
            elif "not found" in err.lower() or "not supported" in err.lower():
                print(f"  ❌  NOT AVAILABLE on this key")
            else:
                print(f"  ❌  {err[:80]}")

        time.sleep(2)   # polite gap between calls

    # 5. Recommendation
    print("\n" + "═" * 70)
    print(" RECOMMENDATION FOR 24/7 BOT OPERATION")
    print("═" * 70)

    working = [r for r in results if r["status"] == "ok"]

    def score(r):
        lim = r.get("rate_limits", {})
        rpd = lim.get("rpd", 0)
        rpm = lim.get("rpm", 0)
        latency = r.get("latency_ms") or 9999
        # Guard: only score if we have numeric limits
        rpd = rpd if isinstance(rpd, (int, float)) else 0
        rpm = rpm if isinstance(rpm, (int, float)) else 0
        latency = latency if isinstance(latency, (int, float)) else 9999
        # Weight: daily quota matters most, then RPM, then latency
        return rpd * 10 + rpm * 5 - latency * 0.01

    working_sorted = sorted(working, key=score, reverse=True)

    if working_sorted:
        best = working_sorted[0]
        lim = best.get("rate_limits", {})
        print(f"\n  🏆  Best model  : {best['model']}")
        print(f"      RPM         : {lim.get('rpm', '?')}")
        print(f"      RPD         : {lim.get('rpd', '?')}")
        print(f"      Latency     : {best['latency_ms']} ms")
        print(f"      Notes       : {lim.get('notes', '')}")

        # Safe call interval suggestion
        rpm = lim.get("rpm")
        rpd = lim.get("rpd")
        if isinstance(rpm, int) and rpm > 0:
            interval = max(60 / rpm + 1, 4)   # +1s safety buffer, min 4s
            print(f"\n  ⏱️  Recommended MIN_API_INTERVAL_SECS = {interval:.0f}  "
                  f"(safe for {rpm} RPM)")
        if isinstance(rpd, int) and rpd > 0:
            calls_per_hour = rpd / 24
            print(f"  📊  Max calls/hour to stay within RPD : {calls_per_hour:.0f}")
            print(f"      ↳ That's 1 call every "
                  f"{3600 / calls_per_hour:.0f} s on average")

        print(f"\n  📋 Top-3 working models:")
        for i, r in enumerate(working_sorted[:3], 1):
            lim = r.get("rate_limits", {})
            print(f"      {i}. {r['model']}  "
                  f"| RPM={lim.get('rpm','?')}  RPD={lim.get('rpd','?')}  "
                  f"| Latency={r['latency_ms']}ms")
    else:
        print("\n  ⚠️  No models responded successfully — check your API key or quota.")

    # 6. Save JSON results
    out_file = "gemini_model_test_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "run_at": datetime.datetime.now().isoformat(),
            "api_key_prefix": GEMINI_API_KEY[:10] + "...",
            "total_models_found": len(all_models),
            "results": results,
            "recommendation": working_sorted[0]["model"] if working_sorted else None,
        }, f, indent=2)

    print(f"\n  💾  Full results saved → {out_file}")
    print("═" * 70 + "\n")

    # 7. Recommend model string for main.py
    if working_sorted:
        model_str = working_sorted[0]["model"].replace("models/", "")
        print("  📝  To use the best model in main.py, update line ~407 to:\n")
        print(f"        model = genai.GenerativeModel('{model_str}', ...)\n")


if __name__ == "__main__":
    main()
