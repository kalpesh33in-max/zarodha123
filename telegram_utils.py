import requests
from env_config import TELE_TOKEN, TELE_CHAT_ID

def send_telegram_message(message):

    if not TELE_TOKEN or not TELE_CHAT_ID:

        print("Telegram configuration missing!")
        return

    url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELE_CHAT_ID,
        "text": message
    }

    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return None
