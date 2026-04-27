import datetime

def calculate_slippage(trade):
    """
    Calculates Decision Latency Slippage and Execution Slippage for a given trade.
    
    Args:
        trade (dict): A trade dictionary from trade_history.json. Expected to have keys:
            - 'observed_ltp'
            - 'order_requested_price'
            - 'execution_actual_price'
            - 'action'
            - 'quantity'
            
    Returns:
        tuple: (decision_latency_slippage, execution_slippage, total_slippage_value_inr)
    """
    if "observed_ltp" not in trade or "order_requested_price" not in trade or "execution_actual_price" not in trade:
        return 0.0, 0.0, 0.0

    observed_ltp = trade["observed_ltp"]
    requested = trade["order_requested_price"]
    actual = trade["execution_actual_price"]
    action = trade["action"]
    quantity = trade.get("quantity", 0)

    # For BUY:  positive slippage means we paid more than expected
    # For SELL: positive slippage means we received less than expected
    if action == "BUY":
        decision_slippage = requested - observed_ltp
        exec_slippage = actual - requested
        total_slippage_value = (actual - observed_ltp) * quantity
    elif action == "SELL":
        decision_slippage = observed_ltp - requested
        exec_slippage = requested - actual
        total_slippage_value = (observed_ltp - actual) * quantity
    else:
        return 0.0, 0.0, 0.0

    return decision_slippage, exec_slippage, total_slippage_value


def get_average_weekly_slippage(symbol, trade_journal):
    """
    Computes the rolling average total slippage percentage for a stock over the last 7 days.
    """
    now = datetime.datetime.now()
    seven_days_ago = now - datetime.timedelta(days=7)
    
    trades = trade_journal.history
    symbol_trades = [t for t in trades if t.get('symbol') == symbol]
    
    recent_slippage_pcts = []
    
    for t in symbol_trades:
        try:
            trade_time = datetime.datetime.fromisoformat(t["timestamp"])
        except ValueError:
            continue
            
        if trade_time >= seven_days_ago:
            if "observed_ltp" in t and "execution_actual_price" in t and "action" in t:
                observed = t["observed_ltp"]
                actual = t["execution_actual_price"]
                action = t["action"]
                
                if observed > 0:
                    if action == "BUY":
                        pct = (actual - observed) / observed * 100
                        recent_slippage_pcts.append(pct)
                    elif action == "SELL":
                        pct = (observed - actual) / observed * 100
                        recent_slippage_pcts.append(pct)

    if not recent_slippage_pcts:
        return 0.0
        
    return sum(recent_slippage_pcts) / len(recent_slippage_pcts)


def get_total_slippage_leakage_today(trade_journal):
    """
    Computes total Rupees lost to slippage across all trades today.
    """
    today = datetime.datetime.now().date()
    trades = trade_journal.history
    
    total_leakage = 0.0
    
    for t in trades:
        try:
            trade_date = datetime.datetime.fromisoformat(t["timestamp"]).date()
        except ValueError:
            continue
            
        if trade_date == today:
            _, _, val = calculate_slippage(t)
            total_leakage += val
            
    return total_leakage
