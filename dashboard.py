# dashboard.py
import streamlit as st
import pandas as pd
import json
import os
import time
import subprocess
import sys
import requests
import urllib.parse
import psutil  # <-- NEW IMPORT FOR PROCESS MANAGEMENT

st.set_page_config(page_title="Iron Butterfly V3", layout="wide")

# --- ABSOLUTE PATHING ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
PID_FILE = os.path.join(BASE_DIR, "engine_pid.txt")
STATE_FILE = os.path.join(BASE_DIR, "trade_state.json")
LOG_FILE_PATH = os.path.join(BASE_DIR, "bot.log")
CSV_LOG_FILE = os.path.join(BASE_DIR, "sandbox_trade_logs.csv")

# --- 1. SETTINGS & AUTH MANAGER ---
SETTINGS_FILE = "settings.json"

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_settings(new_settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(new_settings, f, indent=4)

settings = load_settings()

st.sidebar.header("⚙️ Bot Configuration")

# --- AUTO-LOADED CREDENTIALS ---
api_key = settings.get("API_KEY", "")
api_secret = settings.get("API_SECRET", "")
redirect_uri = settings.get("REDIRECT_URI", "https://127.0.0.1:5000/")

# --- LIVE TOKEN GENERATOR ---
st.sidebar.subheader("🟢 Live Authentication")
if not api_key or not api_secret:
    st.sidebar.error("⚠️ API_KEY and API_SECRET missing in settings.json")
else:
    with st.sidebar.expander("🔑 Generate Daily Live Token", expanded=False):
        auth_url = f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id={api_key}&redirect_uri={urllib.parse.quote(redirect_uri)}"
        st.markdown(f"**Step 1:** [Click here to Login]({auth_url})")
        
        auth_code = st.text_input("Step 2: Paste the 'code' from URL")
        
        if st.button("Generate & Save Live Token"):
            if not auth_code:
                st.error("Please paste the auth code.")
            else:
                url = 'https://api.upstox.com/v2/login/authorization/token'
                headers = {'accept': 'application/json', 'Api-Version': '2.0', 'Content-Type': 'application/x-www-form-urlencoded'}
                data = {'code': auth_code, 'client_id': api_key, 'client_secret': api_secret, 'redirect_uri': redirect_uri, 'grant_type': 'authorization_code'}
                
                try:
                    response = requests.post(url, headers=headers, data=data)
                    response_data = response.json()
                    if 'access_token' in response_data:
                        settings['LIVE_ACCESS_TOKEN'] = response_data['access_token']
                        save_settings(settings)
                        st.success("✅ Live Token Saved!")
                    else:
                        st.error(f"Failed: {response_data}")
                except Exception as e:
                    st.error(f"Error: {e}")

# --- SANDBOX TOKEN MANAGER ---
st.sidebar.subheader("🟡 Sandbox Authentication")
new_sandbox_token = st.sidebar.text_input("30-Day Sandbox Token", value=settings.get("SANDBOX_ACCESS_TOKEN", ""), type="password")
if st.sidebar.button("💾 Save Sandbox Token"):
    settings["SANDBOX_ACCESS_TOKEN"] = new_sandbox_token
    save_settings(settings)
    st.sidebar.success("Sandbox Token Saved!")

# --- TRADING PARAMETERS ---
st.sidebar.subheader("📊 Trading Parameters")
with st.sidebar.form("config_form"):
    env_mode = st.selectbox("Environment", ["SANDBOX", "LIVE"], index=0 if settings.get("ENVIRONMENT") == "SANDBOX" else 1)
    
    nifty_exp = st.text_input("Next Nifty Expiry (YYYY-MM-DD)", value=settings.get("NIFTY_EXPIRY", ""))
    sensex_exp = st.text_input("Next Sensex Expiry (YYYY-MM-DD)", value=settings.get("SENSEX_EXPIRY", ""))
    
    nifty_qty = st.number_input("Nifty Qty (Multiples of 75)", value=settings.get("NIFTY_LOT_SIZE", 75), step=75)
    sensex_qty = st.number_input("Sensex Qty (Multiples of 20)", value=settings.get("SENSEX_LOT_SIZE", 20), step=20)
    
    if st.form_submit_button("💾 Save Parameters"):
        settings["ENVIRONMENT"] = env_mode
        settings["NIFTY_EXPIRY"] = nifty_exp
        settings["SENSEX_EXPIRY"] = sensex_exp
        settings["NIFTY_LOT_SIZE"] = nifty_qty
        settings["SENSEX_LOT_SIZE"] = sensex_qty
        save_settings(settings)
        st.sidebar.success("Parameters Saved!")

# --- 2. ENGINE CONTROL (UPGRADED) ---
st.sidebar.markdown("---")
st.sidebar.header("🚀 Engine Control")

PID_FILE = "engine_pid.txt"
col_start, col_stop = st.sidebar.columns(2)

with col_start:
    if st.button("▶️ Start"):
        if not settings.get("LIVE_ACCESS_TOKEN"):
            st.error("Missing Live Token!")
        else:
            is_running = False
            if os.path.exists(PID_FILE):
                with open(PID_FILE, "r") as f:
                    old_pid = int(f.read().strip())
                if psutil.pid_exists(old_pid):
                    is_running = True

            if is_running:
                st.warning("Engine is already running!")
            else:
                # Launch silently and force ALL system errors into the log file
                log_file = open(LOG_FILE_PATH, "a")
                process = subprocess.Popen(
                    [sys.executable, "main.py"], 
                    cwd=BASE_DIR, 
                    stdout=log_file, 
                    stderr=subprocess.STDOUT
                )
                with open(PID_FILE, "w") as f:
                    f.write(str(process.pid))
                st.success("Engine Started! Check logs for startup status.")

	   


with col_stop:
    if st.button("⏹️ Stop"):
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                try:
                    pid = int(f.read().strip())
                    if psutil.pid_exists(pid):
                        p = psutil.Process(pid)
                        p.terminate()  # Gracefully shutdown the bot
                        p.wait()
                        st.success("Engine Stopped!")
                    else:
                        st.warning("Engine was not running.")
                except Exception as e:
                    st.error(f"Error stopping engine: {e}")
            os.remove(PID_FILE)
        else:
            st.warning("No running engine found.")

st.sidebar.caption("You can now safely control the bot entirely from this UI.")

# --- 3. MAIN DASHBOARD ---
st.title("🦅 Iron Butterfly Command Center")

# Carry Forward Toggle
btst_mode = st.toggle("🌙 Enable BTST (Carry Forward overnight)", value=False, help="If OFF, bot will auto-square off at 3:15 PM.")
if btst_mode:
    with open("btst_flag.txt", "w") as f: f.write("TRUE")
else:
    if os.path.exists("btst_flag.txt"): os.remove("btst_flag.txt")

col_status, col_logs = st.columns([1, 1.5])



with col_status:
    st.subheader("📡 Live System Status")
    state_file = "trade_state.json"
    live_file = "live_prices.json"

    if os.path.exists(state_file):
        with open(state_file, "r") as f:
            state = json.load(f)
            
        if state.get("active"):
            st.success(f"🟢 ACTIVE TRADE: {state['index_symbol']} | Qty: {state.get('quantity', 'N/A')}")
            
            if os.path.exists(live_file):
                with open(live_file, "r") as lf:
                    live_ticks = json.load(lf)
                
                legs = state['legs']
                entries = state['entry_prices']
                
                # Safely get live prices (defaults to entry if cache is just booting up)
                live_sell_pe = live_ticks.get(legs['sell_pe'], {}).get('ltp', entries['sell_pe'])
                live_sell_ce = live_ticks.get(legs['sell_ce'], {}).get('ltp', entries['sell_ce'])
                live_buy_pe = live_ticks.get(legs['buy_pe'], {}).get('ltp', entries['buy_pe'])
                live_buy_ce = live_ticks.get(legs['buy_ce'], {}).get('ltp', entries['buy_ce'])
                
                strikes_data = state.get('strikes', {})
                # --- NEW CLEAN TABLE FORMAT ---
                st.markdown("### Live Position Tracker")
                
                
                table_data = {
                    "Leg Type": ["🔴 SELL PE", "🔴 SELL CE", "🟢 BUY PE", "🟢 BUY CE"],
                    "Strike Price": [
                        strikes_data.get('sell_pe', 'N/A'),
                        strikes_data.get('sell_ce', 'N/A'),
                        strikes_data.get('buy_pe', 'N/A'),
                        strikes_data.get('buy_ce', 'N/A')
                    ],
                    "Trade Price": [f"₹{entries['sell_pe']:.2f}", f"₹{entries['sell_ce']:.2f}", f"₹{entries['buy_pe']:.2f}", f"₹{entries['buy_ce']:.2f}"],
                    "Current Price": [f"₹{live_sell_pe:.2f}", f"₹{live_sell_ce:.2f}", f"₹{live_buy_pe:.2f}", f"₹{live_buy_ce:.2f}"],
                    "PnL / Point": [
                        (entries['sell_pe'] - live_sell_pe), 
                        (entries['sell_ce'] - live_sell_ce), 
                        (live_buy_pe - entries['buy_pe']),   
                        (live_buy_ce - entries['buy_ce'])    
                    ]
                }




                df_live = pd.DataFrame(table_data)
                
                # Color the PnL column (Green for positive, Red for negative)
                def color_pnl(val):
                    color = '#00ff00' if val > 0 else '#ff0000' if val < 0 else 'white'
                    return f'color: {color}'
                
                st.dataframe(df_live.style.applymap(color_pnl, subset=['PnL / Point']), hide_index=True, width='stretch')
                
                # Total Premium Tracker
                entry_net = (entries['sell_ce'] + entries['sell_pe']) - (entries['buy_ce'] + entries['buy_pe'])
                live_net = (live_sell_ce + live_sell_pe) - (live_buy_ce + live_buy_pe)
                
                st.markdown("---")
                st.metric("Net Premium (Credit)", f"₹{live_net:.2f}", delta=f"₹{live_net - entry_net:.2f}", delta_color="inverse")

        else:
            st.warning("🟡 SYSTEM IDLE: Waiting for schedule.")
    else:
        st.warning("🟡 SYSTEM IDLE: Waiting for schedule.")


with col_logs:
    st.subheader("🖥️ Live Engine Logs")
    log_file_path = LOG_FILE_PATH
    if os.path.exists(log_file_path):
        with open(log_file_path, "r") as file:
            lines = file.readlines()
            last_lines = lines[-15:] if len(lines) > 15 else lines
            log_text = "".join(last_lines)
            st.code(log_text, language="text")
    else:
        st.code("No engine logs found. Start the engine.", language="text")

st.markdown("---")

# --- 4. TRADE LEDGER & PNL ---
st.subheader("📜 Trade Ledger & Performance")
log_file = CSV_LOG_FILE

if os.path.exists(log_file):
    df = pd.read_csv(log_file)
    def highlight_exits(row):
        return ['background-color: #2b2b2b' if row['Action'] == 'EXIT' else '' for _ in row]
    st.dataframe(df.style.apply(highlight_exits, axis=1), width='stretch')

    if "PnL" in df.columns:
        total_pnl = df['PnL'].sum()
        color = "normal" if total_pnl >= 0 else "inverse"
        st.metric(label="Total Realized PnL", value=f"₹ {total_pnl:.2f}", delta=f"₹ {total_pnl:.2f}", delta_color=color)
else:
    st.info("No trades logged yet.")

# Auto-refresh the dashboard every 3 seconds
time.sleep(3)
st.rerun()
