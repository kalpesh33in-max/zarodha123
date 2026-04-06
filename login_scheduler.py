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

IST = ZoneInfo("Asia/Kolkata")

def morning_login():
    now = datetime.now(IST)
    if now.weekday() > 4: return
    print("Starting Morning Workflow at 08:30 AM...")
    try:
        access_token = get_automated_token()
        if access_token:
            print("Token updated successfully via Morning Login.")
    except Exception as e:
        print(f"Auto Login Error: {e}")

schedule.every().monday.to().friday.at("08:30").do(morning_login)

print("Zerodha Automated Scheduler active (Mon-Fri).")

while True:
    try:
        schedule.run_pending()
        time.sleep(1)
    except Exception as e:
        print(f"Scheduler Error: {e}")
        time.sleep(10)
