# main.py
import os
import logging
import json
import schedule
import time
from datetime import datetime
import config
import state_manager

# Import our custom modules
from data_feed import get_spot_price, get_option_chain, monitor_live_prices
from strategy import calculate_iron_butterfly_legs, risk_management_evaluator
from execution import place_iron_butterfly_basket, square_off_all

# ANCHOR THE LOG FILE & PATHS
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
log_file_path = os.path.join(BASE_DIR, "bot.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ],
    force=True 
)


def continuous_trading_session(index_symbol, expiry_date, cutoff_hour, cutoff_minute):
    logging.info(f"--- STARTING CONTINUOUS SESSION FOR {index_symbol} ---")

    # Wipe any old manual exit clicks before starting a fresh session
    manual_exit_file = os.path.join(BASE_DIR, "manual_exit_flag.txt")
    if os.path.exists(manual_exit_file):
        os.remove(manual_exit_file)
        
    # ... (Rest of your BTST wake-up protocol) ...

    # --- 1. BTST WAKE-UP PROTOCOL ---
    state = state_manager.load_state()
    if state and state.get("active"):
        logging.critical(f"🌙 BTST CARRY FORWARD DETECTED: Waking up existing {index_symbol} trade.")
        stop_loss_hit, exit_prices = monitor_live_prices(state['legs'], risk_management_evaluator)
        
        if stop_loss_hit:
            logging.warning("Stop Loss hit on Carry Forward trade! Squaring off...")
            square_off_all(exit_prices)
            time.sleep(60)
        return

    # --- 2. NORMAL INTRADAY DEPLOYMENT ---
    while True:
        now = datetime.now()
        
        # THE GRACEFUL HANDOFF: Soft Cutoff.
        # It checks the clock BEFORE taking a new trade. Active trades are untouched.
        if now.hour > cutoff_hour or (now.hour == cutoff_hour and now.minute >= cutoff_minute):
            logging.info(f"⏰ Soft Cutoff ({cutoff_hour}:{cutoff_minute}) reached. No new {index_symbol} trades will be taken.")
            break 

        logging.info(f"Deploying fresh Iron Butterfly for {index_symbol}...")

        spot = get_spot_price(index_symbol)
        chain = get_option_chain(index_symbol, expiry_date) if spot else None
        
        if not spot or not chain:
            time.sleep(30)
            continue

        legs, entry_prices, strikes = calculate_iron_butterfly_legs(index_symbol, spot, chain)
        if not legs:
            time.sleep(30)
            continue
        
        execution_success = place_iron_butterfly_basket(legs, index_symbol, entry_prices, strikes)
        
        if not execution_success:
            time.sleep(30)
            continue
            
        logging.info("Entering live risk monitoring phase...")

        # The bot will safely stay locked in this monitor, even if it passes the cutoff time!

        stop_loss_hit, exit_prices = monitor_live_prices(legs, risk_management_evaluator)

        if stop_loss_hit == "MANUAL_EXIT":
            logging.critical(f"🛑 Manual Exit executed. Squaring off {index_symbol}...")
            square_off_all(exit_prices)
            logging.info("Cooling down for 60 seconds before looking for new setups...")
            time.sleep(60)

        elif stop_loss_hit == "TAKE_PROFIT":
            logging.critical(f"💰 PROFIT LOCKED for {index_symbol}! Squaring off positions.")
            square_off_all(exit_prices)
            logging.info("Cooling down for 60 seconds before looking for new setups...")
            time.sleep(60)

        elif stop_loss_hit == "STOP_LOSS":
            logging.warning(f"🚨 Stop Loss hit for {index_symbol}! Squaring off...")
            square_off_all(exit_prices)
            logging.info("Cooling down for 60 seconds...")
            time.sleep(60)

        elif stop_loss_hit == "TIME_EXIT":
            logging.critical(f"⏰ EOD Cutoff triggered for {index_symbol}. Squaring off and ending session.")
            square_off_all(exit_prices)
            break # Breaks the loop so it goes home for the day!

        elif stop_loss_hit:
            logging.warning(f"Stop Loss hit for {index_symbol}! Squaring off...")
            square_off_all(exit_prices)
            logging.info("Cooling down for 60 seconds...")
            time.sleep(60)

        else:
            logging.error("WebSocket connection terminated unexpectedly.")
            break
        

    logging.info(f"--- END OF SESSION FOR {index_symbol} ---")
    
    # --- 3. END OF DAY: BTST CHECK ---
    btst_file = os.path.join(BASE_DIR, "btst_flag.txt")
    if os.path.exists(btst_file) and open(btst_file, "r").read().strip() == "TRUE":
        if state_manager.load_state():
            logging.critical(f"🌙 BTST ENABLED: Carrying forward {index_symbol} overnight.")
            return 

    # --- 4. SAFETY SQUARE OFF ---
    if state_manager.load_state():
        logging.info(f"Initiating final safety square off for {index_symbol}...")
        
        # Reach into the memory cache to get the final prices to pass to Upstox
        exit_prices = None
        live_file = os.path.join(BASE_DIR, "live_prices.json")
        if os.path.exists(live_file):
            with open(live_file, "r") as f:
                latest_ticks = json.load(f)
            
            state = state_manager.load_state()
            if state:
                legs = state['legs']
                try:
                    exit_prices = {
                        'sell_ce': latest_ticks[legs['sell_ce']]['ltp'],
                        'sell_pe': latest_ticks[legs['sell_pe']]['ltp'],
                        'buy_ce': latest_ticks[legs['buy_ce']]['ltp'],
                        'buy_pe': latest_ticks[legs['buy_pe']]['ltp']
                    }
                except KeyError:
                    pass # Failsafe if the cache is unexpectedly empty
                    
        square_off_all(exit_prices)


