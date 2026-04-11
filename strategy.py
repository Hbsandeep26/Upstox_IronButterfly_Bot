# strategy.py
import config
import logging

def calculate_iron_butterfly_legs(index_symbol, spot_price, option_chain_data):
    logging.info("Calculating Iron Butterfly strikes & prices...")
    interval = 50 if index_symbol == "NIFTY" else 100
    atm_strike = round(spot_price / interval) * interval

    atm_ce_ltp, atm_pe_ltp = 0, 0
    sell_ce_key, sell_pe_key = "", ""

    for strike_data in option_chain_data:
        if strike_data.get('strike_price') == atm_strike:
            call_info, put_info = strike_data.get('call_options', {}), strike_data.get('put_options', {})
            if call_info and put_info:
                sell_ce_key, atm_ce_ltp = call_info.get('instrument_key'), call_info.get('market_data', {}).get('ltp', 0)
                sell_pe_key, atm_pe_ltp = put_info.get('instrument_key'), put_info.get('market_data', {}).get('ltp', 0)
            break

    if not sell_ce_key or not sell_pe_key: return None, None

    target_ce_buy = atm_ce_ltp * config.WING_PERCENT
    target_pe_buy = atm_pe_ltp * config.WING_PERCENT

    best_ce_diff, best_pe_diff = float('inf'), float('inf')
    buy_ce_key, buy_pe_key = "", ""
    buy_ce_ltp, buy_pe_ltp = 0, 0

    for strike_data in option_chain_data:
        strike = strike_data.get('strike_price')
        call_info, put_info = strike_data.get('call_options', {}), strike_data.get('put_options', {})

        if strike > atm_strike and call_info:
            ce_ltp = call_info.get('market_data', {}).get('ltp', 0)
            if ce_ltp > 0 and abs(ce_ltp - target_ce_buy) < best_ce_diff:
                best_ce_diff, buy_ce_key, buy_ce_ltp = abs(ce_ltp - target_ce_buy), call_info.get('instrument_key'), ce_ltp

        if strike < atm_strike and put_info:
            pe_ltp = put_info.get('market_data', {}).get('ltp', 0)
            if pe_ltp > 0 and abs(pe_ltp - target_pe_buy) < best_pe_diff:
                best_pe_diff, buy_pe_key, buy_pe_ltp = abs(pe_ltp - target_pe_buy), put_info.get('instrument_key'), pe_ltp

    legs = {"sell_ce": sell_ce_key, "sell_pe": sell_pe_key, "buy_ce": buy_ce_key, "buy_pe": buy_pe_key}
    prices = {"sell_ce": atm_ce_ltp, "sell_pe": atm_pe_ltp, "buy_ce": buy_ce_ltp, "buy_pe": buy_pe_ltp}
    
    logging.info(f"Selected Execution Legs: {legs}")
    logging.info(f"Target Entry Prices: {prices}")

    # Add this dictionary right before the return statement
    strikes = {
        "sell_ce": atm_strike,         # <-- Replace with your actual ATM Strike variable
        "sell_pe": atm_strike,         # <-- Replace with your actual ATM Strike variable
        "buy_ce": buy_ce_strike,       # <-- Replace with your upper wing strike variable
        "buy_pe": buy_pe_strike        # <-- Replace with your lower wing strike variable
    }
    
    logging.info(f"Selected Strikes: {strikes}")
    
    # Change the return line to return all 3 items:
    return legs, prices, strikes
    
    #return legs, prices

import state_manager
import logging
import time

def risk_management_evaluator(live_data, legs):
    """
    Evaluates risk based on Total Premium with a Ratchet Trailing Stop Loss.
    """
    state = state_manager.load_state()
    if not state or 'entry_prices' not in state:
        return False, {}

    entry = state['entry_prices']
    
    # Check if all 4 legs have live data arriving
    if not all(key in live_data for key in legs.values()):
        return False, {}

    # Extract live LTPs
    live_prices = {
        'sell_ce': live_data[legs['sell_ce']]['ltp'],
        'sell_pe': live_data[legs['sell_pe']]['ltp'],
        'buy_ce': live_data[legs['buy_ce']]['ltp'],
        'buy_pe': live_data[legs['buy_pe']]['ltp']
    }

    # Math: Total Premium = (Sell CE + Sell PE) - (Buy CE + Buy PE)
    entry_premium = (entry['sell_ce'] + entry['sell_pe']) - (entry['buy_ce'] + entry['buy_pe'])
    current_premium = (live_prices['sell_ce'] + live_prices['sell_pe']) - (live_prices['buy_ce'] + live_prices['buy_pe'])

    # --- THE RATCHET TSL ENGINE ---
    
    # Fetch the lowest premium seen so far from the state memory, default to entry premium
    lowest_premium_seen = state.get('lowest_premium', entry_premium)
    
    # If the current premium is the lowest we've seen, update the memory
    if current_premium < lowest_premium_seen:
        lowest_premium_seen = current_premium
        state['lowest_premium'] = lowest_premium_seen
        # Save the updated high-water mark back to the JSON file
        state_manager.save_state(state['index_symbol'], state['legs'], state['entry_prices'], state['quantity'])
        # (Note: make sure your state_manager.save_state function can accept/merge this extra key, 
        # or just let it update the dictionary and write it to the file).

    # 1. Base Stop Loss: 30% Loss
    dynamic_stop_loss = entry_premium * 1.30
    status_message = "Initial SL"

    # 2. Step 1 TSL: If we hit 30% profit, lock in 20%
    if lowest_premium_seen <= (entry_premium * 0.70):
        dynamic_stop_loss = entry_premium * 0.80
        status_message = "TSL Locked @ 20% Profit"
        
    # 3. Step 2 TSL: If we hit 20% profit, lock in 10%
    elif lowest_premium_seen <= (entry_premium * 0.80):
        dynamic_stop_loss = entry_premium * 0.90
        status_message = "TSL Locked @ 10% Profit"

    # --- EXECUTION TRIGGERS ---
    
    # Trigger the exit if the current premium spikes back up and hits our dynamic stop loss
    if current_premium >= dynamic_stop_loss:
        if dynamic_stop_loss < entry_premium:
            logging.critical(f"✅ TRAILING STOP HIT: Locked in profit! Exiting at ({current_premium:.2f}).")
        else:
            logging.critical(f"🛑 HARD STOP LOSS HIT: Premium expanded to ({current_premium:.2f}). Exiting.")
        return True, live_prices

    # Normal heartbeat log every ~10 seconds
    if int(time.time()) % 10 == 0:
        logging.info(f"Live: ₹{current_premium:.2f} | Lowest: ₹{lowest_premium_seen:.2f} | Target SL: ₹{dynamic_stop_loss:.2f} ({status_message})")

    return False, live_prices
