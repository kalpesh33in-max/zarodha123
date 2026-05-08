import pandas as pd
import time
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import TELE_CHAT_ID_BN, TELE_CHAT_ID_STOCKS, TELE_TOKEN_BN, TELE_TOKEN_STOCKS, TELE_TOKEN_VELOCITY, TELE_CHAT_ID_VELOCITY

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SCAN_INTERVAL_SECONDS = 5
SILENT_LOG_INTERVAL_SECONDS = 300


def run_scanner(kite, stop_event=None):

    print("Scanner session initialized. Sending status to Telegram...")
    send_telegram_message("✅ *Kite Scanner Login Successful!* Waiting for market hours (09:00 AM) to send reports...")

    last_silent_log_time = 0.0

    while stop_event is None or not stop_event.is_set():

        # Use IST timezone
        now = datetime.now(IST)
        now_time = now.time()
        start_time = datetime.strptime("09:00", "%H:%M").time()
        end_time = datetime.strptime("15:30", "%H:%M").time()

        if start_time <= now_time <= end_time and now.weekday() <= 4:

            try:
                score, report, bn_alerts, stock_alerts, gap_alerts = calculate_heatmap(kite)

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

                if gap_alerts:
                    print(f"Sending {len(gap_alerts)} May Future Gap Reports...")
                    for alert in gap_alerts:
                        send_telegram_message(alert)

            except Exception as e:
                print(f"Error in scanner loop: {e}")
                send_telegram_message(f"Scanner Error: {e}")

        else:
            # Avoid spamming logs when the market is closed.
            now_ts = now.timestamp()
            if now_ts - last_silent_log_time >= SILENT_LOG_INTERVAL_SECONDS:
                print(f"[{now.strftime('%H:%M:%S')}] Outside trading session (weekend/market closed). Scanner is silent.")
                last_silent_log_time = now_ts

        if stop_event:
            if stop_event.wait(SCAN_INTERVAL_SECONDS):
                break
        else:
            time.sleep(SCAN_INTERVAL_SECONDS)

    print("Scanner loop stopped.")
    send_telegram_message("🛑 *Market Scanner Process Ended.*")
