import requests
from env_config import TELE_TOKEN

def send_telegram_message(message, chat_id=None, token=None):

    target_token = token if token else TELE_TOKEN
    if not target_token:
        print("Telegram token missing!")
        return

    if not chat_id:
        print("Chat ID missing!")
        return

    url = f"https://api.telegram.org/bot{target_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message
    }

    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Telegram Error: {e}")
        return None