def build_todays_schedule():
    """
    Reads the UI-selected dates and builds today's schedule dynamically.
    Prioritizes the expiring index in the morning session.
    """
    schedule.clear('trading_jobs')

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # --- 1. MARKET HOLIDAY CHECK ---
    if hasattr(config, 'MARKET_HOLIDAYS') and today_str in config.MARKET_HOLIDAYS:
        logging.critical(f"🌴 MARKET HOLIDAY DETECTED ({today_str}). The bot will sleep all day.")
        return  # This instantly exits the function. No trades will be scheduled!


    # --- READ UI EXPIRIES ---
    expiry_file = os.path.join(BASE_DIR, "expiries.json")
    nifty_expiry = "UNKNOWN"
    sensex_expiry = "UNKNOWN"

    if os.path.exists(expiry_file):
        try:
            with open(expiry_file, "r") as f:
                data = json.load(f)
                nifty_expiry = data.get("NIFTY", "UNKNOWN")
                sensex_expiry = data.get("SENSEX", "UNKNOWN")
        except Exception:
            pass

    # --- THE UI-ANCHORED ROUTER ---
    if today_str == nifty_expiry:
        logging.critical(f"🎯 NIFTY EXPIRY DETECTED ({today_str}). Loading Nifty Relay.")
        # Morning: NIFTY (Expiring Index)
        schedule.every().day.at("09:16").do(continuous_trading_session, index_symbol="NIFTY", expiry_date=nifty_expiry, cutoff_hour=12, cutoff_minute=30).tag('trading_jobs')
        # Afternoon: SENSEX (Safe Index) takes over after Nifty finishes
        schedule.every().day.at("12:31").do(continuous_trading_session, index_symbol="SENSEX", expiry_date=sensex_expiry, cutoff_hour=15, cutoff_minute=15).tag('trading_jobs')

    elif today_str == sensex_expiry:
        logging.critical(f"🎯 SENSEX EXPIRY DETECTED ({today_str}). Loading Sensex Relay.")
        # Morning: SENSEX (Expiring Index)
        schedule.every().day.at("09:16").do(continuous_trading_session, index_symbol="SENSEX", expiry_date=sensex_expiry, cutoff_hour=12, cutoff_minute=30).tag('trading_jobs')
        # Afternoon: NIFTY (Safe Index) takes over after Sensex finishes
        schedule.every().day.at("12:31").do(continuous_trading_session, index_symbol="NIFTY", expiry_date=nifty_expiry, cutoff_hour=15, cutoff_minute=15).tag('trading_jobs')

    # ... (Keep your NIFTY and SENSEX UI Expiry if/elif blocks) ...
    
    else:
        weekday = now.strftime("%A").upper()
        if weekday in ["WEDNESDAY", "THURSDAY"]:
            logging.info(f"📅 Normal Trading Day ({today_str} - {weekday}). Defaulting to SENSEX.")
            schedule.every().day.at("09:16").do(continuous_trading_session, index_symbol="SENSEX", expiry_date=sensex_expiry, cutoff_hour=15, cutoff_minute=15).tag('trading_jobs')
        else:
            logging.info(f"📅 Normal Trading Day ({today_str} - {weekday}). Defaulting to NIFTY.")
            schedule.every().day.at("09:16").do(continuous_trading_session, index_symbol="NIFTY", expiry_date=nifty_expiry, cutoff_hour=15, cutoff_minute=15).tag('trading_jobs')


if __name__ == "__main__":
    logging.info("System Architect Bot V3 Initialized...")
    if not getattr(config, 'LIVE_ACCESS_TOKEN', None) or not getattr(config, 'SANDBOX_ACCESS_TOKEN', None):
        logging.warning("One or both tokens are missing in config.py. API calls will fail.")

    # 1. Build the schedule for TODAY immediately upon booting
    build_todays_schedule()

    # 2. Tell the bot to read the UI and re-run the schedule builder every morning at 8:00 AM
    schedule.every().day.at("08:00").do(build_todays_schedule)

    # 3. --- 🚨 ORPHANED TRADE RECOVERY ---
    import state_manager
    recovered_state = state_manager.load_state()

    if recovered_state and recovered_state.get("active"):
        rec_index = recovered_state.get("index_symbol", "UNKNOWN")
        logging.critical(f"🔄 ORPHANED TRADE DETECTED ON BOOT! Instantly recovering {rec_index} session...")

        # Bypass the schedule and instantly jump into the live session
        continuous_trading_session(
            index_symbol=rec_index,
            expiry_date="RECOVERY",
            cutoff_hour=15,
            cutoff_minute=15
        )
    # -------------------------------------------

    # 4. Enter the infinite waiting loop
    logging.info("Waiting for scheduled events...")
    while True:
        schedule.run_pending()
        time.sleep(1)
