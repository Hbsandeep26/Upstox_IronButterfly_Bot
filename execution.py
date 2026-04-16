# execution.py
import config
import logging
import requests
import upstox_client
from upstox_client.rest import ApiException
from logger import log_trade
import state_manager
from notifier import send_telegram_alert

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
        configuration.access_token = config.get_live_token()
        
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
       
            alert_msg = (
                f"🟢 <b>TRADE DEPLOYED: {index_symbol}</b>\n"
                f"Net Premium Collected: ₹{net_premium:.2f}\n"
                f"Quantity: {trade_quantity}"
            )   
            send_telegram_alert(alert_msg)

        return success


def square_off_all(exit_prices=None):
    logging.critical("TRIGGERING SQUARE OFF SEQUENCE!")
    state = state_manager.load_state()

    if state and exit_prices:
        entry = state['entry_prices']
        qty = state.get('quantity', 0)
        legs = state['legs']
        index_symbol = state['index_symbol']

        # Premium Paid to close the trade
        exit_premium = (exit_prices['sell_ce'] + exit_prices['sell_pe']) - (exit_prices['buy_ce'] + exit_prices['buy_pe'])

        # Real PnL Calculation
        pnl = (entry['sell_ce'] + entry['sell_pe'] - exit_prices['sell_ce'] - exit_prices['sell_pe']) * qty
        pnl += (exit_prices['buy_ce'] + exit_prices['buy_pe'] - entry['buy_ce'] - entry['buy_pe']) * qty

        if config.ENVIRONMENT == "SANDBOX":
            log_trade("EXIT", index_symbol, exit_prices, exit_premium, pnl, "Local Paper Trade Closed")
            logging.info(f"💰 Simulated PnL for this trade: ₹{pnl:.2f}")

            # --- TELEGRAM ALERT (PAPER) ---
            status_icon = "🤑" if pnl > 0 else "🩸"
            alert_msg = (
                f"🔴 <b>TRADE CLOSED (PAPER): {index_symbol}</b>\n"
                f"{status_icon} Realized PnL: <b>₹{pnl:.2f}</b>\n"
                f"Reason: <i>Risk/Target limits hit</i>"
            )
            send_telegram_alert(alert_msg)

        elif config.ENVIRONMENT == "LIVE":
            logging.critical("🔴 ROUTING EXIT ORDERS TO LIVE UPSTOX EXCHANGE...")

            configuration = upstox_client.Configuration()
            configuration.access_token = config.get_live_token()

            api_client = upstox_client.ApiClient(configuration)
            api_instance = upstox_client.OrderApiV3(api_client)

            # THE FIX: To close the basket, we fire the exact OPPOSITE transactions
            exit_orders = [
                {"token": legs['sell_ce'], "type": "BUY"},   # Closing the Short CE
                {"token": legs['sell_pe'], "type": "BUY"},   # Closing the Short PE
                {"token": legs['buy_ce'], "type": "SELL"},   # Closing the Long CE
                {"token": legs['buy_pe'], "type": "SELL"}    # Closing the Long PE
            ]

            success = True
            for order in exit_orders:
                body = upstox_client.PlaceOrderV3Request(
                    quantity=int(qty),
                    product="I",
                    validity="DAY",
                    price=0.0,
                    tag="iron_fly_exit",
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
                    logging.info(f"Live Exit Order Placed: {order['token']} - Status: success")
                except ApiException as e:
                    logging.error(f"Live Exit Order Rejected for {order['token']}: {e.body}")
                    success = False
                except Exception as e:
                    logging.error(f"System Error placing Live Exit Order for {order['token']}: {e}")
                    success = False

            log_trade("EXIT", index_symbol, exit_prices, exit_premium, pnl, "Live Exchange Exit")

            # --- TELEGRAM ALERT (LIVE) ---
            status_icon = "🤑" if pnl > 0 else "🩸"
            alert_msg = (
                f"🔴 <b>TRADE CLOSED (LIVE): {index_symbol}</b>\n"
                f"{status_icon} Realized PnL: <b>₹{pnl:.2f}</b>\n"
                f"Status: {'✅ All legs closed successfully' if success else '⚠️ WARNING: Some exit legs failed!'}"
            )
            send_telegram_alert(alert_msg)

    else:
        log_trade("EXIT", "UNKNOWN", {}, 0.0, 0.0, "Emergency Square Off")
        send_telegram_alert("⚠️ <b>EMERGENCY SQUARE OFF TRIGGERED!</b> Check AWS Terminal immediately.")

    state_manager.clear_state()
