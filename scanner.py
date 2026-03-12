import pandas as pd
import time
from heatmap_engine import calculate_heatmap
from telegram_utils import send_telegram_message
from env_config import TELE_CHAT_ID_BN, TELE_CHAT_ID_STOCKS, TELE_TOKEN_BN, TELE_TOKEN_STOCKS

def run_scanner(kite):

    print("Scanner started. Sending initial status to Telegram...")
    send_telegram_message("Kite Scanner Started Successfully!")

    while True:

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

        # Wait for 1 minute (60 seconds) to catch bursts more accurately
        time.sleep(60)
