import json
import os
import datetime

class TradeJournal:
    def __init__(self, filename="trade_history.json"):
        self.filename = filename
        self.history = self.load()

    def load(self):
        if not os.path.exists(self.filename):
            return []
        try:
            with open(self.filename, 'r') as f:
                return json.load(f)
        except Exception:
            return []

    def save(self):
        try:
            with open(self.filename, 'w') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"⚠️  Could not save trade journal: {e}")

    def log_trade(self, symbol, action, quantity, price, reason, pnl=None, pnl_pct=None, 
                  observed_ltp=None, order_requested_price=None, execution_actual_price=None):
        trade = {
            "timestamp": datetime.datetime.now().isoformat(),
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "price": price,
            "reason": reason,
        }
        if pnl is not None:
            trade["realized_pnl"] = pnl
        if pnl_pct is not None:
            trade["pnl_pct"] = pnl_pct
            
        # Add Slippage Monitoring Data
        if observed_ltp is not None:
            trade["observed_ltp"] = observed_ltp
        if order_requested_price is not None:
            trade["order_requested_price"] = order_requested_price
        if execution_actual_price is not None:
            trade["execution_actual_price"] = execution_actual_price
            
        self.history.append(trade)
        self.save()

    def get_last_5_trades(self, symbol=None):
        """Return the last 5 trades, optionally filtered by symbol, for context."""
        trades = self.history
        if symbol:
            trades = [t for t in trades if t.get('symbol') == symbol]
        return trades[-5:]
