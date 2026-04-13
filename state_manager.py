# state_manager.py
import json
import os

STATE_FILE = "trade_state.json"

def save_state(index_symbol, legs, entry_prices, quantity, strikes=None):
    # Safely load existing state just to preserve strikes if updating an open trade
    existing_state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                existing_state = json.load(f)
        except Exception:
            pass

    state = {
        "active": True,
        "index_symbol": index_symbol,
        "legs": legs,
        "entry_prices": entry_prices,
        "quantity": quantity,
        "strikes": strikes or existing_state.get("strikes", {})
    }
    
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
