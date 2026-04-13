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
    """
    Evaluates risk based strictly on the "Half Premium" logic for ALL days.
    Exits if the Live Price of the Short CE is half (or less) of the Short PE, or vice versa.
    """
    state = state_manager.load_state()
    if not state or 'entry_prices' not in state:
        return False, {}

    if not all(key in live_data for key in legs.values()):
        return False, {}

    live_prices = {
        'sell_ce': live_data[legs['sell_ce']]['ltp'],
        'sell_pe': live_data[legs['sell_pe']]['ltp'],
        'buy_ce': live_data[legs['buy_ce']]['ltp'],
        'buy_pe': live_data[legs['buy_pe']]['ltp']
    }

    current_sell_ce = live_prices['sell_ce']
    current_sell_pe = live_prices['sell_pe']

    if current_sell_ce > 0 and current_sell_pe > 0:
        
        # Condition 1: CE deflates, PE spikes (Market is crashing downward)
        if current_sell_ce <= (current_sell_pe / 2):
            logging.critical(f"🛑 EXIT TRIGGERED: Sell CE (₹{current_sell_ce:.2f}) is half or less of Sell PE (₹{current_sell_pe:.2f}).")
            return True, live_prices
        
        # Condition 2: PE deflates, CE spikes (Market is rallying upward)
        if current_sell_pe <= (current_sell_ce / 2):
            logging.critical(f"🛑 EXIT TRIGGERED: Sell PE (₹{current_sell_pe:.2f}) is half or less of Sell CE (₹{current_sell_ce:.2f}).")
            return True, live_prices

    # Normal heartbeat log every ~10 seconds
    if int(time.time()) % 10 == 0:
        logging.info(f"Live Balance -> Sell CE: ₹{current_sell_ce:.2f} | Sell PE: ₹{current_sell_pe:.2f}")

    return False, live_prices
