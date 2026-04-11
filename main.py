# main.py
import os
import logging

# 1. ANCHOR THE LOG FILE TO YOUR PROJECT FOLDER
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
log_file_path = os.path.join(BASE_DIR, "bot.log")

# 2. FORCE PYTHON TO USE OUR LOGGING RULES
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ],
    force=True  # <-- THE MAGIC FIX: This overrides any hidden library loggers
)

import schedule
import time
from datetime import datetime
import config
import state_manager

# Import our custom modules
from data_feed import get_spot_price, get_option_chain, monitor_live_prices
from strategy import calculate_iron_butterfly_legs, risk_management_evaluator
from execution import place_iron_butterfly_basket, square_off_all


def continuous_trading_session(index_symbol, expiry_date, cutoff_hour, cutoff_minute):
    logging.info(f"--- STARTING CONTINUOUS SESSION FOR {index_symbol} ---")

    # --- 1. BTST WAKE-UP PROTOCOL ---
    # If a trade was carried forward from yesterday, wake it up and resume monitoring!
    state = state_manager.load_state()
    if state and state.get("active"):
        logging.critical(f"🌙 BTST CARRY FORWARD DETECTED: Waking up existing {index_symbol} trade.")
        stop_loss_hit, exit_prices = monitor_live_prices(state['legs'], risk_management_evaluator)
        
        if stop_loss_hit:
            logging.warning("Stop Loss hit on Carry Forward trade! Squaring off...")
            square_off_all(exit_prices)
            logging.info("Cooling down for 60 seconds...")
            time.sleep(60)
        return # Once the carried-forward trade is done, exit this session.

    # --- 2. NORMAL INTRADAY DEPLOYMENT ---
    while True:
        now = datetime.now()
        if now.hour > cutoff_hour or (now.hour == cutoff_hour and now.minute >= cutoff_minute):
            logging.info(f"Cutoff time {cutoff_hour}:{cutoff_minute} reached. Stopping continuous loop.")
            break

        logging.info("Deploying fresh Iron Butterfly...")

        spot = get_spot_price(index_symbol)
        if not spot:
            time.sleep(30)
            continue
            
        chain = get_option_chain(index_symbol, expiry_date)
        if not chain:
            time.sleep(30)
            continue

        #legs, entry_prices = calculate_iron_butterfly_legs(index_symbol, spot, chain)
        legs, entry_prices, strikes = calculate_iron_butterfly_legs(index_symbol, spot, chain)

        if not legs:
            time.sleep(30)
            continue
        
        #execution_success = place_iron_butterfly_basket(legs, index_symbol, entry_prices)
        execution_success = place_iron_butterfly_basket(legs, index_symbol, entry_prices, strikes)
        
        if not execution_success:
            logging.error("❌ Execution failed. Aborting risk monitor. Retrying in 30 seconds...")
            time.sleep(30)
            continue
            
        logging.info("Entering live risk monitoring phase...")

        stop_loss_hit, exit_prices = monitor_live_prices(legs, risk_management_evaluator)
        
        if stop_loss_hit:
            logging.warning("Stop Loss hit! Squaring off and preparing to redeploy...")
            square_off_all(exit_prices) 
            logging.info("Cooling down for 60 seconds...")
            time.sleep(60)
        else:
            logging.error("WebSocket connection terminated unexpectedly.")
            break 

    # --- 3. END OF DAY: BTST CHECK ---
    logging.info(f"--- END OF INTRADAY SESSION FOR {index_symbol} ---")
    
    # Read the BTST toggle switch from the Dashboard
    btst_file = os.path.join(BASE_DIR, "btst_flag.txt")
    if os.path.exists(btst_file):
        with open(btst_file, "r") as f:
            flag = f.read().strip()
        if flag == "TRUE":
            logging.critical(f"🌙 BTST ENABLED: Carrying forward {index_symbol} position overnight. Skipping square-off.")
            return # Let the trade sleep. It will wake up tomorrow morning.

    # If the switch is OFF, square off normally
    logging.info(f"Initiating scheduled intraday square off for {index_symbol}...")
    square_off_all()



# --- STATE RECOVERY (SELF-HEALING) ---
def recover_orphaned_trade():
    """Checks for abandoned trades on startup and resumes monitoring."""
    state = state_manager.load_state()
    if state and state.get("active"):
        logging.critical("🔄 ORPHANED TRADE DETECTED ON BOOT! Initiating recovery sequence...")
        
        index_symbol = state.get("index_symbol")
        legs = state.get("legs")
        
        logging.info(f"Resuming Risk Monitor for {index_symbol}...")
        
        # Jump directly back into the WebSocket loop using the saved legs
        stop_loss_triggered, exit_prices = monitor_live_prices(legs, risk_management_evaluator)
        
        if stop_loss_triggered:
            logging.info("Risk threshold met during recovery phase. Executing Square Off.")
            square_off_all(exit_prices)
        
        logging.info("Recovery complete. Returning to normal schedule.")
    else:
        logging.info("No active trades found on boot. System is clean.")


# --- STRICT INTRADAY SCHEDULER STATE MACHINE ---
NEXT_NIFTY_EXPIRY = config.NIFTY_EXPIRY
NEXT_SENSEX_EXPIRY = config.SENSEX_EXPIRY

# Note: The final trade of the day is strictly cut off at 15:15 (3:15 PM) to avoid overnight carry
schedule.every().monday.at("09:16").do(continuous_trading_session, index_symbol="NIFTY", expiry_date=NEXT_NIFTY_EXPIRY, cutoff_hour=15, cutoff_minute=15)

schedule.every().tuesday.at("09:16").do(continuous_trading_session, index_symbol="NIFTY", expiry_date=NEXT_NIFTY_EXPIRY, cutoff_hour=12, cutoff_minute=0)
schedule.every().tuesday.at("12:35").do(continuous_trading_session, index_symbol="SENSEX", expiry_date=NEXT_SENSEX_EXPIRY, cutoff_hour=15, cutoff_minute=15)

schedule.every().wednesday.at("09:16").do(continuous_trading_session, index_symbol="SENSEX", expiry_date=NEXT_SENSEX_EXPIRY, cutoff_hour=15, cutoff_minute=15)

schedule.every().thursday.at("10:30").do(continuous_trading_session, index_symbol="SENSEX", expiry_date=NEXT_SENSEX_EXPIRY, cutoff_hour=12, cutoff_minute=0)
schedule.every().thursday.at("12:01").do(continuous_trading_session, index_symbol="NIFTY", expiry_date=NEXT_NIFTY_EXPIRY, cutoff_hour=15, cutoff_minute=15)

schedule.every().friday.at("09:16").do(continuous_trading_session, index_symbol="NIFTY", expiry_date=NEXT_NIFTY_EXPIRY, cutoff_hour=15, cutoff_minute=15)

if __name__ == "__main__":
    logging.info("System Architect Bot V3 Initialized...")
    if not getattr(config, 'LIVE_ACCESS_TOKEN', None) or not getattr(config, 'SANDBOX_ACCESS_TOKEN', None):
        logging.warning("One or both tokens are missing in config.py. API calls will fail.")
    
    # 1. Check for and recover any interrupted trades first
    recover_orphaned_trade()
    
    # 2. Enter the infinite waiting loop
    logging.info("Waiting for scheduled events...")
    while True:
        schedule.run_pending()
        time.sleep(1)
