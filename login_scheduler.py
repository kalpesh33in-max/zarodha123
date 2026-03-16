import threading
import os
import schedule
import time

from datetime import datetime
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect
from env_config import API_KEY, API_SECRET
from telegram_utils import send_telegram_message
from auto_login import get_automated_token
from scanner import run_scanner

IST = ZoneInfo("Asia/Kolkata")

scanner_thread = None
stop_event = threading.Event()


def start_scanner_if_needed():
    global scanner_thread, stop_event

    now = datetime.now(IST)

    if now.weekday() > 4:
        print("Today is weekend. Skipping scanner start.")
        return

    if scanner_thread and scanner_thread.is_alive():
        print("Scanner already running.")
        return

    if not os.path.exists("access_token.txt"):
        print("No access token found. Waiting for automated login.")
        return

    try:
        with open("access_token.txt", "r") as f:
            access_token = f.read().strip()

        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(access_token)

        stop_event.clear()

        scanner_thread = threading.Thread(
            target=run_scanner,
            args=(kite, stop_event)
        )

        scanner_thread.daemon = True
        scanner_thread.start()

        print("Scanner thread launched successfully.")

    except Exception as e:
        print(f"Error starting scanner: {e}")
        send_telegram_message(f"❌ Failed to start scanner: {e}")


def stop_scanner():
    global stop_event
    print("Stopping scanner due to end of trading hours...")
    stop_event.set()


def morning_login():

    now = datetime.now(IST)

    if now.weekday() > 4:
        return

    print("Starting Morning Workflow at 08:30 AM...")

    send_telegram_message(
        "🌅 *Good Morning!* Zerodha Auto Login starting..."
    )

    try:

        access_token = get_automated_token()

        if access_token:
            start_scanner_if_needed()
        else:
            send_telegram_message("❌ Auto Login Failed")

    except Exception as e:
        send_telegram_message(f"❌ Auto Login Error: {str(e)}")


start_scanner_if_needed()

schedule.every().monday.to().friday.at("08:30").do(morning_login)

schedule.every().monday.to().friday.at("15:30").do(stop_scanner)

print("Zerodha Automated Scheduler active (Mon-Fri).")

while True:

    try:
        schedule.run_pending()
        time.sleep(1)

    except Exception as e:
        print(f"Scheduler Error: {e}")
        time.sleep(10)
