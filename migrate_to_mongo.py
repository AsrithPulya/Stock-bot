import os
import json
from dotenv import load_dotenv
from pymongo import MongoClient

# Load environment variables
load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI")

def migrate():
    if not MONGO_URI:
        print("❌ MONGO_URI not found in .env file.")
        print("Please add it as: MONGO_URI=\"mongodb+srv://...\"")
        return

    print("🔌 Connecting to MongoDB...")
    try:
        client = MongoClient(MONGO_URI)
        db = client.get_database("stockbot")
        
        # 1. Migrate State
        state_file = "trading_bot_state.json"
        if os.path.exists(state_file):
            print(f"📦 Found {state_file}. Migrating state...")
            with open(state_file, "r") as f:
                state_data = json.load(f)
            
            # We only want one state document. Upsert it with a hardcoded ID.
            state_coll = db["bot_state"]
            state_coll.replace_one({"_id": "current_state"}, state_data, upsert=True)
            print("✅ State migrated successfully.")
        else:
            print(f"⚠️ {state_file} not found. Skipping state migration.")

        # 2. Migrate Trade History
        history_file = "trade_history.json"
        if os.path.exists(history_file):
            print(f"📜 Found {history_file}. Migrating trade history...")
            with open(history_file, "r") as f:
                history_data = json.load(f)
            
            if history_data:
                history_coll = db["trade_history"]
                # Clear existing to prevent duplicates if run multiple times
                history_coll.delete_many({})
                history_coll.insert_many(history_data)
                print(f"✅ Trade history ({len(history_data)} trades) migrated successfully.")
            else:
                print("ℹ️ Trade history is empty. Nothing to migrate.")
        else:
            print(f"⚠️ {history_file} not found. Skipping trade history migration.")

        print("\n🎉 Migration Complete! You can now safely run the bot using MongoDB.")

    except Exception as e:
        print(f"❌ Error connecting to MongoDB or migrating data: {e}")

if __name__ == "__main__":
    migrate()
