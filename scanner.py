import time
from datetime import datetime
from zoneinfo import ZoneInfo
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import (
    TELE_CHAT_ID_BN, TELE_CHAT_ID_STOCKS, TELE_CHAT_ID_VELOCITY,
    TELE_TOKEN_BN, TELE_TOKEN_STOCKS, TELE_TOKEN_VELOCITY
)

IST = ZoneInfo("Asia/Kolkata")

def run_scanner(kite, stop_event=None):
    print("🚀 Scanner Initialized. Monitoring Market...")
    send_telegram_message("✅ *Scanner Live:* System is active and monitoring Bank Nifty.")

    while stop_event is None or not stop_event.is_set():
        now = datetime.now(IST)
        
        # Market Hours Check (09:15 to 15:30 IST, Mon-Fri)
        if 9 <= now.hour <= 15 and now.weekday() <= 4:
            if now.hour == 9 and now.minute < 15:
                time.sleep(30)
                continue
                
            try:
                score, report, bn_alerts, stock_alerts, velocity_alerts = calculate_heatmap(kite)

                # 1. Send General Sentiment Report
                send_telegram_message(report)

                # 2. Send Bank Nifty Specific Alerts
                for alert in bn_alerts:
                    send_telegram_message(alert, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)

                # 3. Send Individual Stock Alerts
                for alert in stock_alerts:
                    send_telegram_message(alert, chat_id=TELE_CHAT_ID_STOCKS, token=TELE_TOKEN_STOCKS)

                # 4. Send High-Priority Velocity Alerts
                for alert in velocity_alerts:
                    send_telegram_message(alert, chat_id=TELE_CHAT_ID_VELOCITY, token=TELE_TOKEN_VELOCITY)

            except Exception as e:
                print(f"❌ Loop Error: {e}")
                
        else:
            print(f"💤 Market Closed ({now.strftime('%H:%M')}). Sleeping...")
        
        # Wait 30 seconds before next scan
        if stop_event and stop_event.wait(30):
            break
        else:
            time.sleep(30)

    print("🛑 Scanner stopped.")
