import pandas as pd
import time
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import TELE_CHAT_ID_BN, TELE_CHAT_ID_STOCKS, TELE_TOKEN_BN, TELE_TOKEN_STOCKS, TELE_TOKEN_VELOCITY, TELE_CHAT_ID_VELOCITY

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def run_scanner(kite, stop_event=None):

    print("Scanner session initialized. Sending status to Telegram...")
    send_telegram_message("✅ *Kite Scanner Login Successful!* Waiting for market hours (09:00 AM) to send reports...")

    last_report_time = 0  # Track when the last general report was sent
    
    # Buffers to collect alerts during the 3-minute window
    buffered_bn_alerts = []
    buffered_stock_alerts = []
    buffered_velocity_alerts = []

    while stop_event is None or not stop_event.is_set():

        # Use IST timezone
        now = datetime.now(IST)
        now_time = now.time()
        current_timestamp = time.time()

        start_time = datetime.strptime("09:00", "%H:%M").time()
        end_time = datetime.strptime("15:30", "%H:%M").time()

        if start_time <= now_time <= end_time and now.weekday() <= 4:

            try:
                score, report, bn_alerts, stock_alerts, velocity_alerts = calculate_heatmap(kite)
                
                # Accumulate unique alerts into buffers
                if bn_alerts:
                    for a in bn_alerts:
                        if a not in buffered_bn_alerts: buffered_bn_alerts.append(a)
                
                if stock_alerts:
                    for a in stock_alerts:
                        if a not in buffered_stock_alerts: buffered_stock_alerts.append(a)
                
                if velocity_alerts:
                    for a in velocity_alerts:
                        if a not in buffered_velocity_alerts: buffered_velocity_alerts.append(a)

                # Send EVERYTHING as ONE message only every 3 minutes (180 seconds)
                if current_timestamp - last_report_time >= 180:
                    final_message = report + f"\n⚖️ *SENTIMENT SCORE*: {score:.2f}\n"

                    if score > 30:
                        final_message += "🚀 *STATUS: STRONG BULLISH*"
                    elif score < -30:
                        final_message += "📉 *STATUS: STRONG BEARISH*"
                    else:
                        final_message += "⚖️ *STATUS: SIDEWAYS*"
                    
                    # Add buffered alerts to the same message if they exist
                    if buffered_bn_alerts or buffered_stock_alerts or buffered_velocity_alerts:
                        final_message += "\n\n🔔 *LATEST ALERTS:*\n"
                        
                        for a in buffered_bn_alerts: final_message += f"• {a}\n"
                        for a in buffered_stock_alerts: final_message += f"• {a}\n"
                        for a in buffered_velocity_alerts: final_message += f"• {a}\n"

                    print("Sending Combined 3-Minute Report...")
                    send_telegram_message(final_message, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)
                    
                    # Reset timer and clear buffers
                    last_report_time = current_timestamp
                    buffered_bn_alerts.clear()
                    buffered_stock_alerts.clear()
                    buffered_velocity_alerts.clear()

            except Exception as e:
                print(f"Error in scanner loop: {e}")
                # We still want to see errors, but we won't flood the channel
                # send_telegram_message(f"Scanner Error: {e}")

        else:
            print(f"[{now.strftime('%H:%M:%S')}] Outside market hours. Scanner is silent.")

        if stop_event:
            if stop_event.wait(5):
                break
        else:
            time.sleep(5)

    print("Scanner loop stopped.")
    send_telegram_message("🛑 *Market Scanner Process Ended.*")
