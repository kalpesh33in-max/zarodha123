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
stop_event = threading.Event()

def start_scanner_if_needed():
    global scanner_thread, stop_event
    
    # Only run Monday to Friday
    day_of_week = datetime.now().weekday()
    if day_of_week > 4: 
        print("Today is weekend. Skipping scanner start.")
        return

    if scanner_thread and scanner_thread.is_alive():
        print("Scanner already running.")
        return

    # Check for existing access token
    if not os.path.exists("access_token.txt"):
        print("No access token found. Waiting for automated login.")
        return

    try:
        with open("access_token.txt", "r") as f:
            access_token = f.read().strip()
        
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(access_token)
        
        # Reset stop event and start the scanner in a background thread
        stop_event.clear()
        scanner_thread = threading.Thread(target=run_scanner, args=(kite, stop_event))
        scanner_thread.daemon = True
        scanner_thread.start()
        print("Scanner thread launched successfully.")
    except Exception as e:
        print(f"Error starting scanner: {e}")
        send_telegram_message(f"❌ *Failed to start scanner:* {e}")

def stop_scanner():
    global stop_event
    print("Stopping scanner due to end of trading hours...")
    stop_event.set()

def morning_login():
    # Only run Monday to Friday (0 = Monday, 4 = Friday)
    day_of_week = datetime.now().weekday()
    if day_of_week > 4: 
        return

    print("Starting Morning Workflow at 08:30 AM...")
    
    # 1. Send Link for Mobile Login (Backup/Mobile preference)
    login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
    msg = (f"🌅 *Good Morning!* 🌅\n"
           f"Starting Zerodha Auto-Login Workflow.\n\n"
           f"⏳ *Status:* Fully Automated Login starting now...")
    send_telegram_message(msg)

    # 2. Fully Automated Login (Selenium + TOTP)
    try:
        access_token = get_automated_token()
        if access_token:
            # login success leads to starting scanner immediately (which will be silent if before 9am)
            start_scanner_if_needed()
        else:
            send_telegram_message(f"❌ *Auto-Login Failed.* Please use the mobile link above.")
    except Exception as e:
        send_telegram_message(f"❌ *Auto-Login Error:* {str(e)}\nPlease use the mobile link above.")

# On script startup, check if we should start now (any time)
start_scanner_if_needed()

# Schedule for Morning Login (08:30 AM) - This will trigger the scanner start
schedule.every().monday.to().friday.at("08:30").do(morning_login)

# Schedule for Scanner Stop (03:30 PM)
schedule.every().monday.to().friday.at("15:30").do(stop_scanner)

print("Zerodha Automated Scheduler active (Mon-Fri). Reporting: 09:00 - 15:30.")

while True:
    try:
        schedule.run_pending()
        time.sleep(1)
    except Exception as e:
        print(f"Scheduler Error: {e}. Restarting...")
        time.sleep(10)
