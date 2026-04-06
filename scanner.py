import time
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import (
    TELE_CHAT_ID_BN, 
    TELE_CHAT_ID_STOCKS, 
    TELE_CHAT_ID_VELOCITY,
    TELE_TOKEN_BN, 
    TELE_TOKEN_STOCKS, 
    TELE_TOKEN_VELOCITY
)

# Set Timezone to IST
IST = ZoneInfo("Asia/Kolkata")

def log(msg):
    """Prints to Railway console with immediate flush."""
    timestamp = datetime.now(IST).strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}", flush=True)

def run_scanner(kite, stop_event=None):
    log("🚀 Scanner Process Started.")
    send_telegram_message("✅ *Kite Scanner Online!*\nMonitoring Market Hours (09:15 - 15:30)")

    last_report_time = 0 

    while stop_event is None or not stop_event.is_set():
        now = datetime.now(IST)
        
        # Define Market Hours
        start_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
        end_time = now.replace(hour=15, minute=30, second=0, microsecond=0)

        # Check if currently in Market Hours (Monday-Friday)
        if start_time <= now <= end_time and now.weekday() <= 4:
            try:
                # 1. Fetch data from Heatmap Engine
                # Expected return: total_score, report_text, bn_alerts, stock_alerts, velocity_alerts
                score, report, bn_alerts, stock_alerts, velocity_alerts = calculate_heatmap(kite)

                current_ts = time.time()

                # 2. Send General Heatmap Report (Every 3 Minutes)
                if current_ts - last_report_time >= 180:
                    # Determine Status based on Score
                    if score > 35:
                        status = "🚀 *STRONG BULLISH*"
                    elif score > 10:
                        status = "🟢 *BULLISH*"
                    elif score < -35:
                        status = "📉 *STRONG BEARISH*"
                    elif score < -10:
                        status = "🔴 *BEARISH*"
                    else:
                        status = "⚖️ *SIDEWAYS*"

                    # Construct final message matching your screenshot format
                    final_report = (
                        f"{report}\n\n"
                        f"⚖️ *SENTIMENT SCORE*: `{score:.2f}`\n"
                        f"📍 *STATUS*: {status}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"⏰ *REFRESHED*: {now.strftime('%H:%M:%S')}"
                    )

                    log("Dispatching General Heatmap Report...")
                    # General report usually goes to the main Bank Nifty channel
                    send_telegram_message(final_report, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)
                    last_report_time = current_ts

                # 3. Send Bank Nifty Burst Alerts
                if bn_alerts:
                    for alert in bn_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)
                    log(f"Sent {len(bn_alerts)} BankNifty Alerts.")

                # 4. Send Stock Specific Alerts
                if stock_alerts:
                    for alert in stock_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_STOCKS, token=TELE_TOKEN_STOCKS)
                    log(f"Sent {len(stock_alerts)} Stock Alerts.")

                # 5. Send High Velocity Alerts
                if velocity_alerts:
                    for alert in velocity_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_VELOCITY, token=TELE_TOKEN_VELOCITY)
                    log(f"Sent {len(velocity_alerts)} Velocity Alerts.")

            except Exception as e:
                log(f"CRITICAL ERROR in Scanner Loop: {e}")
                # Optional: send error to telegram for remote monitoring
                # send_telegram_message(f"⚠️ *Scanner Error*: {str(e)}")
                time.sleep(10) # Wait before retrying on crash
        
        else:
            # Sleep more during off-hours to save resources
            if now.minute % 30 == 0 and now.second < 10:
                log("Market Closed / Weekend. Scanner in standby mode.")
            time.sleep(30)

        # Main loop throttle
        time.sleep(5)

    log("🛑 Scanner service has been stopped.")
