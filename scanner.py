import pandas as pd
import time
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import TELE_CHAT_ID_BN, TELE_CHAT_ID_STOCKS, TELE_TOKEN_BN, TELE_TOKEN_STOCKS

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def run_scanner(kite, stop_event=None):

    print("Scanner session initialized. Sending status to Telegram...")
    send_telegram_message("✅ *Kite Scanner Login Successful!* Waiting for market hours (09:00 AM) to send reports...")

    while stop_event is None or not stop_event.is_set():

        # Use IST timezone
        now = datetime.now(IST)
        now_time = now.time()

        start_time = datetime.strptime("09:00", "%H:%M").time()
        end_time = datetime.strptime("15:30", "%H:%M").time()

        if start_time <= now_time <= end_time and now.weekday() <= 4:

            try:
                score, report, bn_alerts, stock_alerts = calculate_heatmap(kite)

                final_message = report + f"\n⚖️ *SENTIMENT SCORE*: {score:.2f}\n"

                if score > 30:
                    final_message += "🚀 *STATUS: STRONG BULLISH*"
                elif score < -30:
                    final_message += "📉 *STATUS: STRONG BEARISH*"
                else:
                    final_message += "⚖️ *STATUS: SIDEWAYS*"

                print("Sending General Report...")
                send_telegram_message(final_message)

                if bn_alerts:
                    print(f"Sending {len(bn_alerts)} Bank Nifty Alerts...")
                    for alert in bn_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)

                if stock_alerts:
                    print(f"Sending {len(stock_alerts)} Bank Stock Alerts...")
                    for alert in stock_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_STOCKS, token=TELE_TOKEN_STOCKS)

            except Exception as e:
                print(f"Error in scanner loop: {e}")
                send_telegram_message(f"Scanner Error: {e}")

        else:
            print(f"[{now.strftime('%H:%M:%S')}] Outside market hours. Scanner is silent.")

        if stop_event:
            if stop_event.wait(30):
                break
        else:
            time.sleep(30)

    print("Scanner loop stopped.")
    send_telegram_message("🛑 *Market Scanner Process Ended.*")
