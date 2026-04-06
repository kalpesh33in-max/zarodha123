import time
from datetime import datetime
from zoneinfo import ZoneInfo
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import *

IST = ZoneInfo("Asia/Kolkata")

def run_scanner(kite, stop_event=None):
    last_report_time = 0 
    
    print("Scanner session initialized.")
    # This fulfills your request for the "Scanner Start" message
    send_telegram_message("✅ *Kite Scanner Login Successful!*\nStatus: Active\nMonth: April Contracts")

    while stop_event is None or not stop_event.is_set():
        now = datetime.now(IST)
        
        # Market Hours check (Mon-Fri, 09:15 - 15:30)
        if (9 <= now.hour <= 15) and now.weekday() <= 4:
            if now.hour == 9 and now.minute < 15:
                time.sleep(10)
                continue
                
            try:
                # Unpacks all 5 return values exactly as your March logic did
                score, report, bn_al, st_al, vel_al = calculate_heatmap(kite)

                # Send Report every 3 Minutes
                if time.time() - last_report_time >= 180:
                    status = "🚀 BULLISH" if score > 20 else "📉 BEARISH" if score < -20 else "⚖️ NEUTRAL"
                    send_telegram_message(f"{report}\n\n⚖️ *SENTIMENT*: `{score:.2f}`\n📍 *STATUS*: {status}")
                    last_report_time = time.time()

                # Dispatching Alerts to their specific channels
                for a in bn_al: send_telegram_message(a, TELE_CHAT_ID_BN, TELE_TOKEN_BN)
                for a in st_al: send_telegram_message(a, TELE_CHAT_ID_STOCKS, TELE_TOKEN_STOCKS)
                for a in vel_al: send_telegram_message(a, TELE_CHAT_ID_VELOCITY, TELE_TOKEN_VELOCITY)

            except Exception as e:
                print(f"Error: {e}")
        
        time.sleep(5)
