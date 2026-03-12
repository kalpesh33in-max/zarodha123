import threading
import os
import schedule
import time
from datetime import datetime
from kiteconnect import KiteConnect
from env_config import API_KEY, API_SECRET, TELE_CHAT_ID
from telegram_utils import send_telegram_message
from auto_login import get_automated_token
from scanner import run_scanner

# Track if scanner is already running
scanner_thread = None

def start_scanner_if_needed():
    global scanner_thread
    if scanner_thread and scanner_thread.is_alive():
        print("Scanner already running.")
        return

    # Check for existing access token
    if not os.path.exists("access_token.txt"):
        print("No access token found. Cannot start scanner.")
        return

    try:
        with open("access_token.txt", "r") as f:
            access_token = f.read().strip()
        
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(access_token)
        
        # Start the scanner in a background thread
        scanner_thread = threading.Thread(target=run_scanner, args=(kite,))
        scanner_thread.daemon = True
        scanner_thread.start()
        print("Scanner started in background.")
        send_telegram_message("✅ *Market Scanner Started* - Monthly Expiry Mode Active.")
    except Exception as e:
        print(f"Error starting scanner: {e}")
        send_telegram_message(f"❌ *Failed to start scanner:* {e}")

def daily_job():
    # Only run Monday to Friday (0 = Monday, 4 = Friday)
    day_of_week = datetime.now().weekday()
    if day_of_week > 4: 
        print("Today is weekend. Skipping Zerodha login.")
        return

    print("Starting Morning Workflow at 07:00 AM...")
    
    # 1. Send Link for Mobile Login (Backup/Mobile preference)
    login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
    msg = (f"🌅 *Good Morning!* 🌅\n"
           f"Starting Zerodha Auto-Login Workflow.\n\n"
           f"📲 *Mobile Login Link (Backup):*\n{login_url}\n\n"
           f"⏳ *Status:* Fully Automated Login starting now...")
    send_telegram_message(msg)

    # 2. Fully Automated Login (Selenium + TOTP)
    try:
        access_token = get_automated_token()
        if access_token:
            send_telegram_message(f"✅ *Zerodha Login Successful!*\nAccess Token generated. Scanner starting now...")
            start_scanner_if_needed()
        else:
            send_telegram_message(f"❌ *Auto-Login Failed.* Please use the mobile link above.")
    except Exception as e:
        send_telegram_message(f"❌ *Auto-Login Error:* {str(e)}\nPlease use the mobile link above.")

# On script startup, check if we already have a valid token from today
start_scanner_if_needed()

# Schedule for 07:00 AM
schedule.every().day.at("07:00").do(daily_job)

print("Zerodha Automated Scheduler & Scanner Starter active.")

while True:
    try:
        schedule.run_pending()
        time.sleep(60)
    except Exception as e:
        print(f"Scheduler Error: {e}. Restarting...")
        time.sleep(10)
