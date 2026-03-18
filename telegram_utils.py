import requests
from env_config import TELE_TOKEN, TELE_CHAT_ID

def send_telegram_message(message, chat_id=None, token=None):

    target_token = token if token else TELE_TOKEN
    if not target_token:
        print("Telegram token missing!")
        return

    target_id = chat_id if chat_id else TELE_CHAT_ID
    if not target_id or target_id == "YOUR_CHAT_ID":
        print(f"Target Chat ID missing: {target_id}")
        return

    url = f"https://api.telegram.org/bot{target_token}/sendMessage"
    payload = {
        "chat_id": target_id,
        "text": message
    }

    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return None
