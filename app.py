import streamlit as st
import json
import os
import signal
import subprocess
import sys
import datetime
import time
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv
from trade_journal import TradeJournal
import performance_monitor

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Constants (must match main.py) ──────────────────────────────────────────
STATE_FILE = "trading_bot_state.json"
LOG_FILE   = "bot_log.txt"
PID_FILE   = "bot.pid"
INITIAL_CASH = 1_00_000

NSE_HOLIDAYS_2026 = {
    datetime.date(2026, 1, 26), datetime.date(2026, 2, 26),
    datetime.date(2026, 3, 20), datetime.date(2026, 4, 2),
    datetime.date(2026, 4, 3),  datetime.date(2026, 4, 14),
    datetime.date(2026, 5, 1),  datetime.date(2026, 6, 17),
    datetime.date(2026, 8, 15), datetime.date(2026, 8, 27),
    datetime.date(2026, 10, 2), datetime.date(2026, 10, 20),
    datetime.date(2026, 10, 21),datetime.date(2026, 11, 5),
    datetime.date(2026, 12, 25),
}

# ─── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* Dark gradient background */
.stApp {
    background: linear-gradient(135deg, #0a0e1a 0%, #0f1629 50%, #0a1020 100%);
    color: #e2e8f0;
}

/* Hide default streamlit elements */
#MainMenu, footer, header { visibility: hidden; }

/* Metric cards */
.metric-card {
    background: linear-gradient(135deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.02) 100%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 20px 24px;
    backdrop-filter: blur(10px);
}
.metric-label { font-size: 12px; font-weight: 500; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px; }
.metric-value { font-size: 28px; font-weight: 700; color: #f1f5f9; }
.metric-sub   { font-size: 13px; margin-top: 4px; }
.metric-pos   { color: #22c55e; }
.metric-neg   { color: #ef4444; }
.metric-neu   { color: #94a3b8; }

/* Section headers */
.section-header {
    font-size: 16px; font-weight: 600; color: #cbd5e1;
    padding: 12px 0 8px; border-bottom: 1px solid rgba(255,255,255,0.06);
    margin-bottom: 12px;
}

/* Status badges */
.badge {
    display: inline-block; padding: 4px 12px; border-radius: 999px;
    font-size: 12px; font-weight: 600; letter-spacing: 0.05em;
}
.badge-green  { background: rgba(34,197,94,0.15);  color: #22c55e; border: 1px solid rgba(34,197,94,0.3); }
.badge-red    { background: rgba(239,68,68,0.15);   color: #ef4444; border: 1px solid rgba(239,68,68,0.3); }
.badge-yellow { background: rgba(234,179,8,0.15);   color: #eab308; border: 1px solid rgba(234,179,8,0.3); }
.badge-gray   { background: rgba(100,116,139,0.15); color: #94a3b8; border: 1px solid rgba(100,116,139,0.3); }

/* Log terminal */
.log-box {
    background: #0d1117;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 16px;
    font-family: 'Menlo', 'Monaco', monospace;
    font-size: 12px;
    color: #adbac7;
    max-height: 340px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.6;
}

/* Control buttons */
.stButton > button {
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-size: 14px !important;
    padding: 10px 24px !important;
    border: none !important;
    width: 100% !important;
    transition: all 0.2s !important;
}

/* Dataframe styling */
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def load_state():
    load_dotenv()
    mongo_uri = os.environ.get("MONGO_URI")
    if mongo_uri:
        try:
            client = MongoClient(mongo_uri)
            db = client.get_database("stockbot")
            coll = db["bot_state"]
            state = coll.find_one({"_id": "current_state"})
            if state:
                return state
        except Exception as e:
            pass
            
    # Fallback to local
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def is_bot_running():
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)   # signal 0 = check existence only
        return True
    except Exception:
        return False


def start_bot():
    try:
        proc = subprocess.Popen(
            [sys.executable, "main.py"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1.5)   # give it a moment to write PID
        return True
    except Exception as e:
        st.error(f"Failed to start bot: {e}")
        return False


def stop_bot():
    if not os.path.exists(PID_FILE):
        return
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGINT)
        time.sleep(0.5)
    except Exception:
        pass
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def is_market_open_now():
    now = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    )
    if now.weekday() > 4:
        return False
    if now.date() in NSE_HOLIDAYS_2026:
        return False
    t = now.time()
    return datetime.time(9, 15) <= t <= datetime.time(15, 30)


def read_log(n_lines=120):
    if not os.path.exists(LOG_FILE):
        return "(No log yet — start the bot to see activity here)"
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(lines[-n_lines:])
    except Exception:
        return "(Unable to read log)"


def clear_log():
    try:
        open(LOG_FILE, "w").close()
    except Exception:
        pass


def fmt_inr(val):
    return f"₹{val:,.2f}"


def pnl_color_class(val):
    if val > 0:   return "metric-pos"
    if val < 0:   return "metric-neg"
    return "metric-neu"


# ─── Auto-refresh ────────────────────────────────────────────────────────────
REFRESH_SECS = 15
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = time.time()
if time.time() - st.session_state.last_refresh > REFRESH_SECS:
    st.session_state.last_refresh = time.time()
    st.rerun()


# ─── Header ───────────────────────────────────────────────────────────────────
col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown("## 🔥 Stock Bot Dashboard")
    st.markdown(
        "<span style='color:#64748b;font-size:13px'>NSE/BSE Algorithmic Trading | Gemini AI Powered</span>",
        unsafe_allow_html=True,
    )
with col_status:
    running = is_bot_running()
    market  = is_market_open_now()
    badge_bot    = "badge-green" if running    else "badge-gray"
    badge_market = "badge-green" if market     else "badge-red"
    bot_lbl      = "🟢 Bot Running" if running else "⚫ Bot Stopped"
    mkt_lbl      = "🟢 Market Open" if market  else "🔴 Market Closed"
    st.markdown(
        f"<div style='text-align:right;margin-top:16px'>"
        f"<span class='badge {badge_bot}'>{bot_lbl}</span>&nbsp;&nbsp;"
        f"<span class='badge {badge_market}'>{mkt_lbl}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

st.markdown("---")

# ─── Bot Controls ─────────────────────────────────────────────────────────────
st.markdown("<div class='section-header'>⚙️ Bot Controls</div>", unsafe_allow_html=True)
ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([1, 1, 1, 2])

with ctrl1:
    if not running:
        if st.button("▶ Start Bot", key="start"):
            start_bot()
            st.rerun()
    else:
        st.button("▶ Start Bot", disabled=True, key="start_dis")

with ctrl2:
    if running:
        if st.button("⏹ Stop Bot", key="stop"):
            stop_bot()
            st.rerun()
    else:
        st.button("⏹ Stop Bot", disabled=True, key="stop_dis")

with ctrl3:
    if st.button("🗑 Clear Log", key="clear_log"):
        clear_log()
        st.rerun()

with ctrl4:
    now_ist = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    )
    st.markdown(
        f"<div style='text-align:right;color:#64748b;font-size:12px;padding-top:12px'>"
        f"🕐 IST {now_ist.strftime('%d %b %Y, %H:%M:%S')} &nbsp;|&nbsp; Auto-refresh every {REFRESH_SECS}s"
        f"</div>",
        unsafe_allow_html=True,
    )

st.markdown("---")

# ─── Load State ───────────────────────────────────────────────────────────────
state = load_state()

# ─── Portfolio Metrics ────────────────────────────────────────────────────────
st.markdown("<div class='section-header'>💼 Portfolio Overview</div>", unsafe_allow_html=True)

if state:
    cash         = state.get("cash", INITIAL_CASH)
    initial_cash = state.get("initial_cash", INITIAL_CASH)
    total_trades = state.get("total_trades", 0)
    holdings     = state.get("holdings", {})
    avg_cost     = state.get("avg_cost", {})
    stock_data   = state.get("stock_data", {})
    last_updated = state.get("last_updated", "—")

    # Compute current portfolio value
    stock_value = 0.0
    for sym, qty in holdings.items():
        if qty > 0:
            hist = stock_data.get(sym, {}).get("price_history", [])
            if hist:
                stock_value += qty * hist[-1]
    total_value = cash + stock_value
    pnl_abs     = total_value - initial_cash
    pnl_pct     = (pnl_abs / initial_cash * 100) if initial_cash > 0 else 0
    cash_pct    = (cash / total_value * 100) if total_value > 0 else 100

    pnl_cls = pnl_color_class(pnl_abs)
    pnl_sign = "+" if pnl_abs >= 0 else ""

    # Get daily slippage
    tj = TradeJournal()
    slippage_today = performance_monitor.get_total_slippage_leakage_today(tj)
    
    # 1% slippage warning threshold
    slippage_warning_threshold = initial_cash * 0.075 * 0.01

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    metrics = [
        (m1, "Total Portfolio",   fmt_inr(total_value),  None,   None),
        (m2, "Available Cash",    fmt_inr(cash),          f"{cash_pct:.1f}% of port", "metric-neu"),
        (m3, "Stock Value",       fmt_inr(stock_value),   None,   None),
        (m4, "P&L",               f"{pnl_sign}{fmt_inr(pnl_abs)}", f"{pnl_sign}{pnl_pct:.2f}%", pnl_cls),
        (m5, "Total Trades",      str(total_trades),      f"Since {last_updated[:10] if last_updated != '—' else '—'}", "metric-neu"),
        (m6, "Slippage (Today)",  f"₹{slippage_today:.2f}", "🚨 High Friction!" if slippage_today > slippage_warning_threshold else "Normal", "metric-neg" if slippage_today > slippage_warning_threshold else "metric-neu"),
    ]
    for col, label, value, sub, sub_cls in metrics:
        sub_html = f"<div class='metric-sub {sub_cls or ''}'>{sub}</div>" if sub else ""
        col.markdown(
            f"<div class='metric-card'>"
            f"<div class='metric-label'>{label}</div>"
            f"<div class='metric-value'>{value}</div>"
            f"{sub_html}"
            f"</div>",
            unsafe_allow_html=True,
        )

else:
    st.info("📂 No portfolio state found yet. Start the bot to begin trading.")
    cash = INITIAL_CASH; holdings = {}; avg_cost = {}; stock_data = {}; total_trades = 0

st.markdown("---")

# ─── Holdings Table ────────────────────────────────────────────────────────────
st.markdown("<div class='section-header'>📦 Current Holdings</div>", unsafe_allow_html=True)

if state:
    rows = []
    for sym, qty in holdings.items():
        if qty > 0:
            hist     = stock_data.get(sym, {}).get("price_history", [])
            cur_price= hist[-1] if hist else 0
            avg      = avg_cost.get(sym, 0)
            value    = qty * cur_price
            pnl_v    = (cur_price - avg) * qty
            pnl_p    = ((cur_price - avg) / avg * 100) if avg > 0 else 0
            rows.append({
                "Symbol":        sym,
                "Qty":           qty,
                "Avg Cost (₹)":  f"{avg:,.2f}",
                "CMP (₹)":       f"{cur_price:,.2f}",
                "Value (₹)":     f"{value:,.2f}",
                "P&L (₹)":       f"{'+' if pnl_v>=0 else ''}{pnl_v:,.2f}",
                "P&L %":         f"{'+' if pnl_p>=0 else ''}{pnl_p:.2f}%",
            })

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Symbol":       st.column_config.TextColumn("Symbol", width="small"),
                "Qty":          st.column_config.NumberColumn("Qty",    width="small"),
                "Avg Cost (₹)": st.column_config.TextColumn("Avg Cost", width="medium"),
                "CMP (₹)":      st.column_config.TextColumn("CMP",     width="medium"),
                "Value (₹)":    st.column_config.TextColumn("Value",   width="medium"),
                "P&L (₹)":      st.column_config.TextColumn("P&L ₹",  width="medium"),
                "P&L %":        st.column_config.TextColumn("P&L %",   width="small"),
            },
        )
    else:
        st.markdown(
            "<div style='color:#64748b;padding:20px;text-align:center;background:rgba(255,255,255,0.02);"
            "border-radius:12px;border:1px solid rgba(255,255,255,0.06)'>No open positions</div>",
            unsafe_allow_html=True,
        )
else:
    st.markdown(
        "<div style='color:#64748b;padding:20px;text-align:center;background:rgba(255,255,255,0.02);"
        "border-radius:12px;border:1px solid rgba(255,255,255,0.06)'>Start the bot to see holdings</div>",
        unsafe_allow_html=True,
    )

st.markdown("---")

# ─── Live Log ─────────────────────────────────────────────────────────────────
st.markdown("<div class='section-header'>📟 Live Bot Log</div>", unsafe_allow_html=True)

@st.fragment(run_every=3)
def live_log_panel():
    log_text = read_log()
    st.markdown(f"<div class='log-box'>{log_text}</div>", unsafe_allow_html=True)

live_log_panel()

st.markdown("---")

# ─── Footer ───────────────────────────────────────────────────────────────────
st.markdown(
    "<p style='text-align:center;color:#334155;font-size:11px'>"
    "⚠️ This bot is for educational purposes. Not financial advice. Trade at your own risk."
    "</p>",
    unsafe_allow_html=True,
)
