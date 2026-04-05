import pandas as pd
import time
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message

def run_scanner(kite):

    print("Scanner started. Sending initial status to Telegram...")
    send_telegram_message("Kite Scanner Started Successfully!")

    while True:

        try:
            score, report = calculate_heatmap(kite)
            
            final_message = report

            print(final_message)
            send_telegram_message(final_message)

        except Exception as e:
            print(f"Error in scanner loop: {e}")
            send_telegram_message(f"Scanner Error: {e}")

        # Wait for 3 minutes (180 seconds)
        time.sleep(180)
