import pandas as pd
import time
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import TELE_CHAT_ID_BN, TELE_CHAT_ID_STOCKS, TELE_TOKEN_BN, TELE_TOKEN_STOCKS, TELE_TOKEN_VELOCITY, TELE_CHAT_ID_VELOCITY

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
REPORT_INTERVAL_SECONDS = 300
SCAN_INTERVAL_SECONDS = 2


def run_scanner(kite, stop_event=None):

    print("Scanner session initialized. Sending status to Telegram...")
    send_telegram_message("✅ *Kite Scanner Login Successful!* Waiting for market hours (09:00 AM) to send reports...")

    last_report_time = 0  # Track when the last general report was sent

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

                # Send general report every 5 minutes.
                if current_timestamp - last_report_time >= REPORT_INTERVAL_SECONDS:
                    print("Sending General Report...")
                    send_telegram_message(report)
                    last_report_time = current_timestamp

                # Alerts are checked on the scanner loop cadence.
                # ALL Alerts now go to the BANK NIFTY channel as requested
                if bn_alerts:
                    print(f"Sending {len(bn_alerts)} Bank Nifty Alerts...")
                    for alert in bn_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)

                if stock_alerts:
                    print(f"Sending {len(stock_alerts)} Bank Stock Alerts...")
                    for alert in stock_alerts:
                        send_telegram_message(alert, chat_id=TELE_CHAT_ID_BN, token=TELE_TOKEN_BN)

                if velocity_alerts:
                    print(f"Sending {len(velocity_alerts)} Velocity/Smart Money Alerts...")
                    for alert in velocity_alerts:
                        # Sent to Main Channel (TELE_CHAT_ID) as requested
                        send_telegram_message(alert)

            except Exception as e:
                print(f"Error in scanner loop: {e}")
                send_telegram_message(f"Scanner Error: {e}")

        else:
            print(f"[{now.strftime('%H:%M:%S')}] Outside trading session (weekend/market closed). Scanner is silent.")

        if stop_event:
            if stop_event.wait(SCAN_INTERVAL_SECONDS):
                break
        else:
            time.sleep(SCAN_INTERVAL_SECONDS)

    print("Scanner loop stopped.")
    send_telegram_message("🛑 *Market Scanner Process Ended.*")
