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

def log(msg):
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {msg}")
    sys.stdout.flush()

def run_scanner(kite, stop_event=None):
    log("🚀 Scanner Initialized. Market Hours: 09:00 - 15:30 IST.")
    send_telegram_message("✅ *Kite Scanner Online!*")

    last_report_time = 0

    while stop_event is None or not stop_event.is_set():
        now = datetime.now(IST)
        if 9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 30):
            if now.weekday() <= 4:
                try:
                    # FIXED: Unpacking 5 values from heatmap_engine
                    score, report, bn_alerts, stock_alerts, velocity_alerts = calculate_heatmap(kite)

                    curr_ts = time.time()
                    if curr_ts - last_report_time >= 180:
                        status = "STRONG BULLISH" if score > 30 else "STRONG BEARISH" if score < -30 else "SIDEWAYS"
                        final_msg = f"{report}\n\n⚖️ *SCORE*: {score:.2f}\n📢 *STATUS*: {status}"
                        send_telegram_message(final_msg)
                        last_report_time = curr_ts
                        log("Sent General Report.")

                    for alert in bn_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)
                    for alert in stock_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_STOCKS, token=TELE_TOKEN_STOCKS)
                    for alert in velocity_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_VELOCITY, token=TELE_TOKEN_VELOCITY)

                except Exception as e:
                    log(f"CRITICAL ERROR: {e}")
                    time.sleep(10)
            else:
                log("Weekend. Scanner sleeping...")
                time.sleep(3600)
        else:
            log("Outside market hours.")
            time.sleep(60)
        
        time.sleep(5)
