# auth.py
import requests
import config
import logging

def get_daily_access_token(auth_code):
    """
    Exchanges the daily browser auth_code for a trading access_token.
    """
    url = 'https://api.upstox.com/v2/login/authorization/token'
    headers = {
        'accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    data = {
        'code': auth_code,
        'client_id': config.API_KEY,
        'client_secret': config.API_SECRET,
        'redirect_uri': config.REDIRECT_URI,
        'grant_type': 'authorization_code',
    }

    try:
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data['access_token']
        logging.info("Successfully generated daily access token.")
        return access_token
    except Exception as e:
        logging.error(f"Failed to get access token: {e}")
        return None
