# Approximate pricing per 1 Million tokens (in USD)
# (Using conservative estimates for gemini-2.5-flash and gemini-2.5-flash-lite)
FLASH_IN_PRICE = 0.15
FLASH_OUT_PRICE = 0.60

LITE_IN_PRICE = 0.075
LITE_OUT_PRICE = 0.30

# Cycles
MARKET_HOURS = 6.25 # 9:15 to 15:30
CYCLES_PER_HOUR = 60 / 5 # 5 min loop
CYCLES_PER_DAY = MARKET_HOURS * CYCLES_PER_HOUR # 75

# Constants
STOCKS = 80
TOKENS_PER_STOCK_NEWS = 60
TOKENS_PER_STOCK_DATA = 120

# 1. Researcher (Flash-Lite)
res_in = STOCKS * TOKENS_PER_STOCK_NEWS
res_out = STOCKS * 20 # symbol + label + reason

# 2. Quant (Flash-Lite)
quant_in = STOCKS * TOKENS_PER_STOCK_DATA
quant_out = STOCKS * 20

# 3. Adversarial (Flash-Lite) 
# Usually only runs on ~5-10 candidates
adv_in = 10 * (TOKENS_PER_STOCK_NEWS + TOKENS_PER_STOCK_DATA)
adv_out = 10 * 50 # Bear case string

# 4. Orchestrator (Flash)
orch_in = STOCKS * (TOKENS_PER_STOCK_NEWS + TOKENS_PER_STOCK_DATA) + STOCKS * 40 # plus previous outputs
orch_out = 5 * 100 # Makes maybe 5 decisions

# Daily Lite Usage
daily_lite_in = CYCLES_PER_DAY * (res_in + quant_in + adv_in)
daily_lite_out = CYCLES_PER_DAY * (res_out + quant_out + adv_out)

# Daily Flash Usage
daily_flash_in = CYCLES_PER_DAY * orch_in
daily_flash_out = CYCLES_PER_DAY * orch_out

# Costs
cost_lite = (daily_lite_in / 1e6) * LITE_IN_PRICE + (daily_lite_out / 1e6) * LITE_OUT_PRICE
cost_flash = (daily_flash_in / 1e6) * FLASH_IN_PRICE + (daily_flash_out / 1e6) * FLASH_OUT_PRICE

total_usd = cost_lite + cost_flash
total_inr = total_usd * 85 # roughly 85-87 INR per USD

days = 1000 / total_inr

print(f"Daily cost (USD): ${total_usd:.2f}")
print(f"Daily cost (INR): ₹{total_inr:.2f}")
print(f"Total Trading Days: {days:.1f}")
print(f"Total Calendar Months: {(days / 20):.1f}")
