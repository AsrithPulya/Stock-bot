"""
groww_broker.py
===============
Groww Trading API wrapper.

  Subscription : ₹499 + GST / month at groww.in/trade-api
  Auth         : API Key + Secret (OAuth2 daily token)
  Docs         : https://groww.in/trade-api/docs

PAPER_TRADE = True  →  all orders are simulated locally (safe default)
PAPER_TRADE = False →  real orders sent to Groww (only after thorough testing!)

Setup (one-time):
  1. Subscribe at groww.in/trade-api
  2. Generate API Key + Secret from Groww Cloud dashboard
  3. export GROWW_API_KEY="your_key"
     export GROWW_API_SECRET="your_secret"
  4. Set PAPER_TRADE = False only when ready to go live
"""

import os
import datetime
import requests

# ── Config ────────────────────────────────────────────────────────────────────
PAPER_TRADE = True   # ← KEEP TRUE until you've tested thoroughly!

GROWW_API_BASE   = "https://groww.in/v1/api"
GROWW_API_KEY    = os.environ.get("GROWW_API_KEY", "")
GROWW_API_SECRET = os.environ.get("GROWW_API_SECRET", "")

_access_token: str = ""
_token_expiry: datetime.datetime = datetime.datetime.min


# ── Authentication ────────────────────────────────────────────────────────────

def _get_access_token() -> str:
    """
    Return a valid Groww access token, refreshing if expired.
    Tokens are valid for one calendar day.
    """
    global _access_token, _token_expiry
    now = datetime.datetime.now()
    if _access_token and now < _token_expiry:
        return _access_token

    if not GROWW_API_KEY or not GROWW_API_SECRET:
        raise EnvironmentError(
            "GROWW_API_KEY and GROWW_API_SECRET env vars not set. "
            "Subscribe at groww.in/trade-api to get your credentials."
        )
    resp = requests.post(
        f"{GROWW_API_BASE}/trade/v1/login",
        json={"apiKey": GROWW_API_KEY, "apiSecret": GROWW_API_SECRET},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _access_token = data["accessToken"]
    # Tokens expire end of trading day — refresh at midnight to be safe
    _token_expiry = now.replace(hour=23, minute=59)
    return _access_token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_get_access_token()}",
            "Content-Type": "application/json"}


# ── Order Placement ───────────────────────────────────────────────────────────

def place_order(symbol: str, action: str, quantity: int,
                order_type: str = "MARKET") -> dict:
    """
    Place a BUY or SELL order via Groww Trading API.

    In PAPER_TRADE mode (default) the order is only simulated — no real money moves.
    Switch PAPER_TRADE = False in groww_broker.py only after thorough testing.

    Args:
        symbol:     NSE ticker (e.g. "TCS", "RELIANCE")
        action:     "BUY" or "SELL"
        quantity:   Number of shares
        order_type: "MARKET" (default) or "LIMIT"

    Returns:
        dict with keys:
          - status:    "PAPER" | "PLACED" | "ERROR"
          - order_id:  str (real order ID from Groww, or "PAPER-xxx")
          - symbol, action, quantity: echoed back
          - message:   human-readable status
    """
    action = action.upper()
    if action not in ("BUY", "SELL"):
        return {"status": "ERROR", "message": f"Invalid action: {action}"}
    if quantity < 1:
        return {"status": "ERROR", "message": "Quantity must be ≥ 1"}

    if PAPER_TRADE:
        import random
        fake_id = f"PAPER-{random.randint(100000, 999999)}"
        return {
            "status":    "PAPER",
            "order_id":  fake_id,
            "symbol":    symbol,
            "action":    action,
            "quantity":  quantity,
            "message":   f"[PAPER TRADE] {action} {quantity} × {symbol} simulated — no real order placed.",
        }

    try:
        payload = {
            "tradingsymbol": symbol,
            "exchange":      "NSE",
            "transaction_type": action,
            "quantity":      quantity,
            "order_type":    order_type,
            "product":       "CNC",     # CNC = delivery (hold positions)
        }
        resp = requests.post(
            f"{GROWW_API_BASE}/trade/v1/order/place",
            json=payload,
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "status":   "PLACED",
            "order_id": data.get("orderId", "unknown"),
            "symbol":   symbol,
            "action":   action,
            "quantity": quantity,
            "message":  f"Order placed: {action} {quantity} × {symbol}",
        }
    except Exception as e:
        return {"status": "ERROR", "message": str(e),
                "symbol": symbol, "action": action, "quantity": quantity}


# ── Portfolio Sync ────────────────────────────────────────────────────────────

def get_groww_holdings() -> dict:
    """
    Fetch live holdings from Groww.
    Returns dict mapping symbol → {quantity, avg_price, current_value}.
    Returns empty dict in PAPER_TRADE mode (local state is authoritative).
    """
    if PAPER_TRADE:
        return {"note": "PAPER_TRADE mode — using local bot state as portfolio"}

    try:
        resp = requests.get(
            f"{GROWW_API_BASE}/trade/v1/holdings",
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        holdings = {}
        for item in resp.json().get("holdings", []):
            sym = item.get("tradingsymbol", "")
            holdings[sym] = {
                "quantity":      item.get("quantity", 0),
                "avg_price":     item.get("averagePrice", 0),
                "current_value": item.get("currentValue", 0),
            }
        return holdings
    except Exception as e:
        return {"error": str(e)}
