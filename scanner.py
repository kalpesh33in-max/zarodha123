import pandas as pd  # Fixed: Changed from 'import pd'
import time
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import (
    TELE_CHAT_ID_BN, TELE_CHAT_ID_STOCKS, TELE_TOKEN_BN, 
    TELE_TOKEN_STOCKS, TELE_TOKEN_VELOCITY, TELE_CHAT_ID_VELOCITY
)

from datetime import datetime
from zoneinfo import ZoneInfo

# Set Timezone to IST
IST = ZoneInfo("Asia/Kolkata")

def run_scanner(kite, stop_event=None):
    print("Scanner session initialized. Sending status to Telegram...")
    send_telegram_message("✅ *Kite Scanner Login Successful!* Waiting for market hours (09:00 AM) to send reports...")

    last_report_time = 0  # Track when the last general report was sent

    while stop_event is None or not stop_event.is_set():
        # Current time in IST
        now = datetime.now(IST)
        now_time = now.time()
        current_timestamp = time.time()

        # Define Market Hours (9:00 AM to 3:30 PM)
        start_time = datetime.strptime("09:00", "%H:%M").time()
        end_time = datetime.strptime("15:30", "%H:%M").time()

        # Only run during market hours on weekdays (Monday-Friday)
        if start_time <= now_time <= end_time and now.weekday() <= 4:
            try:
                # CRITICAL FIX: Pass the 'kite' object to the heatmap engine
                score, report, bn_alerts, stock_alerts, velocity_alerts = calculate_heatmap(kite)

                # Send General Heatmap Report every 3 minutes (180 seconds)
                if current_timestamp - last_report_time >= 180:
                    final_message = report
                    print("Sending General Heatmap Report...")
                    send_telegram_message(final_message)
                    last_report_time = current_timestamp

                # Process and send Bank Nifty specific alerts
                if bn_alerts:
                    print(f"Sending {len(bn_alerts)} Bank Nifty Alerts...")
                    for alert in bn_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)

                # Process and send Stock specific alerts
                if stock_alerts:
                    print(f"Sending {len(stock_alerts)} Bank Stock Alerts...")
                    for alert in stock_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_STOCKS, token=TELE_TOKEN_STOCKS)

                # Process and send Velocity/Burst alerts
                if velocity_alerts:
                    print(f"Sending {len(velocity_alerts)} Velocity Burst Alerts...")
                    for alert in velocity_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_VELOCITY, token=TELE_TOKEN_VELOCITY)

            except Exception as e:
                print(f"Error in scanner loop: {e}")
                # Optional: send_telegram_message(f"⚠️ Scanner Loop Error: {e}")
        
        else:
            # Logic for outside market hours
            print(f"[{now.strftime('%H:%M:%S')}] Outside market hours. Scanner is idling.")

        # Sleep logic to prevent CPU hitting 100%
        if stop_event:
            if stop_event.wait(5): 
                break
        else:
            time.sleep(5)

    print("Scanner loop stopped.")
    send_telegram_message("🛑 *Market Scanner Process Ended.*")
