import time
import sys
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
    print("Scanner session initialized. Waiting for market hours...", flush=True)
    send_telegram_message("✅ *Scanner Online!* Monitoring starts at 09:15 AM IST.")

    last_report_time = 0

    while stop_event is None or not stop_event.is_set():
        now = datetime.now(IST)
        # Market Hours: 09:15 to 15:30
        if (9 <= now.hour <= 15) and now.weekday() <= 4:
            try:
                # Unpacking fixed to match heatmap_engine
                score, report, bn_al, st_al, vel_al = calculate_heatmap(kite)

                curr_ts = time.time()
                if curr_ts - last_report_time >= 180:
                    status = "BULLISH" if score > 30 else "BEARISH" if score < -30 else "SIDEWAYS"
                    msg = f"{report}\n⚖️ *SCORE*: {score:.2f}\n📢 *STATUS*: {status}"
                    send_telegram_message(msg)
                    last_report_time = curr_ts
                    print(f"[{now.strftime('%H:%M:%S')}] General Report Sent.", flush=True)

                # Send individual alerts
                for a in bn_al: send_telegram_message(a, TELE_CHAT_ID_BN, TELE_TOKEN_BN)
                for a in st_al: send_telegram_message(a, TELE_CHAT_ID_STOCKS, TELE_TOKEN_STOCKS)
                for a in vel_al: send_telegram_message(a, TELE_CHAT_ID_VELOCITY, TELE_TOKEN_VELOCITY)

            except Exception as e:
                print(f"Scanner Loop Error: {e}", flush=True)
                time.sleep(10)
        else:
            if now.minute % 30 == 0: # Log status every 30 mins outside hours
                print(f"[{now.strftime('%H:%M:%S')}] Outside market hours.", flush=True)
            time.sleep(60)
        
        time.sleep(5)
