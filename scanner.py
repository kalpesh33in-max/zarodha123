import pandas as pd
import time
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import TELE_CHAT_ID_BN, TELE_CHAT_ID_STOCKS, TELE_TOKEN_BN, TELE_TOKEN_STOCKS

from datetime import datetime

def run_scanner(kite, stop_event=None):

    print("Scanner session initialized. Sending status to Telegram...")
    send_telegram_message("✅ *Kite Scanner Login Successful!* Waiting for market hours (09:00 AM) to send reports...")

    while stop_event is None or not stop_event.is_set():
        
        # Check current time for Market Hours (09:00 to 15:30)
        now = datetime.now()
        now_time = now.time()
        start_time = datetime.strptime("09:00", "%H:%M").time()
        end_time = datetime.strptime("15:30", "%H:%M").time()
        
        # Only scan and send alerts if within market hours and it's a weekday
        if start_time <= now_time <= end_time and now.weekday() <= 4:
            try:
                score, report, bn_alerts, stock_alerts = calculate_heatmap(kite)
                
                # 1. Send General Report (uses Main Bot Token)
                final_message = report + f"\n⚖️ *SENTIMENT SCORE*: {score:.2f}\n"

                if score > 30:
                    final_message += "🚀 *STATUS: STRONG BULLISH*"
                elif score < -30:
                    final_message += "📉 *STATUS: STRONG BEARISH*"
                else:
                    final_message += "⚖️ *STATUS: SIDEWAYS*"

                print("Sending General Report...")
                send_telegram_message(final_message)

                # 2. Send Bank Nifty Option Alerts to BN Bot
                if bn_alerts:
                    print(f"Sending {len(bn_alerts)} Bank Nifty Alerts...")
                    bn_msg = "🏛 *BANK NIFTY OPTION ALERTS*\n" + "\n---\n".join(bn_alerts)
                    send_telegram_message(bn_msg, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)

                # 3. Send Bank Stock Option Alerts to Stocks Bot
                if stock_alerts:
                    print(f"Sending {len(stock_alerts)} Bank Stock Alerts...")
                    stock_msg = "🏦 *BANK STOCK OPTION ALERTS*\n" + "\n---\n".join(stock_alerts)
                    send_telegram_message(stock_msg, chat_id=TELE_CHAT_ID_STOCKS, token=TELE_TOKEN_STOCKS)

            except Exception as e:
                print(f"Error in scanner loop: {e}")
                send_telegram_message(f"Scanner Error: {e}")
        else:
            # Silent Mode: Just print to local console, don't send to Telegram
            print(f"[{now.strftime('%H:%M:%S')}] Outside market hours. Scanner is silent.")

        # Wait for 30 seconds or until stop_event is set
        if stop_event:
            if stop_event.wait(30):
                break
        else:
            time.sleep(30)

    print("Scanner loop stopped.")
    send_telegram_message("🛑 *Market Scanner Process Ended.*")
