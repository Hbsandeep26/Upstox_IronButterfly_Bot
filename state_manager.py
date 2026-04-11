# state_manager.py
import json
import os
import logging

STATE_FILE = "trade_state.json"

def save_state(index_symbol, legs, entry_prices, quantity, strikes=None):
    state = {
        "active": True,
        "index_symbol": index_symbol,
        "legs": legs,
        "entry_prices": entry_prices,
        "quantity": quantity,
        "strikes": strikes or {}  # <-- ADD THIS LINE
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)



def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                state = json.load(f)
                if state.get("active"):
                    return state
            except json.JSONDecodeError:
                return None
    return None

def clear_state():
    if os.path.exists(STATE_FILE):
        state = {"active": False}
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
        logging.info("🗑️ State cleared. Ready for new trades.")
