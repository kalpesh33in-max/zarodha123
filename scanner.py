import time
from datetime import datetime
from zoneinfo import ZoneInfo
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import *

IST = ZoneInfo("Asia/Kolkata")

def run_scanner(kite, stop_event=None):
    last_report_time = 0
    print("Scanner Started...", flush=True)

    while stop_event is None or not stop_event.is_set():
        now = datetime.now(IST)
        
        # Market Hours check (09:00 - 15:30)
        if 9 <= now.hour <= 15 and now.weekday() <= 4:
            try:
                score, report, bn_alerts, st_alerts, vel_alerts = calculate_heatmap(kite)

                # Send Heatmap Report every 3 Minutes
                if time.time() - last_report_time >= 180:
                    status = "🚀 *STRONG BULLISH*" if score > 30 else "📉 *STRONG BEARISH*" if score < -30 else "⚖️ *SIDEWAYS*"
                    final_msg = f"{report}\n⚖️ *SENTIMENT SCORE*: {score:.2f}\n{status}"
                    send_telegram_message(final_msg)
                    last_report_time = time.time()

                # Dispatch Alerts to respective channels
                for a in bn_alerts: send_telegram_message(a, TELE_CHAT_ID_BN, TELE_TOKEN_BN)
                for a in st_alerts: send_telegram_message(a, TELE_CHAT_ID_STOCKS, TELE_TOKEN_STOCKS)
                for a in vel_alerts: send_telegram_message(a, TELE_CHAT_ID_VELOCITY, TELE_TOKEN_VELOCITY)

            except Exception as e:
                print(f"Loop Error: {e}", flush=True)
        
        time.sleep(5)
