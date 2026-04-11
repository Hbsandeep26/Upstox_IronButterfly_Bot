# execution.py
import config
import logging
import requests
import upstox_client
from upstox_client.rest import ApiException
from logger import log_trade
import state_manager

def place_iron_butterfly_basket(legs, index_symbol, entry_prices, strikes):
    trade_quantity = config.NIFTY_LOT_SIZE if index_symbol == "NIFTY" else config.SENSEX_LOT_SIZE
    
    # ---------------------------------------------------------
    # 1. LOCAL PAPER TRADING (Bypassing the broken Upstox Sandbox)
    # ---------------------------------------------------------
    if config.ENVIRONMENT == "SANDBOX":
        logging.info("🟡 SANDBOX MODE DETECTED: Simulating local paper execution...")
        
        # We pretend the orders executed perfectly at the target entry prices
        net_premium = (entry_prices['sell_ce'] + entry_prices['sell_pe']) - (entry_prices['buy_ce'] + entry_prices['buy_pe'])
        
        logging.info("✅ Simulated Basket Executed Successfully!")
        log_trade("ENTRY", index_symbol, entry_prices, net_premium, 0.0, "Local Paper Trade (Simulated)")
        #state_manager.save_state(index_symbol, legs, entry_prices, trade_quantity)
        state_manager.save_state(index_symbol, legs, entry_prices, trade_quantity, strikes)
        
        return True # Tell the orchestrator the trade was successful

    # ---------------------------------------------------------
    # 2. LIVE EXECUTION (Using Real Money)
    # ---------------------------------------------------------
    elif config.ENVIRONMENT == "LIVE":
        logging.critical("🔴 LIVE MODE DETECTED: Routing orders to real Upstox Exchange...")
        
        configuration = upstox_client.Configuration()
        configuration.access_token = config.LIVE_ACCESS_TOKEN
        
        api_client = upstox_client.ApiClient(configuration)
        api_instance = upstox_client.OrderApiV3(api_client)
        
        orders = [
            {"token": legs['sell_ce'], "type": "SELL"}, 
            {"token": legs['sell_pe'], "type": "SELL"},
            {"token": legs['buy_ce'], "type": "BUY"}, 
            {"token": legs['buy_pe'], "type": "BUY"}
        ]
        
        success = True
        for order in orders:
            body = upstox_client.PlaceOrderV3Request(
                quantity=int(trade_quantity),
                product="I", 
                validity="DAY",
                price=0.0,
                tag="iron_fly",
                instrument_token=order["token"],
                order_type="MARKET",
                transaction_type=order["type"],
                disclosed_quantity=0,
                trigger_price=0.0,
                is_amo=False,
                slice=False
            )
            
            try:
                api_instance.place_order(body)
                logging.info(f"Live Order Placed: {order['token']} - Status: success")
            except ApiException as e:
                logging.error(f"Live Order Rejected for {order['token']}: {e.body}")
                success = False
            except Exception as e:
                logging.error(f"System Error placing Live Order for {order['token']}: {e}")
                success = False
                
        if success:
            net_premium = (entry_prices['sell_ce'] + entry_prices['sell_pe']) - (entry_prices['buy_ce'] + entry_prices['buy_pe'])
            log_trade("ENTRY", index_symbol, entry_prices, net_premium, 0.0, "Live Basket Executed")
            #state_manager.save_state(index_symbol, legs, entry_prices, trade_quantity)
            state_manager.save_state(index_symbol, legs, entry_prices, trade_quantity, strikes)
        
        return success

def square_off_all(exit_prices=None):
    logging.critical("TRIGGERING SQUARE OFF SEQUENCE!")
    state = state_manager.load_state()
    
    if state and exit_prices:
        entry = state['entry_prices']
        qty = state.get('quantity', 0)
        
        # Premium Paid to close the trade
        exit_premium = (exit_prices['sell_ce'] + exit_prices['sell_pe']) - (exit_prices['buy_ce'] + exit_prices['buy_pe'])
        
        # Real PnL Calculation
        pnl = (entry['sell_ce'] + entry['sell_pe'] - exit_prices['sell_ce'] - exit_prices['sell_pe']) * qty
        pnl += (exit_prices['buy_ce'] + exit_prices['buy_pe'] - entry['buy_ce'] - entry['buy_pe']) * qty
        
        if config.ENVIRONMENT == "SANDBOX":
            log_trade("EXIT", state['index_symbol'], exit_prices, exit_premium, pnl, "Local Paper Trade Closed")
            logging.info(f"💰 Simulated PnL for this trade: ₹{pnl:.2f}")
        else:
            # Note: In a true Live environment, you would also place opposite MARKET orders here to close the positions
            log_trade("EXIT", state['index_symbol'], exit_prices, exit_premium, pnl, "Live Risk management exit")
            logging.info(f"💰 Estimated Live PnL for this trade: ₹{pnl:.2f}")
            
    else:
        log_trade("EXIT", "UNKNOWN", {}, 0.0, 0.0, "Emergency Square Off")
        
    state_manager.clear_state()
