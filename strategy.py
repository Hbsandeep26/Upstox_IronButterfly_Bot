# strategy.py
import config
import logging
import state_manager
import time

def calculate_iron_butterfly_legs(index_symbol, spot_price, option_chain_data):
    logging.info("Calculating Iron Butterfly strikes & prices...")
    interval = 50 if index_symbol == "NIFTY" else 100
    atm_strike = round(spot_price / interval) * interval

    atm_ce_ltp, atm_pe_ltp = 0, 0
    sell_ce_key, sell_pe_key = "", ""

    # Find the ATM strikes
    for strike_data in option_chain_data:
        if strike_data.get('strike_price') == atm_strike:
            call_info, put_info = strike_data.get('call_options', {}), strike_data.get('put_options', {})
            if call_info and put_info:
                sell_ce_key, atm_ce_ltp = call_info.get('instrument_key'), call_info.get('market_data', {}).get('ltp', 0)
                sell_pe_key, atm_pe_ltp = put_info.get('instrument_key'), put_info.get('market_data', {}).get('ltp', 0)
            break

    if not sell_ce_key or not sell_pe_key: 
        return None, None, None

    target_ce_buy = atm_ce_ltp * config.WING_PERCENT
    target_pe_buy = atm_pe_ltp * config.WING_PERCENT

    best_ce_diff, best_pe_diff = float('inf'), float('inf')
    
    buy_ce_key, buy_pe_key = "", ""
    buy_ce_ltp, buy_pe_ltp = 0, 0
    buy_ce_strike, buy_pe_strike = 0, 0 

    # Find the optimal protective wings
    for strike_data in option_chain_data:
        strike = strike_data.get('strike_price')
        call_info, put_info = strike_data.get('call_options', {}), strike_data.get('put_options', {})

        if strike > atm_strike and call_info:
            ce_ltp = call_info.get('market_data', {}).get('ltp', 0)
            if ce_ltp > 0 and abs(ce_ltp - target_ce_buy) < best_ce_diff:
                best_ce_diff, buy_ce_key, buy_ce_ltp = abs(ce_ltp - target_ce_buy), call_info.get('instrument_key'), ce_ltp
                buy_ce_strike = strike

        if strike < atm_strike and put_info:
            pe_ltp = put_info.get('market_data', {}).get('ltp', 0)
            if pe_ltp > 0 and abs(pe_ltp - target_pe_buy) < best_pe_diff:
                best_pe_diff, buy_pe_key, buy_pe_ltp = abs(pe_ltp - target_pe_buy), put_info.get('instrument_key'), pe_ltp
                buy_pe_strike = strike

    legs = {"sell_ce": sell_ce_key, "sell_pe": sell_pe_key, "buy_ce": buy_ce_key, "buy_pe": buy_pe_key}
    prices = {"sell_ce": atm_ce_ltp, "sell_pe": atm_pe_ltp, "buy_ce": buy_ce_ltp, "buy_pe": buy_pe_ltp}
    
    strikes = {
        "sell_ce": atm_strike,         
        "sell_pe": atm_strike,         
        "buy_ce": buy_ce_strike,       
        "buy_pe": buy_pe_strike        
    }

    logging.info(f"Selected Execution Legs: {legs}")
    logging.info(f"Target Entry Prices: {prices}")
    logging.info(f"Selected Strikes: {strikes}")

    return legs, prices, strikes


def risk_management_evaluator(live_data, legs):
    import os
    import logging
    import config
    import state_manager

    state = state_manager.load_state()
    if not state or 'entry_prices' not in state:
        return False, {}

    entries = state['entry_prices']

    # --- 1. PARSE LIVE PRICES FIRST ---
    # We build the clean dictionary immediately so we can return it safely for any exit signal.
    live_sell_ce = live_data.get(legs['sell_ce'], {}).get('ltp', entries['sell_ce'])
    live_sell_pe = live_data.get(legs['sell_pe'], {}).get('ltp', entries['sell_pe'])
    live_buy_ce = live_data.get(legs['buy_ce'], {}).get('ltp', entries['buy_ce'])
    live_buy_pe = live_data.get(legs['buy_pe'], {}).get('ltp', entries['buy_pe'])

    current_prices = {
        'sell_ce': live_sell_ce,
        'sell_pe': live_sell_pe,
        'buy_ce': live_buy_ce,
        'buy_pe': live_buy_pe
    }

    # --- 2. TACTICAL MANUAL EXIT CHECK ---
    manual_exit_file = "manual_exit_flag.txt"
    if os.path.exists(manual_exit_file):
        with open(manual_exit_file, "r") as f:
            if f.read().strip() == "TRUE":
                logging.critical("🛑 MANUAL EXIT TRIGGERED FROM UI! Forcing Square Off.")
                os.remove(manual_exit_file)
                # FIX: Return the clean dictionary!
                return "MANUAL_EXIT", current_prices 

    # --- 2.5 TIME-BASED EOD EXIT (BTST CHECK) ---
    import datetime
    now = datetime.datetime.now()
    
    # Check if it is exactly 3:15 PM (15:15) or later
    if now.hour > 15 or (now.hour == 15 and now.minute >= 15):
        btst_file = "btst_flag.txt"
        btst_enabled = False
        if os.path.exists(btst_file):
            with open(btst_file, "r") as f:
                btst_enabled = (f.read().strip() == "TRUE")
                
        if not btst_enabled:
            logging.critical("⏰ 3:15 PM CUTOFF REACHED! BTST is disabled. Forcing End of Day Square Off.")
            return "TIME_EXIT", current_prices


    # --- 3. TAKE PROFIT CHECK (The Ceiling) ---
    entry_net = (entries['sell_ce'] + entries['sell_pe']) - (entries['buy_ce'] + entries['buy_pe'])
    
    # Get the dynamic target from the UI (defaults to 20%)
    target_pct = config.get_target_profit_pct() / 100.0
    target_exit_premium = entry_net * (1.0 - target_pct)
    
    live_net = (live_sell_ce + live_sell_pe) - (live_buy_ce + live_buy_pe)
    
    if live_net <= target_exit_premium:
        logging.critical(f"🎯 TARGET REACHED! Net premium decayed to ₹{live_net:.2f} (Target: ₹{target_exit_premium:.2f}).")
        # FIX: Return the clean dictionary!
        return "TAKE_PROFIT", current_prices

    # --- 4. HALF-PREMIUM STOP LOSS CHECK (The Floor) ---
    # Force strict floats to completely prevent "String Math" bugs
    entry_sell_ce = float(entries['sell_ce'])
    entry_sell_pe = float(entries['sell_pe'])

    limit_ce = entry_sell_ce * 2.0
    limit_pe = entry_sell_pe * 2.0

    if float(live_sell_ce) >= limit_ce:
        logging.warning(f"🚨 STOP LOSS: Call leg doubled! (Entry: ₹{entry_sell_ce:.2f}, Limit: ₹{limit_ce:.2f}, Live: ₹{float(live_sell_ce):.2f})")
        return "STOP_LOSS", current_prices

    if float(live_sell_pe) >= limit_pe:
        logging.warning(f"🚨 STOP LOSS: Put leg doubled! (Entry: ₹{entry_sell_pe:.2f}, Limit: ₹{limit_pe:.2f}, Live: ₹{float(live_sell_pe):.2f})")
        return "STOP_LOSS", current_prices

    # If no exits are triggered, keep holding
    return False, {}
    
