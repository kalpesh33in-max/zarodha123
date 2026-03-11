import schedule
import time
from datetime import datetime
from kiteconnect import KiteConnect
from env_config import API_KEY, TELE_CHAT_ID
from telegram_utils import send_telegram_message
from auto_login import get_automated_token

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
            send_telegram_message(f"✅ *Zerodha Login Successful!*\nAccess Token generated and saved. Scanner will start at market open.")
        else:
            send_telegram_message(f"❌ *Auto-Login Failed.* Please use the mobile link above.")
    except Exception as e:
        send_telegram_message(f"❌ *Auto-Login Error:* {str(e)}\nPlease use the mobile link above.")

# Schedule for 07:00 AM
schedule.every().day.at("07:00").do(daily_job)

print("Zerodha 07:00 AM Automated Scheduler Started. Waiting for Mon-Fri...")

while True:
    try:
        schedule.run_pending()
        time.sleep(60)
    except Exception as e:
        print(f"Scheduler Error: {e}. Restarting...")
        time.sleep(10)
