# data_feed.py
import config
import logging
import requests
import urllib.parse
import upstox_client
import json

def get_spot_price(index_symbol):
    """
    Fetches the live spot price of Nifty or Sensex using Upstox API.
    """
    logging.info(f"Fetching live spot price for {index_symbol}...")
    
    if index_symbol == "NIFTY":
        instrument_key = "NSE_INDEX|Nifty 50"
    elif index_symbol == "SENSEX":
        instrument_key = "BSE_INDEX|SENSEX"
    else:
        logging.error("Invalid Index Symbol provided.")
        return None

    url = 'https://api.upstox.com/v2/market-quote/quotes'
    
    safe_instrument_key = urllib.parse.quote(instrument_key)
    full_url = f"{url}?instrument_key={safe_instrument_key}"
    
    headers = {
        'accept': 'application/json',
        'Api-Version': '2.0',
        'Authorization': f'Bearer {config.LIVE_ACCESS_TOKEN}'
    }

    try:
        response = requests.get(full_url, headers=headers)
        response.raise_for_status() 
        data = response.json()
        
        response_key = instrument_key.replace('|', ':')
        
        if 'data' in data and response_key in data['data']:
            live_price = data['data'][response_key]['last_price']
            logging.info(f"Live Spot Price for {index_symbol} is: {live_price}")
            return live_price
        else:
            logging.error(f"Failed to parse price. Raw Data: {data}")
            return None

    except Exception as e:
        logging.error(f"Upstox API Error fetching spot price: {e}")
        return None

def get_option_chain(index_symbol, expiry_date):
    """
    Fetches the full option chain for the index and given expiry date.
    """
    logging.info(f"Fetching option chain for {index_symbol} expiring on {expiry_date}...")
    
    if index_symbol == "NIFTY":
        instrument_key = "NSE_INDEX|Nifty 50"
    elif index_symbol == "SENSEX":
        instrument_key = "BSE_INDEX|SENSEX"
    else:
        return []

    url = 'https://api.upstox.com/v2/option/chain'
    
    safe_instrument_key = urllib.parse.quote(instrument_key)
    full_url = f"{url}?instrument_key={safe_instrument_key}&expiry_date={expiry_date}"
    
    headers = {
        'accept': 'application/json',
        'Api-Version': '2.0',
        'Authorization': f'Bearer {config.LIVE_ACCESS_TOKEN}'
    }

    try:
        response = requests.get(full_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if 'data' in data:
            return data['data'] 
        else:
            logging.error(f"Option chain data missing. Raw Response: {data}")
            return []
            
    except Exception as e:
        logging.error(f"Upstox API Error fetching option chain: {e}")
        return []


def monitor_live_prices(instrument_keys_dict, callback_function):
    logging.info("Initializing SDK WebSocket connection for live risk management...")
    
    keys_to_subscribe = list(instrument_keys_dict.values())
    
    configuration = upstox_client.Configuration()
    configuration.access_token = config.LIVE_ACCESS_TOKEN
    api_client = upstox_client.ApiClient(configuration)
    
    streamer = upstox_client.MarketDataStreamerV3(api_client, keys_to_subscribe, "full")
    
    # --- NEW: We added 'latest_prices' to act as a memory cache ---
    state = {"stop_loss_hit": False, "error_count": 0, "exit_prices": {}, "latest_prices": {}}

    def on_message(message):
        try:
            state["error_count"] = 0 
            
            if isinstance(message, str):
                message = json.loads(message)
                
            feeds = message.get("feeds", {})
            
            for instrument_key, feed_data in feeds.items():
                ltp = 0.0
                
                if "fullFeed" in feed_data:
                    market_ff = feed_data["fullFeed"].get("marketFF", {})
                    ltp = market_ff.get("ltpc", {}).get("ltp", 0.0)
                elif "ltpc" in feed_data:
                    ltp = feed_data.get("ltpc", {}).get("ltp", 0.0)
                    
                if ltp > 0:
                    # Update our memory cache with the new price
                    state["latest_prices"][instrument_key] = {'ltp': ltp}
            
            # Now we pass the FULL memory cache to the UI and Risk Manager
            if state["latest_prices"]:
                with open("live_prices.json", "w") as f:
                    json.dump(state["latest_prices"], f)
                    
                # Evaluate the full cached prices
                stop_loss_triggered, current_prices = callback_function(state["latest_prices"], instrument_keys_dict)
                
                if stop_loss_triggered:
                    logging.critical("Risk limit reached! Terminating WebSocket connection.")
                    state["stop_loss_hit"] = True
                    state["exit_prices"] = current_prices
                    streamer.disconnect() 
                    
        except Exception as e:
            logging.error(f"Error parsing live tick data: {e}")

    # ... [Keep your on_error and the rest of the function the same] ...


    def on_error(error):
        logging.error(f"WebSocket Error: {error}")
        state["error_count"] += 1
        
        # FAIL-SAFE: If it drops 5 times consecutively, kill the trade
        if state["error_count"] >= 5:
            logging.critical("CRITICAL: Maximum WebSocket failures reached. Initiating emergency square-off!")
            state["stop_loss_hit"] = True 
            streamer.disconnect()

    # Bind our unified logic to the streamer events
    streamer.on("message", on_message)
    streamer.on("error", on_error)
    
    logging.info("WebSocket Connected! Streaming live market data...")
    
    # 1. Start the background streaming thread
    streamer.connect()
    
    # 2. Block the main thread so it waits patiently for the Risk Manager
    import time
    while not state["stop_loss_hit"]:
        time.sleep(1)
        
    # Return both the trigger boolean AND the exact exit prices to execution.py
    return state["stop_loss_hit"], state.get("exit_prices", {})
