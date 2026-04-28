# notifier.py
import requests
import config
import logging

def send_telegram_alert(message):
    """Sends a formatted message to your Telegram app."""
    if not getattr(config, 'TELEGRAM_BOT_TOKEN', None) or not getattr(config, 'TELEGRAM_CHAT_ID', None):
        return # Silently skip if Telegram isn't configured
    
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML" # Allows us to use bold <b> and italic <i> tags
    }
    
    try:
        # We use a short timeout so a network glitch doesn't freeze your trading bot
        requests.post(url, json=payload, timeout=3)
    except Exception as e:
        logging.error(f"Failed to send Telegram alert: {e}")
