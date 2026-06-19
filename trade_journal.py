import os
import datetime
from pymongo import MongoClient
from dotenv import load_dotenv

class TradeJournal:
    def __init__(self):
        load_dotenv()
        self.mongo_uri = os.environ.get("MONGO_URI")
        self.collection = None
        
        if self.mongo_uri:
            try:
                client = MongoClient(self.mongo_uri)
                db = client.get_database("stockbot")
                self.collection = db["trade_history"]
            except Exception as e:
                print(f"⚠️  Could not connect to MongoDB for TradeJournal: {e}")
        else:
            print("⚠️  MONGO_URI not set. TradeJournal will not persist to DB.")

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
            
        if self.collection is not None:
            try:
                self.collection.insert_one(trade)
            except Exception as e:
                print(f"⚠️  Could not save trade to MongoDB: {e}")

    def get_last_5_trades(self, symbol=None):
        """Return the last 5 trades, optionally filtered by symbol, for context."""
        if self.collection is None:
            return []
            
        query = {}
        if symbol:
            query['symbol'] = symbol
            
        try:
            cursor = self.collection.find(query).sort("timestamp", -1).limit(5)
            # Reverse so it's in chronological order (oldest to newest) like the original list
            trades = list(cursor)
            trades.reverse()
            # remove _id for clean json
            for t in trades:
                t.pop('_id', None)
            return trades
        except Exception:
            return []

    @property
    def history(self):
        if self.collection is None:
            return []
        try:
            return list(self.collection.find({}, {"_id": 0}).sort("timestamp", 1))
        except Exception:
            return []
