import requests
from env_config import TELE_TOKEN, TELE_CHAT_ID

def send_telegram_message(message, chat_id=None, token=None):
    # Use provided params or fall back to defaults from env_config
    target_token = token if token else TELE_TOKEN
    target_id = chat_id if chat_id else TELE_CHAT_ID

    # Validation
    if not target_token:
        print("❌ Error: Telegram Token missing.")
        return
    if not target_id or target_id == "YOUR_CHAT_ID":
        print(f"❌ Error: Target Chat ID missing or invalid: {target_id}")
        return

    url = f"https://api.telegram.org/bot{target_token}/sendMessage"
    payload = {
        "chat_id": target_id,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        result = response.json()
        if not result.get("ok"):
            print(f"⚠️ Telegram API Error: {result.get('description')}")
        return result
    except Exception as e:
        print(f"❌ Telegram Connection Error: {e}")
        return None
